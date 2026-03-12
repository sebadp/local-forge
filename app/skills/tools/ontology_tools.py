"""Ontology tools: search_knowledge_graph."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ontology.registry import EntityRegistry
    from app.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


def register(registry: SkillRegistry, entity_registry: EntityRegistry) -> None:
    async def search_knowledge_graph(query: str, entity_types: str = "all", depth: int = 1) -> str:
        """Search the knowledge graph for entities and their relationships.

        Finds entities by text query and traverses their relationships to surface
        related memories, notes, projects, and other connected knowledge.

        Args:
            query: Natural language search query (e.g. "Python deployment", "proyecto X")
            entity_types: Comma-separated entity types to search: memory, note, project, task, topic.
                          Use "all" to search all types.
            depth: How many relationship hops to traverse (1-2 recommended, max 3)
        """
        from app.ontology.graph import find_by_query

        depth = max(1, min(3, depth))
        types: list[str] | None = None
        if entity_types and entity_types != "all":
            types = [t.strip() for t in entity_types.split(",") if t.strip()]

        try:
            results = await find_by_query(
                registry=entity_registry,
                query=query,
                entity_types=types,
                depth=depth,
                limit=5,
            )
        except Exception:
            logger.exception("search_knowledge_graph failed")
            return "Error searching knowledge graph."

        if not results:
            return f"No entities found for '{query}'."

        lines = [f"Knowledge graph results for '{query}':"]
        for graph_result in results:
            if graph_result.root:
                lines.append(f"\n📌 **{graph_result.root.name}** ({graph_result.root.entity_type})")
            text = graph_result.to_text(budget_chars=800)
            if text.strip():
                lines.append(text)

        return "\n".join(lines) or "No related entities found."

    registry.register_tool(
        name="search_knowledge_graph",
        description=(
            "Search the knowledge graph for entities (memories, notes, projects, tasks) "
            "and traverse their relationships. Use when the user asks about connections "
            "between different pieces of knowledge."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language search query",
                },
                "entity_types": {
                    "type": "string",
                    "description": "Comma-separated entity types: memory, note, project, task. Use 'all' for all types.",
                    "default": "all",
                },
                "depth": {
                    "type": "integer",
                    "description": "Relationship traversal depth (1-3, default 1)",
                    "default": 1,
                },
            },
            "required": ["query"],
        },
        handler=search_knowledge_graph,
        skill_name="ontology",
    )
