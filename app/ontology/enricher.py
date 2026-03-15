"""ContextEnricher: given found entities + query, fetch related entities via the graph.

Used in Phase B of _run_normal_flow() to augment the search results with
structurally related entities that semantic search didn't find.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ontology.registry import EntityRegistry

logger = logging.getLogger(__name__)

_DEFAULT_BUDGET = 1500  # chars


@dataclass
class EnrichmentResult:
    """Extra entities found via graph traversal."""

    extra_text: str = ""
    entities_found: int = 0
    types_found: list[str] = field(default_factory=list)


async def enrich_context(
    registry: EntityRegistry,
    query: str,
    budget_chars: int = _DEFAULT_BUDGET,
    depth: int = 1,
) -> EnrichmentResult:
    """Search the knowledge graph for query and return enriched context text.

    Best-effort: exceptions are caught and logged, returning empty result.
    """
    try:
        from app.ontology.graph import find_by_query
        from app.tracing.context import get_current_trace

        results = await find_by_query(
            registry=registry,
            query=query,
            depth=depth,
            limit=3,
        )

        if not results:
            return EnrichmentResult()

        sections = []
        total_chars = 0
        total_entities = 0
        types_seen: set[str] = set()

        for graph_result in results:
            if not graph_result.related:
                continue
            text = graph_result.to_text(budget_chars=budget_chars - total_chars)
            if text and text.strip():
                sections.append(text)
                total_chars += len(text)
                total_entities += graph_result.total()
                types_seen.update(graph_result.related.keys())
                if total_chars >= budget_chars:
                    break

        if not sections:
            return EnrichmentResult()

        combined = "\n".join(sections)
        result = EnrichmentResult(
            extra_text=combined,
            entities_found=total_entities,
            types_found=sorted(types_seen),
        )

        trace = get_current_trace()
        if trace:
            try:
                async with trace.span("ontology:enrich", kind="span") as span:
                    span.set_input({"query": query[:200], "depth": depth})
                    span.set_output(
                        {"entities_found": result.entities_found, "types": result.types_found}
                    )
            except Exception:
                logger.debug("Failed to record enrichment span", exc_info=True)

        return result
    except Exception:
        logger.debug("Context enrichment failed (best-effort)", exc_info=True)
        return EnrichmentResult()
