from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

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


def _perform_search(query: str, time_range: str | None = None) -> list[dict]:
    """Search DuckDuckGo using the duckduckgo-search library."""
    results = DDGS().text(
        query,
        timelimit=time_range,
        max_results=MAX_RESULTS,
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
