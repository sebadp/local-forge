"""Shared web content extraction utilities.

Used by web_search (Plan 52) and web_research (Plan 51).
Fetches pages with httpx and extracts clean text with trafilatura.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

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
