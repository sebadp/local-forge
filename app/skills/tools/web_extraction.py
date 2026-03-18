"""Shared web content extraction utilities.

Used by web_search (Plan 52) and web_research (Plan 51).
Fetches pages with httpx and extracts clean text with trafilatura.
Chunking + semantic ranking for web_research pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Regex to split text at markdown headings (##, ###, etc.)
_RE_HEADING_SPLIT = re.compile(r"\n(?=#{1,6}\s)")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5,es;q=0.3",
}


async def fetch_page(url: str, timeout: float = 8.0) -> str | None:
    """Fetch a single URL and return raw HTML, or None on failure."""
    try:
        async with httpx.AsyncClient(
            headers=_HEADERS, follow_redirects=True, timeout=timeout
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
    except Exception as e:
        logger.debug("fetch_page failed for %s: %s", url, e)
        return None


def extract_text(html: str) -> str:
    """Extract clean text from HTML using trafilatura with regex fallback."""
    try:
        import trafilatura

        text = trafilatura.extract(html, include_links=False, include_tables=True)
        if text:
            return text
    except Exception:
        logger.debug("trafilatura extraction failed, using regex fallback")

    # Regex fallback: strip tags
    import re

    clean = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    clean = re.sub(r"<style[^>]*>.*?</style>", "", clean, flags=re.DOTALL)
    clean = re.sub(r"<[^>]+>", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


async def fetch_and_extract(url: str, timeout: float = 8.0) -> tuple[str, str | None]:
    """Fetch a URL and extract text. Returns (url, extracted_text_or_None)."""
    html = await fetch_page(url, timeout=timeout)
    if not html:
        return url, None
    text = await asyncio.to_thread(extract_text, html)
    if not text or len(text) < 50:
        return url, None
    return url, text


async def fetch_multiple(
    urls: list[str],
    timeout: float = 8.0,
    max_concurrent: int = 4,
) -> list[tuple[str, str | None]]:
    """Fetch and extract multiple URLs in parallel with concurrency limit."""
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _bounded(url: str) -> tuple[str, str | None]:
        async with semaphore:
            return await fetch_and_extract(url, timeout=timeout)

    raw = await asyncio.gather(*[_bounded(u) for u in urls], return_exceptions=True)
    output: list[tuple[str, str | None]] = []
    for i, r in enumerate(raw):
        if isinstance(r, BaseException):
            logger.debug("fetch_multiple exception for %s: %s", urls[i], r)
            output.append((urls[i], None))
        else:
            output.append(r)
    return output


# ---------------------------------------------------------------------------
# Chunking + Semantic Ranking (Plan 51)
# ---------------------------------------------------------------------------


def chunk_text(text: str, max_chunk_chars: int = 1500) -> list[str]:
    """Split text into chunks by markdown headings, fallback to paragraphs.

    Merges small adjacent chunks and hard-splits oversized ones.
    Chunks shorter than 50 chars are filtered out.
    """
    # Try splitting by markdown headings
    parts = _RE_HEADING_SPLIT.split(text)

    if len(parts) <= 1:
        # Fallback: split by double newlines (paragraphs)
        parts = text.split("\n\n")

    chunks: list[str] = []
    current = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if current and len(current) + len(part) + 2 <= max_chunk_chars:
            current = f"{current}\n\n{part}"
        else:
            if current:
                chunks.append(current)
            if len(part) > max_chunk_chars:
                # Hard-split oversized parts
                for i in range(0, len(part), max_chunk_chars):
                    chunks.append(part[i : i + max_chunk_chars])
                current = ""
            else:
                current = part
    if current:
        chunks.append(current)

    return [c for c in chunks if len(c) >= 50]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def rank_chunks(
    query: str,
    chunks: list[tuple[str, str]],
    ollama_client: Any,
    embed_model: str = "nomic-embed-text",
    top_k: int = 8,
    similarity_threshold: float = 0.2,
) -> list[tuple[str, str, float]]:
    """Embed query + chunks with nomic-embed-text, rank by cosine similarity.

    Args:
        query: The search query.
        chunks: List of (chunk_text, source_url) tuples.
        ollama_client: OllamaClient instance for embedding.
        embed_model: Embedding model name.
        top_k: Maximum number of chunks to return.
        similarity_threshold: Minimum cosine similarity to include.

    Returns:
        List of (chunk_text, source_url, similarity) sorted by similarity descending.
    """
    if not chunks:
        return []

    # nomic-embed-text prefixes for better accuracy
    query_texts = [f"search_query: {query}"]
    chunk_texts = [f"search_document: {text}" for text, _ in chunks]

    all_texts = query_texts + chunk_texts
    embeddings = await ollama_client.embed(all_texts, model=embed_model)

    query_emb = embeddings[0]
    chunk_embs = embeddings[1:]

    scored: list[tuple[str, str, float]] = []
    for i, (text, url) in enumerate(chunks):
        sim = _cosine_similarity(query_emb, chunk_embs[i])
        if sim >= similarity_threshold:
            scored.append((text, url, sim))

    scored.sort(key=lambda x: x[2], reverse=True)
    return scored[:top_k]
