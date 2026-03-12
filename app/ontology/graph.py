"""GraphTraversal: BFS/DFS search over the entity relation graph."""

from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING

from app.ontology.models import Entity, GraphResult

if TYPE_CHECKING:
    from app.ontology.registry import EntityRegistry

logger = logging.getLogger(__name__)

_MAX_NODES_PER_HOP = 10  # Max neighbor entities to visit per BFS hop
_MAX_RESULTS_PER_TYPE = 5  # Max entities per type in the final result


async def traverse(
    registry: EntityRegistry,
    start_entity_id: str,
    depth: int = 1,
    entity_types: list[str] | None = None,
) -> GraphResult:
    """BFS traversal from start_entity_id up to `depth` hops.

    Returns a GraphResult with entities grouped by type.
    """
    root = await registry.get_entity(start_entity_id)
    result = GraphResult(root=root)

    if not root:
        return result

    visited = {start_entity_id}
    queue: deque[tuple[str, int]] = deque([(start_entity_id, 0)])
    found: dict[str, list[Entity]] = {}

    while queue:
        entity_id, current_depth = queue.popleft()
        if current_depth >= depth:
            continue

        try:
            neighbors = await registry.get_neighbors(entity_id, limit=_MAX_NODES_PER_HOP)
        except Exception:
            logger.debug("Graph traversal neighbor fetch failed", exc_info=True)
            continue

        for neighbor_id, _relation_type, _direction in neighbors:
            if neighbor_id in visited:
                continue
            visited.add(neighbor_id)

            neighbor = await registry.get_entity(neighbor_id)
            if not neighbor:
                continue

            # Filter by entity type if requested
            if entity_types and neighbor.entity_type not in entity_types:
                queue.append((neighbor_id, current_depth + 1))
                continue

            # Collect (cap per type)
            existing = found.get(neighbor.entity_type, [])
            if len(existing) < _MAX_RESULTS_PER_TYPE:
                existing.append(neighbor)
                found[neighbor.entity_type] = existing

            queue.append((neighbor_id, current_depth + 1))

    result.related = found
    return result


async def find_by_query(
    registry: EntityRegistry,
    query: str,
    entity_types: list[str] | None = None,
    depth: int = 1,
    limit: int = 5,
) -> list[GraphResult]:
    """Search entities by text query and traverse graph from each match."""
    matches = await registry.search_entities(query, entity_types=entity_types, limit=limit)
    results = []
    for entity in matches:
        graph = await traverse(registry, entity.id, depth=depth, entity_types=entity_types)
        results.append(graph)
    return results
