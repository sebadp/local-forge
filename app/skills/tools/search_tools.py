from __future__ import annotations

import asyncio
import logging

from ddgs import DDGS

from app.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

MAX_RESULTS = 5


def _perform_search(query: str, time_range: str | None = None) -> list[dict]:
    """Search DuckDuckGo using the duckduckgo-search library."""
    results = DDGS().text(
        query,
        timelimit=time_range,
        max_results=MAX_RESULTS,
    )
    return results


def register(registry: SkillRegistry) -> None:
    async def web_search(query: str, time_range: str | None = None) -> str:
        logger.info("Searching web for: %s (time_range=%s)", query, time_range)
        try:
            loop = asyncio.get_running_loop()
            from functools import partial

            results = await loop.run_in_executor(
                None, partial(_perform_search, query, time_range=time_range)
            )

            if not results:
                logger.info("No results found for: %s", query)
                return f"No results found for '{query}'."

            formatted = []
            for i, res in enumerate(results, 1):
                title = res.get("title", "No title")
                link = res.get("href", "#")
                body = res.get("body", "")
                formatted.append(f"{i}. [{title}]({link}): {body}")

            logger.info("Found %d results for: %s", len(results), query)
            return "\n\n".join(formatted)

        except Exception as e:
            logger.exception("Search failed for query '%s'", query)
            return f"Error performing search: {e}"

    registry.register_tool(
        name="web_search",
        description="Search the internet for information",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (e.g. 'latest news about AI', 'recipe for lasagna')",
                },
                "time_range": {
                    "type": "string",
                    "enum": ["d", "w", "m", "y"],
                    "description": "Time filter: 'd' (day), 'w' (week), 'm' (month), 'y' (year)",
                },
            },
            "required": ["query"],
        },
        handler=web_search,
        skill_name="search",
    )
