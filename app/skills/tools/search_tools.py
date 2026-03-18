from __future__ import annotations

import asyncio
import datetime
import logging
import time
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from ddgs import DDGS

from app.skills.registry import SkillRegistry
from app.tracing.context import get_current_trace

if TYPE_CHECKING:
    from app.config import Settings
    from app.llm.client import OllamaClient

logger = logging.getLogger(__name__)

MAX_RESULTS = 5

_EXTRACT_PROMPT = (
    "Extract the most relevant information from these web pages to answer the query.\n"
    "RULES:\n"
    "- Include EXACT data: dates, times, names, prices, numbers — never approximate\n"
    "- Preserve URLs for sources\n"
    "- If a page has no relevant info, skip it\n"
    "- Format as a clear, structured summary\n"
    "- Keep it concise — only information that directly answers the query\n"
)


def _perform_search(
    query: str, time_range: str | None = None, max_results: int = MAX_RESULTS
) -> list[dict]:
    """Search DuckDuckGo using the duckduckgo-search library."""
    results = DDGS().text(
        query,
        timelimit=time_range,
        max_results=max_results,
    )
    return results


def _format_snippets(results: list[dict]) -> str:
    """Format search results as numbered markdown list."""
    formatted = []
    for i, res in enumerate(results, 1):
        title = res.get("title", "No title")
        link = res.get("href", "#")
        body = res.get("body", "")
        formatted.append(f"{i}. [{title}]({link}): {body}")
    return "\n\n".join(formatted)


async def _llm_extract(
    query: str,
    pages: list[tuple[str, str]],
    ollama_client: OllamaClient,
    page_limit: int = 2500,
) -> str:
    """Use LLM to extract relevant information from fetched pages.

    Args:
        query: The original search query.
        pages: List of (url, text) tuples for successfully fetched pages.
        ollama_client: The Ollama client for LLM calls.
        page_limit: Max chars per page sent to the LLM.

    Returns:
        Extracted text summary from the LLM.
    """
    from app.models import ChatMessage

    # Build page content for the LLM
    page_sections = []
    for url, text in pages:
        truncated = text[:page_limit]
        page_sections.append(f"### Source: {url}\n{truncated}")
    pages_text = "\n\n---\n".join(page_sections)

    messages = [
        ChatMessage(role="system", content=_EXTRACT_PROMPT),
        ChatMessage(role="user", content=f"Query: {query}\n\n{pages_text}"),
    ]

    return await ollama_client.chat(messages, think=False)


# ---------------------------------------------------------------------------
# Helpers for web_research (Plan 51)
# ---------------------------------------------------------------------------


def _generate_search_variant(query: str) -> str:
    """Generate a programmatic search variant by rotating keywords and adding the current year."""
    words = query.split()
    year = str(datetime.date.today().year)

    # Add year if not present (helps for time-sensitive queries)
    has_year = any(w.isdigit() and len(w) == 4 for w in words)
    if not has_year:
        words.append(year)

    # Rotate: move first word to end (changes search engine weighting)
    if len(words) > 2:
        words = words[1:] + [words[0]]

    return " ".join(words)


def _generate_retry_variant(query: str) -> str:
    """Generate a different variant for retry round (mid-rotation)."""
    words = query.split()
    year = str(datetime.date.today().year)
    if not any(w.isdigit() and len(w) == 4 for w in words):
        words.append(year)
    if len(words) > 2:
        mid = len(words) // 2
        words = words[mid:] + words[:mid]
    return " ".join(words)


