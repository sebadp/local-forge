"""BackfillJob: populate the entity graph from existing data.

Run as a script (scripts/backfill_ontology.py) or as a background task at startup.
Best-effort: errors are logged, not propagated.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.ontology.extractor import extract_memory_name

if TYPE_CHECKING:
    import aiosqlite

    from app.ontology.registry import EntityRegistry

logger = logging.getLogger(__name__)


async def backfill_memories(conn: aiosqlite.Connection, registry: EntityRegistry) -> int:
    """Register all active memories as entities."""
    cursor = await conn.execute("SELECT id, content, category FROM memories WHERE active = 1")
    rows = await cursor.fetchall()
    count = 0
    for row in rows:
        mem_id, content, category = row
        try:
            name = extract_memory_name(content)
            await registry.upsert_entity(
                entity_type="memory",
                ref_id=str(mem_id),
                name=name,
                metadata={"category": category or "general"},
            )
            count += 1
        except Exception:
            logger.debug("backfill_memories: skipped memory %s", mem_id, exc_info=True)
    logger.info("Backfilled %d memories as entities", count)
    return count


async def backfill_notes(conn: aiosqlite.Connection, registry: EntityRegistry) -> int:
    """Register all notes as entities."""
    cursor = await conn.execute("SELECT id, title FROM notes")
    rows = await cursor.fetchall()
    count = 0
    for row in rows:
        note_id, title = row
        try:
            await registry.upsert_entity(
                entity_type="note",
                ref_id=str(note_id),
                name=title or f"Note #{note_id}",
            )
            count += 1
        except Exception:
            logger.debug("backfill_notes: skipped note %s", note_id, exc_info=True)
    logger.info("Backfilled %d notes as entities", count)
    return count


async def backfill_projects(conn: aiosqlite.Connection, registry: EntityRegistry) -> int:
    """Register all projects as entities. Also register tasks with belongs_to relation."""
    cursor = await conn.execute("SELECT id, name, description FROM projects")
    proj_rows = await cursor.fetchall()
    count = 0
    for row in proj_rows:
        proj_id, name, description = row
        try:
            proj_entity_id = await registry.upsert_entity(
                entity_type="project",
                ref_id=str(proj_id),
                name=name,
                metadata={"description": (description or "")[:200]},
            )
            count += 1

            # Register tasks
            task_cursor = await conn.execute(
                "SELECT id, title FROM project_tasks WHERE project_id = ?", (proj_id,)
            )
            task_rows = await task_cursor.fetchall()
            for task_row in task_rows:
                task_id, task_title = task_row
                try:
                    task_entity_id = await registry.upsert_entity(
                        entity_type="task",
                        ref_id=str(task_id),
                        name=task_title or f"Task #{task_id}",
                        metadata={"project_id": proj_id},
                    )
                    await registry.add_relation(
                        task_entity_id, "belongs_to", proj_entity_id, confidence=1.0
                    )
                except Exception:
                    logger.debug("backfill_projects: skipped task %s", task_id, exc_info=True)
        except Exception:
            logger.debug("backfill_projects: skipped project %s", proj_id, exc_info=True)
    logger.info("Backfilled %d projects as entities", count)
    return count


async def run_full_backfill(conn: aiosqlite.Connection, registry: EntityRegistry) -> dict[str, int]:
    """Run all backfill jobs. Returns counts per entity type."""
    results = {}
    results["memory"] = await backfill_memories(conn, registry)
    results["note"] = await backfill_notes(conn, registry)
    results["project"] = await backfill_projects(conn, registry)
    return results
