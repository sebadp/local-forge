"""EntityRegistry: CRUD for entities and relations in the ontology graph."""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING

from app.ontology.models import Entity

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)


class EntityRegistry:
    """Manages entities and relations in the SQLite-backed ontology graph."""

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def upsert_entity(
        self,
        entity_type: str,
        ref_id: str,
        name: str,
        metadata: dict | None = None,
    ) -> str:
        """Insert or ignore entity; always return its id."""
        # First try to get existing
        cursor = await self._conn.execute(
            "SELECT id FROM entities WHERE entity_type = ? AND ref_id = ?",
            (entity_type, str(ref_id)),
        )
        row = await cursor.fetchone()
        if row:
            return row[0]

        entity_id = str(uuid.uuid4())
        await self._conn.execute(
            "INSERT OR IGNORE INTO entities (id, entity_type, ref_id, name, metadata_json) VALUES (?, ?, ?, ?, ?)",
            (entity_id, entity_type, str(ref_id), name[:500], json.dumps(metadata or {})),
        )
        await self._conn.commit()

        # Re-fetch in case of race (INSERT OR IGNORE may have been a no-op)
        cursor = await self._conn.execute(
            "SELECT id FROM entities WHERE entity_type = ? AND ref_id = ?",
            (entity_type, str(ref_id)),
        )
        row = await cursor.fetchone()
        return row[0] if row else entity_id

    async def get_entity(self, entity_id: str) -> Entity | None:
        cursor = await self._conn.execute(
            "SELECT id, entity_type, ref_id, name, metadata_json, created_at FROM entities WHERE id = ?",
            (entity_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return Entity(
            id=row[0],
            entity_type=row[1],
            ref_id=row[2],
            name=row[3],
            metadata=json.loads(row[4] or "{}"),
            created_at=row[5],
        )

    async def get_entity_by_ref(self, entity_type: str, ref_id: str) -> Entity | None:
        cursor = await self._conn.execute(
            "SELECT id, entity_type, ref_id, name, metadata_json, created_at FROM entities "
            "WHERE entity_type = ? AND ref_id = ?",
            (entity_type, str(ref_id)),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return Entity(
            id=row[0],
            entity_type=row[1],
            ref_id=row[2],
            name=row[3],
            metadata=json.loads(row[4] or "{}"),
            created_at=row[5],
        )

    async def add_relation(
        self,
        source_id: str,
        relation_type: str,
        target_id: str,
        confidence: float = 1.0,
        source_trace_id: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Add a relation (idempotent — UNIQUE constraint prevents duplicates)."""
        try:
            await self._conn.execute(
                "INSERT OR IGNORE INTO entity_relations "
                "(source_id, relation_type, target_id, confidence, source_trace_id, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    source_id,
                    relation_type,
                    target_id,
                    confidence,
                    source_trace_id,
                    json.dumps(metadata or {}),
                ),
            )
            await self._conn.commit()
        except Exception:
            logger.warning("add_relation failed (best-effort)", exc_info=True)

    async def get_neighbors(
        self, entity_id: str, relation_types: list[str] | None = None, limit: int = 20
    ) -> list[tuple[str, str, str]]:
        """Return (neighbor_entity_id, relation_type, direction) for an entity.

        direction is 'out' (source→target) or 'in' (target→source).
        """
        results: list[tuple[str, str, str]] = []
        if relation_types:
            placeholders = ",".join("?" * len(relation_types))
            # Outgoing
            cursor = await self._conn.execute(
                f"SELECT target_id, relation_type FROM entity_relations "
                f"WHERE source_id = ? AND relation_type IN ({placeholders}) "
                f"ORDER BY confidence DESC LIMIT ?",
                [entity_id, *relation_types, limit],
            )
            rows = await cursor.fetchall()
            results.extend((r[0], r[1], "out") for r in rows)
            # Incoming
            cursor = await self._conn.execute(
                f"SELECT source_id, relation_type FROM entity_relations "
                f"WHERE target_id = ? AND relation_type IN ({placeholders}) "
                f"ORDER BY confidence DESC LIMIT ?",
                [entity_id, *relation_types, limit],
            )
            rows = await cursor.fetchall()
            results.extend((r[0], r[1], "in") for r in rows)
        else:
            cursor = await self._conn.execute(
                "SELECT target_id, relation_type FROM entity_relations "
                "WHERE source_id = ? ORDER BY confidence DESC LIMIT ?",
                (entity_id, limit),
            )
            rows = await cursor.fetchall()
            results.extend((r[0], r[1], "out") for r in rows)
            cursor = await self._conn.execute(
                "SELECT source_id, relation_type FROM entity_relations "
                "WHERE target_id = ? ORDER BY confidence DESC LIMIT ?",
                (entity_id, limit),
            )
            rows = await cursor.fetchall()
            results.extend((r[0], r[1], "in") for r in rows)
        return results[:limit]

    async def search_entities(
        self, query: str, entity_types: list[str] | None = None, limit: int = 10
    ) -> list[Entity]:
        """Text search on entity name."""
        pattern = f"%{query}%"
        if entity_types:
            placeholders = ",".join("?" * len(entity_types))
            cursor = await self._conn.execute(
                f"SELECT id, entity_type, ref_id, name, metadata_json, created_at FROM entities "
                f"WHERE name LIKE ? AND entity_type IN ({placeholders}) "
                f"ORDER BY created_at DESC LIMIT ?",
                [pattern, *entity_types, limit],
            )
        else:
            cursor = await self._conn.execute(
                "SELECT id, entity_type, ref_id, name, metadata_json, created_at FROM entities "
                "WHERE name LIKE ? ORDER BY created_at DESC LIMIT ?",
                (pattern, limit),
            )
        rows = await cursor.fetchall()
        return [
            Entity(
                id=r[0],
                entity_type=r[1],
                ref_id=r[2],
                name=r[3],
                metadata=json.loads(r[4] or "{}"),
                created_at=r[5],
            )
            for r in rows
        ]

    async def get_entity_count(self) -> dict[str, int]:
        """Return count per entity_type."""
        cursor = await self._conn.execute(
            "SELECT entity_type, COUNT(*) FROM entities GROUP BY entity_type"
        )
        rows = await cursor.fetchall()
        return {r[0]: r[1] for r in rows}

    async def get_relation_count(self) -> int:
        cursor = await self._conn.execute("SELECT COUNT(*) FROM entity_relations")
        row = await cursor.fetchone()
        return row[0] if row else 0