def _normalize_url(url: str) -> str:
    """Normalize URL for dedup: strip query params, fragment, trailing slash."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _dedup_urls(results: list[dict]) -> list[str]:
    """Deduplicate search result URLs by normalized domain+path."""
    seen: set[str] = set()
    unique: list[str] = []
    for r in results:
        url = r.get("href", "")
        if not url:
            continue
        normalized = _normalize_url(url)
        if normalized not in seen:
            seen.add(normalized)
            unique.append(url)
    return unique


def _format_research_output(
    query: str,
    top_chunks: list[tuple[str, str, float]],
    total_sources: int,
    max_chars: int = 12000,
) -> str:
    """Format ranked chunks into the final output, respecting the char limit."""
    # Build output, dropping lowest-ranked chunks if over limit
    chunks_to_use = list(top_chunks)
    while chunks_to_use:
        sections: list[str] = []
        for text, url, _sim in chunks_to_use:
            sections.append(f"### Source: {url}\n{text}")
        body = "\n\n---\n".join(sections)
        footer = (
            f"\n\n({total_sources} sources analyzed, {len(chunks_to_use)} relevant sections found)"
        )
        output = f'## Results from web research: "{query}"\n\n{body}{footer}'
        if len(output) <= max_chars:
            return output
        chunks_to_use = chunks_to_use[:-1]  # drop lowest-ranked

    return f'## Results from web research: "{query}"\n\nNo relevant content found.'


def _close_pipeline(trace, pipeline_span, pipeline_ctx, total_start, output_data):
    """Helper to close the pipeline span (fire-and-forget, best-effort)."""
    if not trace or not pipeline_span or not pipeline_ctx:
        return

    async def _close():
        try:
            output_data["latency_total_ms"] = round((time.monotonic() - total_start) * 1000, 1)
            pipeline_span.set_output(output_data)
            await pipeline_ctx.__aexit__(None, None, None)
        except Exception:
            pass

    # Schedule the close in the current event loop
    asyncio.ensure_future(_close())


def register(
    registry: SkillRegistry,
    ollama_client: OllamaClient | None = None,
    settings: Settings | None = None,
) -> None:
    # Read settings with defaults
    fetch_top_n = getattr(settings, "web_search_fetch_top_n", 3) if settings else 3
    fetch_timeout = getattr(settings, "web_search_fetch_timeout", 8.0) if settings else 8.0
    page_limit = getattr(settings, "web_search_extract_page_limit", 2500) if settings else 2500

    async def web_search(
        query: str,
        time_range: str | None = None,
        depth: str = "quick",
    ) -> str:
        logger.info("Searching web for: %s (time_range=%s, depth=%s)", query, time_range, depth)
        total_start = time.monotonic()

        try:
            loop = asyncio.get_running_loop()
            from functools import partial

            search_start = time.monotonic()
            results = await loop.run_in_executor(
                None, partial(_perform_search, query, time_range=time_range)
            )
            search_ms = (time.monotonic() - search_start) * 1000

            if not results:
                logger.info("No results found for: %s", query)
                return f"No results found for '{query}'."

            snippets = _format_snippets(results)
            logger.info("Found %d results for: %s", len(results), query)

            # Quick mode: return snippets only
            if depth != "detailed" or not ollama_client:
                return snippets

            # === Detailed mode: fetch pages + LLM extraction ===
            from app.skills.tools.web_extraction import fetch_multiple

            urls = [r["href"] for r in results[:fetch_top_n]]

            trace = get_current_trace()
            detailed_span_id: str | None = None

            # Open the detailed root span
            if trace:
                detailed_ctx = trace.span("web_search:detailed", kind="span")
                detailed_span = await detailed_ctx.__aenter__()
                detailed_span_id = detailed_span.span_id
                detailed_span.set_input(
                    {
                        "query": query,
                        "depth": "detailed",
                        "time_range": time_range,
                        "urls_to_fetch": urls,
                        "fetch_top_n": fetch_top_n,
                    }
                )

            try:
                # --- Fetch phase ---
                fetch_start = time.monotonic()
                fetch_results = await fetch_multiple(urls, timeout=fetch_timeout, max_concurrent=4)
                fetch_ms = (time.monotonic() - fetch_start) * 1000

                successful = [(url, text) for url, text in fetch_results if text]
                failed_count = len(fetch_results) - len(successful)

                # Trace: fetch span
                if trace and detailed_span_id:
                    async with trace.span(
                        "web_search:fetch", kind="span", parent_id=detailed_span_id
                    ) as fetch_span:
                        fetch_span.set_input(
                            {
                                "urls": urls,
                                "timeout": fetch_timeout,
                            }
                        )
                        fetch_span.set_output(
                            {
                                "results": [
                                    {
                                        "url": url,
                                        "status": "ok" if text else "empty",
                                        "chars_extracted": len(text) if text else 0,
                                    }
                                    for url, text in fetch_results
                                ],
                                "success_rate": f"{len(successful)}/{len(fetch_results)}",
                                "latency_ms": round(fetch_ms, 1),
                            }
                        )

                if not successful:
                    if trace and detailed_span_id:
                        detailed_span.set_output(
                            {
                                "pages_attempted": len(urls),
                                "pages_successful": 0,
                                "extraction_chars": 0,
                                "latency_total_ms": round(
                                    (time.monotonic() - total_start) * 1000, 1
                                ),
                            }
                        )
                        await detailed_ctx.__aexit__(None, None, None)
                    return (
                        snippets + "\n\n(Could not fetch page content. "
                        "Results above are search snippets only.)"
                    )

                # --- LLM extraction phase ---
                extract_start = time.monotonic()
                extracted = await _llm_extract(
                    query, successful, ollama_client, page_limit=page_limit
                )
                extract_ms = (time.monotonic() - extract_start) * 1000

                # Trace: LLM extraction span
                if trace and detailed_span_id:
                    async with trace.span(
                        "llm:web_extract", kind="generation", parent_id=detailed_span_id
                    ) as extract_span:
                        extract_span.set_input(
                            {
                                "query": query,
                                "pages_count": len(successful),
                                "pages": [
                                    {"url": url, "chars": len(text), "preview": text[:300]}
                                    for url, text in successful
                                ],
                                "total_input_chars": sum(len(t) for _, t in successful),
                                "page_limit_chars": page_limit,
                            }
                        )
                        extract_span.set_metadata(
                            {
                                "gen_ai.request.model": ollama_client._model,
                                "think": False,
                            }
                        )
                        extract_span.set_output(
                            {
                                "extracted_chars": len(extracted),
                                "extracted_preview": extracted[:500],
                                "latency_ms": round(extract_ms, 1),
                            }
                        )

                total_ms = (time.monotonic() - total_start) * 1000

                # Close the detailed root span with summary
                if trace and detailed_span_id:
                    detailed_span.set_output(
                        {
                            "search_results_count": len(results),
                            "pages_attempted": len(urls),
                            "pages_successful": len(successful),
                            "pages_failed": failed_count,
                            "extraction_chars": len(extracted),
                            "latency_search_ms": round(search_ms, 1),
                            "latency_fetch_ms": round(fetch_ms, 1),
                            "latency_extract_ms": round(extract_ms, 1),
                            "latency_total_ms": round(total_ms, 1),
                        }
                    )
                    await detailed_ctx.__aexit__(None, None, None)

                logger.info(
                    "web_search detailed: fetched %d/%d pages, extracted %d chars in %.0fms",
                    len(successful),
                    len(urls),
                    len(extracted),
                    total_ms,
                )

                return f"{snippets}\n\n---\n## Extracted content from top results:\n\n{extracted}"

            except Exception:
                # Ensure span closes on error
                if trace and detailed_span_id:
                    try:
                        await detailed_ctx.__aexit__(None, None, None)
                    except Exception:
                        pass
                raise

        except Exception as e:
            logger.exception("Search failed for query '%s'", query)
            return f"Error performing search: {e}"

    registry.register_tool(
        name="web_search",
        description=(
            "Search the internet for information. Returns search result snippets by default (fast). "
            "Set depth='detailed' when you need specific data like dates, times, prices, schedules, "
            "or detailed lists — this will fetch and extract actual content from the top result pages."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Search query (e.g. 'latest news about AI', 'recipe for lasagna')"
                    ),
                },
                "time_range": {
                    "type": "string",
                    "enum": ["d", "w", "m", "y"],
                    "description": ("Time filter: 'd' (day), 'w' (week), 'm' (month), 'y' (year)"),
                },
                "depth": {
                    "type": "string",
                    "enum": ["quick", "detailed"],
                    "description": (
                        "Search depth: 'quick' returns snippets only (fast), "
                        "'detailed' also fetches and extracts content from top result pages "
                        "(use for specific data like dates, prices, schedules)"
                    ),
                },
            },
            "required": ["query"],
        },
        handler=web_search,
        skill_name="search",
    )

    # === web_research composite tool (Plan 51) ===

    # Read web_research settings with defaults
    wr_max_pages = getattr(settings, "web_research_max_pages", 8) if settings else 8
    wr_fetch_timeout = getattr(settings, "web_research_fetch_timeout", 8.0) if settings else 8.0
    wr_max_concurrent = getattr(settings, "web_research_max_concurrent", 6) if settings else 6
    wr_chunk_size = getattr(settings, "web_research_chunk_size", 1500) if settings else 1500
    wr_top_k = getattr(settings, "web_research_top_k", 8) if settings else 8
    wr_sim_threshold = (
        getattr(settings, "web_research_similarity_threshold", 0.2) if settings else 0.2
    )
    wr_max_output = getattr(settings, "web_research_max_output_chars", 12000) if settings else 12000
    wr_embed_model = (
        getattr(settings, "embedding_model", "nomic-embed-text") if settings else "nomic-embed-text"
    )

    async def web_research(query: str, max_pages: int | None = None) -> str:
        """Deep web research: multi-query search → parallel fetch → chunk → embed → rank."""
        from app.skills.tools.web_extraction import chunk_text, fetch_multiple, rank_chunks

        effective_max_pages = max_pages or wr_max_pages
        total_start = time.monotonic()
        trace = get_current_trace()
        pipeline_span = None
        pipeline_ctx = None

        try:
            # --- Generate search variant ---
            variant = _generate_search_variant(query)
            is_multiquery = variant != query

            # Open pipeline span
            if trace:
                pipeline_ctx = trace.span("web_research:pipeline", kind="span")
                pipeline_span = await pipeline_ctx.__aenter__()
                pipeline_span.set_input(
                    {
                        "query": query,
                        "max_pages": effective_max_pages,
                        "is_multiquery": is_multiquery,
                        "query_variant": variant,
                    }
                )

            # --- Phase: Search ---
            search_start = time.monotonic()
            loop = asyncio.get_running_loop()
            from functools import partial

            search_coros = [
                loop.run_in_executor(None, partial(_perform_search, query, max_results=10)),
            ]
            if is_multiquery:
                search_coros.append(
                    loop.run_in_executor(None, partial(_perform_search, variant, max_results=10)),
                )

            search_raw = await asyncio.gather(*search_coros, return_exceptions=True)
            results_per_query: list[int] = []
            all_results: list[dict] = []
            for r in search_raw:
                if isinstance(r, BaseException):
                    logger.warning("web_research search failed: %s", r)
                    results_per_query.append(0)
                else:
                    results_per_query.append(len(r))
                    all_results.extend(r)

            search_ms = (time.monotonic() - search_start) * 1000

            # Dedup URLs
            deduped_urls = _dedup_urls(all_results)

            # Trace: search span
            if trace and pipeline_span:
                async with trace.span(
                    "web_research:search", kind="span", parent_id=pipeline_span.span_id
                ) as search_span:
                    search_span.set_input(
                        {
                            "queries": [query, variant] if is_multiquery else [query],
                            "max_results_per_query": 10,
                        }
                    )
                    search_span.set_output(
                        {
                            "results_per_query": results_per_query,
                            "total_unique_urls": len(deduped_urls),
                            "urls": deduped_urls[:10],
                            "latency_ms": round(search_ms, 1),
                        }
                    )

            if not deduped_urls:
                logger.info("web_research: no URLs found for: %s", query)
                _close_pipeline(
                    trace,
                    pipeline_span,
                    pipeline_ctx,
                    total_start,
                    {
                        "total_urls_found": 0,
                        "outcome": "no_urls",
                    },
                )
                return f"No results found for '{query}'."

            # --- Phase: Fetch ---
            urls_to_fetch = deduped_urls[:effective_max_pages]
            fetch_start = time.monotonic()
            fetch_results = await fetch_multiple(
                urls_to_fetch, timeout=wr_fetch_timeout, max_concurrent=wr_max_concurrent
            )
            fetch_ms = (time.monotonic() - fetch_start) * 1000

            successful = [(url, text) for url, text in fetch_results if text]
            pages_failed = len(fetch_results) - len(successful)

            # Trace: fetch span
            if trace and pipeline_span:
                async with trace.span(
                    "web_research:fetch", kind="span", parent_id=pipeline_span.span_id
                ) as fetch_span:
                    fetch_span.set_input(
                        {
                            "urls_to_fetch": urls_to_fetch,
                            "timeout": wr_fetch_timeout,
                            "max_concurrent": wr_max_concurrent,
                        }
                    )
                    fetch_span.set_output(
                        {
                            "results": [
                                {
                                    "url": url,
                                    "status": "ok" if text else "empty",
                                    "chars_extracted": len(text) if text else 0,
                                }
                                for url, text in fetch_results
                            ],
                            "success_rate": f"{len(successful)}/{len(fetch_results)}",
                            "latency_ms": round(fetch_ms, 1),
                        }
                    )

            if not successful:
                snippets = _format_snippets(all_results[:5])
                _close_pipeline(
                    trace,
                    pipeline_span,
                    pipeline_ctx,
                    total_start,
                    {
                        "total_urls_found": len(deduped_urls),
                        "pages_fetched": len(urls_to_fetch),
                        "pages_successful": 0,
                        "outcome": "no_content",
                    },
                )
                return f"Could not fetch page content. Search snippets:\n\n{snippets}"

            # --- Phase: Chunk + Embed + Rank ---
            all_chunks: list[tuple[str, str]] = []
            chunks_per_source: dict[str, int] = {}
            for url, text in successful:
                page_chunks = chunk_text(text, max_chunk_chars=wr_chunk_size)
                chunks_per_source[url] = len(page_chunks)
                for chunk in page_chunks:
                    all_chunks.append((chunk, url))

            if not all_chunks:
                # Fetched pages but chunking produced nothing useful
                raw_text = "\n\n---\n".join(
                    f"### Source: {url}\n{text[:2000]}" for url, text in successful[:3]
                )
                _close_pipeline(
                    trace,
                    pipeline_span,
                    pipeline_ctx,
                    total_start,
                    {
                        "total_urls_found": len(deduped_urls),
                        "pages_successful": len(successful),
                        "total_chunks": 0,
                        "outcome": "no_chunks",
                    },
                )
                return f'## Results from web research: "{query}"\n\n{raw_text}'

            # Rank with embeddings (requires ollama_client)
            top_chunks: list[tuple[str, str, float]] = []
            embed_ms = 0.0
            if ollama_client:
                embed_start = time.monotonic()
                try:
                    top_chunks = await rank_chunks(
                        query,
                        all_chunks,
                        ollama_client,
                        embed_model=wr_embed_model,
                        top_k=wr_top_k,
                        similarity_threshold=wr_sim_threshold,
                    )
                except Exception:
                    logger.warning("web_research: embedding/ranking failed, using unranked chunks")
                embed_ms = (time.monotonic() - embed_start) * 1000

            # Fallback: if no ollama_client or ranking failed, use first chunks unranked
            if not top_chunks:
                top_chunks = [(text, url, 0.0) for text, url in all_chunks[:wr_top_k]]

            best_similarity = top_chunks[0][2] if top_chunks else 0.0

            # Trace: rank span
            if trace and pipeline_span:
                async with trace.span(
                    "web_research:rank", kind="span", parent_id=pipeline_span.span_id
                ) as rank_span:
                    rank_span.set_input(
                        {
                            "total_chunks": len(all_chunks),
                            "chunks_per_source": chunks_per_source,
                            "embedding_model": wr_embed_model,
                            "top_k": wr_top_k,
                            "similarity_threshold": wr_sim_threshold,
                        }
                    )
                    rank_span.set_output(
                        {
                            "top_chunks": [
                                {
                                    "source": url,
                                    "similarity": round(sim, 4),
                                    "preview": text[:200],
                                }
                                for text, url, sim in top_chunks[:5]
                            ],
                            "above_threshold": len(top_chunks),
                            "below_threshold": len(all_chunks) - len(top_chunks),
                            "latency_embed_ms": round(embed_ms, 1),
                        }
                    )

            # --- Phase: Retry (conditional) ---
            retry_triggered = False
            visited_urls = set(urls_to_fetch)

            if len(top_chunks) < 2 or best_similarity < 0.25:
                retry_triggered = True
                retry_query = _generate_retry_variant(query)
                retry_start = time.monotonic()

                try:
                    retry_results = await loop.run_in_executor(
                        None, partial(_perform_search, retry_query, max_results=10)
                    )
                    retry_urls = [u for u in _dedup_urls(retry_results) if u not in visited_urls][
                        :effective_max_pages
                    ]

                    if retry_urls:
                        retry_fetched = await fetch_multiple(
                            retry_urls,
                            timeout=wr_fetch_timeout,
                            max_concurrent=wr_max_concurrent,
                        )
                        new_chunks: list[tuple[str, str]] = []
                        for r_url, r_text in retry_fetched:
                            if r_text:
                                for chunk in chunk_text(r_text, max_chunk_chars=wr_chunk_size):
                                    new_chunks.append((chunk, r_url))

                        if new_chunks and ollama_client:
                            # Re-rank ALL chunks (round 1 + round 2)
                            combined = all_chunks + new_chunks
                            try:
                                top_chunks = await rank_chunks(
                                    query,
                                    combined,
                                    ollama_client,
                                    embed_model=wr_embed_model,
                                    top_k=wr_top_k,
                                    similarity_threshold=wr_sim_threshold,
                                )
                            except Exception:
                                logger.warning("web_research: retry ranking failed")
                            best_similarity = top_chunks[0][2] if top_chunks else 0.0

                    new_urls_fetched = len(retry_urls) if retry_urls else 0
                except Exception:
                    logger.warning("web_research: retry search failed")
                    new_urls_fetched = 0
                    new_chunks = []

                # Trace: retry span
                if trace and pipeline_span:
                    async with trace.span(
                        "web_research:retry", kind="span", parent_id=pipeline_span.span_id
                    ) as retry_span:
                        retry_span.set_input(
                            {
                                "reason": "insufficient_chunks"
                                if len(top_chunks) < 2
                                else "low_similarity",
                                "best_similarity_round1": round(
                                    top_chunks[0][2] if top_chunks else 0.0, 4
                                ),
                                "new_query": retry_query,
                            }
                        )
                        retry_span.set_output(
                            {
                                "new_urls_fetched": new_urls_fetched,
                                "new_chunks_added": len(new_chunks) if new_chunks else 0,
                                "best_similarity_after_retry": round(best_similarity, 4),
                                "total_relevant_chunks": len(top_chunks),
                                "latency_ms": round((time.monotonic() - retry_start) * 1000, 1),
                            }
                        )

            # --- Phase: Format output ---
            output = _format_research_output(query, top_chunks, len(successful), wr_max_output)

            total_ms = (time.monotonic() - total_start) * 1000

            # Close pipeline span
            _close_pipeline(
                trace,
                pipeline_span,
                pipeline_ctx,
                total_start,
                {
                    "total_urls_found": len(deduped_urls),
                    "unique_urls": len(deduped_urls),
                    "pages_fetched": len(urls_to_fetch),
                    "pages_successful": len(successful),
                    "pages_failed": pages_failed,
                    "total_chunks": len(all_chunks),
                    "relevant_chunks": len(top_chunks),
                    "top_similarity": round(best_similarity, 4),
                    "retry_triggered": retry_triggered,
                    "output_chars": len(output),
                    "latency_search_ms": round(search_ms, 1),
                    "latency_fetch_ms": round(fetch_ms, 1),
                    "latency_embed_ms": round(embed_ms, 1),
                    "latency_total_ms": round(total_ms, 1),
                },
            )

            logger.info(
                "web_research: %d chunks ranked, top_sim=%.3f, retry=%s, %.0fms",
                len(top_chunks),
                best_similarity,
                retry_triggered,
                total_ms,
            )

            return output

        except Exception as e:
            if trace and pipeline_ctx:
                try:
                    await pipeline_ctx.__aexit__(None, None, None)
                except Exception:
                    pass
            logger.exception("web_research failed for query '%s'", query)
            return f"Error performing web research: {e}"

    registry.register_tool(
        name="web_research",
        description=(
            "Deep web research: searches multiple queries, fetches page content, "
            "and extracts the most relevant information using semantic ranking. "
            "Use when you need specific data (dates, times, prices, schedules, lists, "
            "match fixtures, product specs) from web pages — not just search snippets. "
            "Returns actual page content ranked by relevance."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Research query (e.g. 'fixture Rosario Central 2026')",
                },
                "max_pages": {
                    "type": "integer",
                    "description": "Maximum pages to fetch and analyze (default 8)",
                },
            },
            "required": ["query"],
        },
        handler=web_research,
        skill_name="search",
    )
