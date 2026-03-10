from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import TYPE_CHECKING

from ddgs import DDGS

from app.skills.registry import SkillRegistry

if TYPE_CHECKING:
    from app.database.repository import Repository

logger = logging.getLogger(__name__)

MAX_RESULTS = 5


def _search_news(query: str, time_range: str | None = None) -> list[dict]:
    """Search DuckDuckGo News. Returns dicts with: date, title, body, url, source, image."""
    results = DDGS().news(
        query,
        timelimit=time_range,
        max_results=MAX_RESULTS,
    )
    return results


def register(registry: SkillRegistry, repository: Repository) -> None:
    async def add_news_preference(source: str, preference: str) -> str:
        """Save a user's preference for a news source."""
        pref = preference.lower()
        if pref not in ("like", "dislike"):
            return "Error: preference must be 'like' or 'dislike'."

        content = f"News Preference: User {pref}s {source}."
        logger.info("Saving news preference: %s", content)
        await repository.add_memory(content, category="news_pref")
        return f"Memorized: You {pref} {source}."

    async def search_news(query: str, time_range: str | None = None) -> str:
        """Search for news with optional time filtering."""
        logger.info("Searching news: %s (time_range=%s)", query, time_range)
        try:
            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(
                None, partial(_search_news, query, time_range=time_range)
            )

            if not results:
                return f"No news found for '{query}'."

            formatted = []
            for i, r in enumerate(results, 1):
                title = r.get("title", "No title")
                url = r.get("url", "#")
                source = r.get("source", "")
                date = r.get("date", "")
                body = r.get("body", "")
                source_date = ", ".join(filter(None, [source, date]))
                formatted.append(f"{i}. [{title}]({url}) — {source_date}: {body}")

            logger.info("Found %d news results for: %s", len(results), query)
            return "\n\n".join(formatted)

        except Exception as e:
            logger.exception("News search failed for query '%s'", query)
            return f"Error searching news: {e}"

    registry.register_tool(
        name="add_news_preference",
        description="MEMORIZE a news preference. Call this IMMEDIATELY when the user explicitly mentions liking, disliking, or wanting to avoid a specific news source/site.",
        parameters={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Name of the news source (e.g. 'Pagina 12', 'Clarin')",
                },
                "preference": {
                    "type": "string",
                    "enum": ["like", "dislike"],
                    "description": "Must be 'like' or 'dislike'",
                },
            },
            "required": ["source", "preference"],
        },
        handler=add_news_preference,
        skill_name="news",
    )

    registry.register_tool(
        name="search_news",
        description="Search specifically for NEWS, CURRENT EVENTS, or RECENT UPDATES. Use this instead of generic web_search to leverage user preferences.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query including any site filters based on preferences",
                },
                "time_range": {
                    "type": "string",
                    "enum": ["d", "w", "m", "y"],
                    "description": "Time filter: 'd' (day), 'w' (week), 'm' (month), 'y' (year). USE THIS for relative dates like 'last week'.",
                },
            },
            "required": ["query"],
        },
        handler=search_news,
        skill_name="news",
    )
