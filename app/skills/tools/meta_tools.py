"""Meta tools: tool discovery and system introspection.

Provides ``discover_tools`` — a lightweight search over registered tools
so the LLM can find capabilities not included in the initial tool set.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Injected at registration time by the startup code.
_registry_ref: object | None = None


def set_registry(registry: object) -> None:
    """Store a reference to the SkillRegistry for runtime search."""
    global _registry_ref
    _registry_ref = registry


async def discover_tools(query: str) -> str:
    """Find available tools by keyword search.

    Returns a formatted list of tool names and descriptions that match
    the query.  The LLM can then call ``request_more_tools`` with the
    appropriate category to load the full schemas.
    """
    if not _registry_ref:
        return "Error: tool registry not available"

    from app.skills.registry import SkillRegistry

    registry: SkillRegistry = _registry_ref  # type: ignore[assignment]
    results = registry.search_tools(query, limit=8)

    if not results:
        return f"No tools found matching '{query}'. Try a broader keyword."

    lines = [f"Found {len(results)} tools matching '{query}':"]
    for tool in results:
        lines.append(f"- **{tool['name']}**: {tool['description']}")
        if tool.get("category"):
            lines.append(f"  Category: {tool['category']}")
    lines.append(
        "\nTo use any of these tools, call request_more_tools with the "
        "appropriate category or query."
    )
    return "\n".join(lines)
