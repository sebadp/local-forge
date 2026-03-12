"""AuditLogger — best-effort async audit log for entity mutations."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app.provenance.models import AuditEntry

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)


class AuditLogger:
    """Best-effort audit logger. Errors are logged, never propagated."""

    def __init__(self, conn: aiosqlite.Connection, enabled: bool = True):
        self._conn = conn
        self._enabled = enabled

    async def log_mutation(
        self,
        entity_type: str,
        entity_id: int,
        action: str,
        actor: str,
        *,
        source_trace_id: str | None = None,
        before_snapshot: str | None = None,
        after_snapshot: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Insert an audit log entry. Best-effort — never raises."""
        if not self._enabled:
            return
        try:
            await self._conn.execute(
                "INSERT INTO entity_audit_log "
                "(entity_type, entity_id, action, actor, source_trace_id, "
                "before_snapshot, after_snapshot, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entity_type,
                    entity_id,
                    action,
                    actor,
                    source_trace_id,
                    before_snapshot,
                    after_snapshot,
                    json.dumps(metadata or {}),
                ),
            )
            await self._conn.commit()
        except Exception:
            logger.warning(
                "Audit log failed: %s %s #%d by %s",
                action,
                entity_type,
                entity_id,
                actor,
                exc_info=True,
            )

    async def version_memory(
        self,
        memory_id: int,
        content: str,
        actor: str,
        *,
        source_trace_id: str | None = None,
    ) -> None:
        """Append a new version for a memory. Best-effort."""
        if not self._enabled:
            return
        try:
            # Get current max version
            cursor = await self._conn.execute(
                "SELECT COALESCE(MAX(version), 0) FROM memory_versions WHERE memory_id = ?",
                (memory_id,),
            )
            row = await cursor.fetchone()
            next_version = (row[0] if row else 0) + 1  # type: ignore[index]
            await self._conn.execute(
                "INSERT INTO memory_versions (memory_id, version, content, actor, source_trace_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (memory_id, next_version, content, actor, source_trace_id),
            )
            await self._conn.commit()
        except Exception:
            logger.warning(
                "Memory version failed: memory_id=%d actor=%s",
                memory_id,
                actor,
                exc_info=True,
            )

    async def get_audit_log(
        self,
        entity_type: str,
        entity_id: int,
        limit: int = 20,
    ) -> list[AuditEntry]:
        """Retrieve audit log entries for an entity."""
        try:
            cursor = await self._conn.execute(
                "SELECT id, entity_type, entity_id, action, actor, source_trace_id, "
                "before_snapshot, after_snapshot, metadata_json, created_at "
                "FROM entity_audit_log "
                "WHERE entity_type = ? AND entity_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (entity_type, entity_id, limit),
            )
            rows = await cursor.fetchall()
            return [
                AuditEntry(
                    id=r[0],
                    entity_type=r[1],
                    entity_id=r[2],  # type: ignore[index]
                    action=r[3],
                    actor=r[4],  # type: ignore[index]
                    source_trace_id=r[5],  # type: ignore[index]
                    before_snapshot=r[6],
                    after_snapshot=r[7],  # type: ignore[index]
                    metadata_json=r[8] or "{}",  # type: ignore[index]
                    created_at=r[9],  # type: ignore[index]
                )
                for r in rows
            ]
        except Exception:
            logger.warning("get_audit_log failed", exc_info=True)
            return []

    async def get_memory_versions(
        self,
        memory_id: int,
    ) -> list[dict]:
        """Retrieve all versions of a memory."""
        try:
            cursor = await self._conn.execute(
                "SELECT version, content, actor, source_trace_id, created_at "
                "FROM memory_versions WHERE memory_id = ? ORDER BY version",
                (memory_id,),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "version": r[0],  # type: ignore[index]
                    "content": r[1],  # type: ignore[index]
                    "actor": r[2],  # type: ignore[index]
                    "source_trace_id": r[3],  # type: ignore[index]
                    "created_at": r[4],  # type: ignore[index]
                }
                for r in rows
            ]
        except Exception:
            logger.warning("get_memory_versions failed", exc_info=True)
            return []

    async def get_entity_history(
        self,
        entity_type: str | None = None,
        actor: str | None = None,
        limit: int = 50,
    ) -> list[AuditEntry]:
        """Query audit log with optional filters."""
        try:
            conditions = []
            params: list = []
            if entity_type:
                conditions.append("entity_type = ?")
                params.append(entity_type)
            if actor:
                conditions.append("actor = ?")
                params.append(actor)
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            params.append(limit)
            cursor = await self._conn.execute(
                f"SELECT id, entity_type, entity_id, action, actor, source_trace_id, "
                f"before_snapshot, after_snapshot, metadata_json, created_at "
                f"FROM entity_audit_log {where} ORDER BY id DESC LIMIT ?",
                params,
            )
            rows = await cursor.fetchall()
            return [
                AuditEntry(
                    id=r[0],
                    entity_type=r[1],
                    entity_id=r[2],  # type: ignore[index]
                    action=r[3],
                    actor=r[4],  # type: ignore[index]
                    source_trace_id=r[5],  # type: ignore[index]
                    before_snapshot=r[6],
                    after_snapshot=r[7],  # type: ignore[index]
                    metadata_json=r[8] or "{}",  # type: ignore[index]
                    created_at=r[9],  # type: ignore[index]
                )
                for r in rows
            ]
        except Exception:
            logger.warning("get_entity_history failed", exc_info=True)
            return []

    async def cleanup_old_entries(self, days: int = 90) -> int:
        """Delete audit log entries older than N days. Returns count deleted."""
        try:
            cursor = await self._conn.execute(
                "DELETE FROM entity_audit_log WHERE created_at < datetime('now', ?)",
                (f"-{days} days",),
            )
            await self._conn.commit()
            return cursor.rowcount
        except Exception:
            logger.warning("Audit cleanup failed", exc_info=True)
            return 0
