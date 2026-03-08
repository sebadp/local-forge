from __future__ import annotations

import json
import struct
from typing import Any

import aiosqlite

from app.models import ChatMessage, Memory, Note, Project, ProjectNote, ProjectTask


def _compute_percentiles(name: str, sorted_values: list[float]) -> dict:
    """Compute p50/p95/p99 from a pre-sorted list of latency values."""

    def _pct(values: list[float], p: float) -> float:
        if not values:
            return 0.0
        idx = max(0, int(len(values) * p / 100) - 1)
        return round(values[idx], 1)

    return {
        "span": name,
        "n": len(sorted_values),
        "p50": _pct(sorted_values, 50),
        "p95": _pct(sorted_values, 95),
        "p99": _pct(sorted_values, 99),
        "max": round(sorted_values[-1], 1) if sorted_values else 0.0,
    }


class Repository:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def get_or_create_conversation(self, phone_number: str) -> int:
        cursor = await self._conn.execute(
            "SELECT id FROM conversations WHERE phone_number = ?",
            (phone_number,),
        )
        row = await cursor.fetchone()
        if row:
            await self._conn.execute(
                "UPDATE conversations SET updated_at = datetime('now') WHERE id = ?",
                (row[0],),
            )
            await self._conn.commit()
            return row[0]
        cursor = await self._conn.execute(
            "INSERT INTO conversations (phone_number) VALUES (?)",
            (phone_number,),
        )
        await self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_conversation_id(self, phone_number: str) -> int | None:
        cursor = await self._conn.execute(
            "SELECT id FROM conversations WHERE phone_number = ?",
            (phone_number,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def get_messages_paginated(
        self, conversation_id: int, limit: int, offset: int
    ) -> list[tuple[str, str, str]]:
        cursor = await self._conn.execute(
            "SELECT role, content, created_at FROM messages "
            "WHERE conversation_id = ? ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (conversation_id, limit, offset),
        )
        rows = await cursor.fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    async def save_message(
        self,
        conversation_id: int,
        role: str,
        content: str,
        wa_message_id: str | None = None,
    ) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO messages (conversation_id, role, content, wa_message_id) VALUES (?, ?, ?, ?)",
            (conversation_id, role, content, wa_message_id),
        )
        await self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_recent_messages(self, conversation_id: int, limit: int) -> list[ChatMessage]:
        cursor = await self._conn.execute(
            "SELECT role, content FROM messages "
            "WHERE conversation_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (conversation_id, limit),
        )
        rows = await cursor.fetchall()
        return [ChatMessage(role=r[0], content=r[1]) for r in reversed(rows)]  # type: ignore[call-overload]

    async def get_message_count(self, conversation_id: int) -> int:
        cursor = await self._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        )
        row = await cursor.fetchone()
        return row[0]  # type: ignore[index]

    async def is_duplicate(self, wa_message_id: str) -> bool:
        cursor = await self._conn.execute(
            "SELECT 1 FROM messages WHERE wa_message_id = ?",
            (wa_message_id,),
        )
        return await cursor.fetchone() is not None

    async def clear_conversation(self, conversation_id: int) -> None:
        await self._conn.execute(
            "DELETE FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        )
        await self._conn.execute(
            "DELETE FROM summaries WHERE conversation_id = ?",
            (conversation_id,),
        )
        await self._conn.execute(
            "DELETE FROM conversation_state WHERE conversation_id = ?",
            (conversation_id,),
        )
        await self._conn.commit()

    async def save_summary(
        self, conversation_id: int, summary_text: str, message_count: int
    ) -> None:
        await self._conn.execute(
            "INSERT INTO summaries (conversation_id, content, message_count) VALUES (?, ?, ?)",
            (conversation_id, summary_text, message_count),
        )
        await self._conn.commit()

    async def get_latest_summary(self, conversation_id: int) -> str | None:
        cursor = await self._conn.execute(
            "SELECT content FROM summaries WHERE conversation_id = ? ORDER BY id DESC LIMIT 1",
            (conversation_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def add_memory(self, content: str, category: str | None = None) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO memories (content, category) VALUES (?, ?)",
            (content, category),
        )
        await self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def remove_memory(self, content: str) -> bool:
        cursor = await self._conn.execute(
            "UPDATE memories SET active = 0 WHERE content = ? AND active = 1",
            (content,),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def list_memories(self) -> list[Memory]:
        cursor = await self._conn.execute(
            "SELECT id, content, category, active, created_at FROM memories WHERE active = 1 ORDER BY id",
        )
        rows = await cursor.fetchall()
        return [
            Memory(id=r[0], content=r[1], category=r[2], active=bool(r[3]), created_at=r[4])
            for r in rows
        ]

    async def get_active_memories(self, limit: int | None = None) -> list[str]:
        sql = "SELECT content FROM memories WHERE active = 1 ORDER BY id"
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        cursor = await self._conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [r[0] for r in rows]

    async def delete_old_messages(self, conversation_id: int, keep_last: int) -> int:
        cursor = await self._conn.execute(
            "DELETE FROM messages WHERE conversation_id = ? AND id NOT IN "
            "(SELECT id FROM messages WHERE conversation_id = ? ORDER BY created_at DESC, id DESC LIMIT ?)",
            (conversation_id, conversation_id, keep_last),
        )
        await self._conn.commit()
        return cursor.rowcount

    # --- Sticky Categories (context engineering) ---

    async def get_sticky_categories(self, conversation_id: int) -> list[str]:
        """Return sticky tool categories from the last tool-using turn."""
        cursor = await self._conn.execute(
            "SELECT sticky_categories FROM conversation_state WHERE conversation_id = ?",
            (conversation_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return []
        try:
            return json.loads(row[0]) or []
        except (json.JSONDecodeError, TypeError):
            return []

    async def save_sticky_categories(self, conversation_id: int, categories: list[str]) -> None:
        """Persist sticky categories for this conversation."""
        await self._conn.execute(
            "INSERT INTO conversation_state (conversation_id, sticky_categories, updated_at) "
            "VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(conversation_id) DO UPDATE SET "
            "sticky_categories = excluded.sticky_categories, "
            "updated_at = excluded.updated_at",
            (conversation_id, json.dumps(categories, ensure_ascii=False)),
        )
        await self._conn.commit()

    async def clear_sticky_categories(self, conversation_id: int) -> None:
        """Clear sticky categories when a turn doesn't use tools."""
        await self.save_sticky_categories(conversation_id, [])

    # --- Self-Correction Cooldown ---

    async def get_recent_self_corrections(self, hours: int = 2) -> list[Memory]:
        """Return active self_correction memories created within the last N hours."""
        cursor = await self._conn.execute(
            "SELECT id, content, category, active, created_at FROM memories "
            "WHERE category = 'self_correction' AND active = 1 "
            "AND created_at > datetime('now', ?)",
            (f"-{hours} hours",),
        )
        rows = await cursor.fetchall()
        return [
            Memory(id=r[0], content=r[1], category=r[2], active=bool(r[3]), created_at=r[4])
            for r in rows
        ]

    async def cleanup_expired_self_corrections(self, ttl_hours: int = 24) -> int:
        """Deactivate self_correction memories older than TTL. Returns count removed."""
        cursor = await self._conn.execute(
            "UPDATE memories SET active = 0 "
            "WHERE category = 'self_correction' AND active = 1 "
            "AND created_at < datetime('now', ?)",
            (f"-{ttl_hours} hours",),
        )
        await self._conn.commit()
        return cursor.rowcount

    # --- Deduplication ---

    async def try_claim_message(self, wa_message_id: str) -> bool:
        """Atomically claim a message ID. Returns True if already processed (duplicate)."""
        cursor = await self._conn.execute(
            "INSERT OR IGNORE INTO processed_messages (wa_message_id) VALUES (?)",
            (wa_message_id,),
        )
        await self._conn.commit()
        # If rowcount == 0, the INSERT was ignored → message was already claimed
        return cursor.rowcount == 0

    # --- Reply context ---

    async def get_message_by_wa_id(self, wa_message_id: str) -> ChatMessage | None:
        cursor = await self._conn.execute(
            "SELECT role, content FROM messages WHERE wa_message_id = ?",
            (wa_message_id,),
        )
        row = await cursor.fetchone()
        if row:
            return ChatMessage(role=row[0], content=row[1])
        return None

    # --- Notes ---

    async def save_note(self, title: str, content: str) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO notes (title, content) VALUES (?, ?)",
            (title, content),
        )
        await self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def list_notes(self) -> list[Note]:
        cursor = await self._conn.execute(
            "SELECT id, title, content, created_at FROM notes ORDER BY id DESC",
        )
        rows = await cursor.fetchall()
        return [Note(id=r[0], title=r[1], content=r[2], created_at=r[3]) for r in rows]

    async def search_notes(self, query: str) -> list[Note]:
        cursor = await self._conn.execute(
            "SELECT id, title, content, created_at FROM notes "
            "WHERE title LIKE ? OR content LIKE ? ORDER BY id DESC",
            (f"%{query}%", f"%{query}%"),
        )
        rows = await cursor.fetchall()
        return [Note(id=r[0], title=r[1], content=r[2], created_at=r[3]) for r in rows]

    async def delete_note(self, note_id: int) -> bool:
        cursor = await self._conn.execute(
            "DELETE FROM notes WHERE id = ?",
            (note_id,),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def get_note(self, note_id: int) -> Note | None:
        cursor = await self._conn.execute(
            "SELECT id, title, content, created_at FROM notes WHERE id = ?",
            (note_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return Note(id=row[0], title=row[1], content=row[2], created_at=row[3])

    # --- Embeddings (sqlite-vec) ---

    @staticmethod
    def _serialize_vector(vec: list[float]) -> bytes:
        return struct.pack(f"{len(vec)}f", *vec)

    async def save_embedding(self, memory_id: int, embedding: list[float]) -> None:
        blob = self._serialize_vector(embedding)
        await self._conn.execute(
            "INSERT OR REPLACE INTO vec_memories (memory_id, embedding) VALUES (?, ?)",
            (memory_id, blob),
        )
        await self._conn.commit()

    async def delete_embedding(self, memory_id: int) -> None:
        await self._conn.execute(
            "DELETE FROM vec_memories WHERE memory_id = ?",
            (memory_id,),
        )
        await self._conn.commit()

    async def search_similar_memories(self, embedding: list[float], top_k: int = 10) -> list[str]:
        blob = self._serialize_vector(embedding)
        cursor = await self._conn.execute(
            "SELECT m.content FROM vec_memories v "
            "JOIN memories m ON m.id = v.memory_id "
            "WHERE m.active = 1 AND v.embedding MATCH ? AND k = ? "
            "ORDER BY distance",
            (blob, top_k),
        )
        rows = await cursor.fetchall()
        return [r[0] for r in rows]

    async def search_similar_memories_with_distance(
        self, embedding: list[float], top_k: int = 10
    ) -> list[tuple[str, float]]:
        """Return (content, distance) pairs sorted by distance (ascending = most similar first).

        Uses L2 distance from sqlite-vec. Lower distance = more similar.
        """
        blob = self._serialize_vector(embedding)
        cursor = await self._conn.execute(
            "SELECT m.content, v.distance FROM vec_memories v "
            "JOIN memories m ON m.id = v.memory_id "
            "WHERE m.active = 1 AND v.embedding MATCH ? AND k = ? "
            "ORDER BY distance",
            (blob, top_k),
        )
        rows = await cursor.fetchall()
        return [(r[0], float(r[1])) for r in rows]

    async def get_unembedded_memories(self) -> list[tuple[int, str]]:
        cursor = await self._conn.execute(
            "SELECT m.id, m.content FROM memories m "
            "LEFT JOIN vec_memories v ON v.memory_id = m.id "
            "WHERE m.active = 1 AND v.memory_id IS NULL",
        )
        rows = await cursor.fetchall()
        return [(r[0], r[1]) for r in rows]

    async def remove_memory_return_id(self, content: str) -> int | None:
        """Deactivate a memory and return its ID (for embedding cleanup)."""
        cursor = await self._conn.execute(
            "SELECT id FROM memories WHERE content = ? AND active = 1",
            (content,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        memory_id = row[0]
        await self._conn.execute(
            "UPDATE memories SET active = 0 WHERE id = ?",
            (memory_id,),
        )
        await self._conn.commit()
        return memory_id

    # --- Note Embeddings ---

    async def save_note_embedding(self, note_id: int, embedding: list[float]) -> None:
        blob = self._serialize_vector(embedding)
        await self._conn.execute(
            "INSERT OR REPLACE INTO vec_notes (note_id, embedding) VALUES (?, ?)",
            (note_id, blob),
        )
        await self._conn.commit()

    async def delete_note_embedding(self, note_id: int) -> None:
        await self._conn.execute(
            "DELETE FROM vec_notes WHERE note_id = ?",
            (note_id,),
        )
        await self._conn.commit()

    async def search_similar_notes(self, embedding: list[float], top_k: int = 5) -> list[Note]:
        blob = self._serialize_vector(embedding)
        cursor = await self._conn.execute(
            "SELECT n.id, n.title, n.content, n.created_at FROM vec_notes v "
            "JOIN notes n ON n.id = v.note_id "
            "WHERE v.embedding MATCH ? AND k = ? "
            "ORDER BY distance",
            (blob, top_k),
        )
        rows = await cursor.fetchall()
        return [Note(id=r[0], title=r[1], content=r[2], created_at=r[3]) for r in rows]

    # --- User Profiles ---

    async def get_user_profile(self, phone_number: str) -> dict:
        """Return user profile dict, creating the row if it doesn't exist yet."""
        cursor = await self._conn.execute(
            "SELECT onboarding_state, data, message_count FROM user_profiles WHERE phone_number = ?",
            (phone_number,),
        )
        row = await cursor.fetchone()
        if row:
            return {
                "onboarding_state": row[0],
                "data": json.loads(row[1]),
                "message_count": row[2],
            }
        # Create on first access — new users skip onboarding (opt-in via /setup)
        await self._conn.execute(
            "INSERT OR IGNORE INTO user_profiles (phone_number, onboarding_state) VALUES (?, 'complete')",
            (phone_number,),
        )
        await self._conn.commit()
        return {"onboarding_state": "complete", "data": {}, "message_count": 0}

    async def save_user_profile(self, phone_number: str, state: str, data: dict) -> None:
        """Upsert user profile state and data."""
        await self._conn.execute(
            "INSERT INTO user_profiles (phone_number, onboarding_state, data, updated_at) "
            "VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(phone_number) DO UPDATE SET "
            "onboarding_state = excluded.onboarding_state, "
            "data = excluded.data, "
            "updated_at = excluded.updated_at",
            (phone_number, state, json.dumps(data, ensure_ascii=False)),
        )
        await self._conn.commit()

    async def increment_profile_message_count(self, phone_number: str) -> int:
        """Atomically increment message_count and return the new value."""
        await self._conn.execute(
            "INSERT INTO user_profiles (phone_number, message_count) VALUES (?, 1) "
            "ON CONFLICT(phone_number) DO UPDATE SET "
            "message_count = message_count + 1, updated_at = datetime('now')",
            (phone_number,),
        )
        await self._conn.commit()
        cursor = await self._conn.execute(
            "SELECT message_count FROM user_profiles WHERE phone_number = ?",
            (phone_number,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 1

    async def reset_user_profile(self, phone_number: str) -> None:
        """Reset profile to pending state (for /setup command)."""
        await self._conn.execute(
            "INSERT INTO user_profiles (phone_number, onboarding_state, data, message_count) "
            "VALUES (?, 'pending', '{}', 0) "
            "ON CONFLICT(phone_number) DO UPDATE SET "
            "onboarding_state = 'pending', data = '{}', message_count = 0, "
            "updated_at = datetime('now')",
            (phone_number,),
        )
        await self._conn.commit()

    async def get_unembedded_notes(self) -> list[tuple[int, str, str]]:
        cursor = await self._conn.execute(
            "SELECT n.id, n.title, n.content FROM notes n "
            "LEFT JOIN vec_notes v ON v.note_id = n.id "
            "WHERE v.note_id IS NULL",
        )
        rows = await cursor.fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    # --- Projects ---

    async def create_project(self, phone_number: str, name: str, description: str = "") -> int:
        cursor = await self._conn.execute(
            "INSERT INTO projects (phone_number, name, description) VALUES (?, ?, ?)",
            (phone_number, name, description),
        )
        await self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_project(self, project_id: int) -> Project | None:
        cursor = await self._conn.execute(
            "SELECT id, phone_number, name, description, status, created_at, updated_at "
            "FROM projects WHERE id = ?",
            (project_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return Project(
            id=row[0],
            phone_number=row[1],
            name=row[2],
            description=row[3],
            status=row[4],
            created_at=row[5],
            updated_at=row[6],
        )

    async def get_project_by_name(self, phone_number: str, name: str) -> Project | None:
        cursor = await self._conn.execute(
            "SELECT id, phone_number, name, description, status, created_at, updated_at "
            "FROM projects WHERE phone_number = ? AND name = ? COLLATE NOCASE",
            (phone_number, name),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return Project(
            id=row[0],
            phone_number=row[1],
            name=row[2],
            description=row[3],
            status=row[4],
            created_at=row[5],
            updated_at=row[6],
        )

    async def list_projects(self, phone_number: str, status: str | None = None) -> list[Project]:
        if status:
            cursor = await self._conn.execute(
                "SELECT id, phone_number, name, description, status, created_at, updated_at "
                "FROM projects WHERE phone_number = ? AND status = ? ORDER BY updated_at DESC",
                (phone_number, status),
            )
        else:
            cursor = await self._conn.execute(
                "SELECT id, phone_number, name, description, status, created_at, updated_at "
                "FROM projects WHERE phone_number = ? ORDER BY updated_at DESC",
                (phone_number,),
            )
        rows = await cursor.fetchall()
        return [
            Project(
                id=r[0],
                phone_number=r[1],
                name=r[2],
                description=r[3],
                status=r[4],
                created_at=r[5],
                updated_at=r[6],
            )
            for r in rows
        ]

    async def update_project(
        self, project_id: int, name: str | None = None, description: str | None = None
    ) -> bool:
        if name is None and description is None:
            return False
        parts = []
        params: list = []
        if name is not None:
            parts.append("name = ?")
            params.append(name)
        if description is not None:
            parts.append("description = ?")
            params.append(description)
        parts.append("updated_at = datetime('now')")
        params.append(project_id)
        cursor = await self._conn.execute(
            f"UPDATE projects SET {', '.join(parts)} WHERE id = ?",
            params,
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def update_project_status(self, project_id: int, status: str) -> bool:
        cursor = await self._conn.execute(
            "UPDATE projects SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status, project_id),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    # --- Project Tasks ---

    async def add_project_task(
        self,
        project_id: int,
        title: str,
        description: str = "",
        priority: str = "medium",
    ) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO project_tasks (project_id, title, description, priority) VALUES (?, ?, ?, ?)",
            (project_id, title, description, priority),
        )
        await self._conn.execute(
            "UPDATE projects SET updated_at = datetime('now') WHERE id = ?",
            (project_id,),
        )
        await self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_project_task(self, task_id: int) -> ProjectTask | None:
        cursor = await self._conn.execute(
            "SELECT id, project_id, title, description, status, priority, due_date, created_at, updated_at "
            "FROM project_tasks WHERE id = ?",
            (task_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return ProjectTask(
            id=row[0],
            project_id=row[1],
            title=row[2],
            description=row[3],
            status=row[4],
            priority=row[5],
            due_date=row[6],
            created_at=row[7],
            updated_at=row[8],
        )

    async def list_project_tasks(
        self, project_id: int, status: str | None = None
    ) -> list[ProjectTask]:
        priority_order = "CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END"
        status_order = "CASE status WHEN 'in_progress' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END"
        if status:
            cursor = await self._conn.execute(
                f"SELECT id, project_id, title, description, status, priority, due_date, created_at, updated_at "
                f"FROM project_tasks WHERE project_id = ? AND status = ? "
                f"ORDER BY {status_order}, {priority_order}",
                (project_id, status),
            )
        else:
            cursor = await self._conn.execute(
                f"SELECT id, project_id, title, description, status, priority, due_date, created_at, updated_at "
                f"FROM project_tasks WHERE project_id = ? "
                f"ORDER BY {status_order}, {priority_order}",
                (project_id,),
            )
        rows = await cursor.fetchall()
        return [
            ProjectTask(
                id=r[0],
                project_id=r[1],
                title=r[2],
                description=r[3],
                status=r[4],
                priority=r[5],
                due_date=r[6],
                created_at=r[7],
                updated_at=r[8],
            )
            for r in rows
        ]

    async def update_task_status(self, task_id: int, status: str) -> bool:
        cursor = await self._conn.execute(
            "UPDATE project_tasks SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status, task_id),
        )
        if cursor.rowcount > 0:
            # Touch parent project
            row = await (
                await self._conn.execute(
                    "SELECT project_id FROM project_tasks WHERE id = ?", (task_id,)
                )
            ).fetchone()
            if row:
                await self._conn.execute(
                    "UPDATE projects SET updated_at = datetime('now') WHERE id = ?", (row[0],)
                )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def update_task_due_date(self, task_id: int, due_date: str | None) -> bool:
        cursor = await self._conn.execute(
            "UPDATE project_tasks SET due_date = ?, updated_at = datetime('now') WHERE id = ?",
            (due_date, task_id),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def delete_project_task(self, task_id: int) -> bool:
        # Fetch project_id before deleting so we can touch updated_at
        row = await (
            await self._conn.execute(
                "SELECT project_id FROM project_tasks WHERE id = ?", (task_id,)
            )
        ).fetchone()
        cursor = await self._conn.execute(
            "DELETE FROM project_tasks WHERE id = ?",
            (task_id,),
        )
        if cursor.rowcount > 0 and row:
            await self._conn.execute(
                "UPDATE projects SET updated_at = datetime('now') WHERE id = ?", (row[0],)
            )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def get_project_progress(self, project_id: int) -> dict:
        cursor = await self._conn.execute(
            "SELECT status, COUNT(*) FROM project_tasks WHERE project_id = ? GROUP BY status",
            (project_id,),
        )
        rows = await cursor.fetchall()
        counts = {r[0]: r[1] for r in rows}
        pending = counts.get("pending", 0)
        in_progress = counts.get("in_progress", 0)
        done = counts.get("done", 0)
        total = sum(counts.values())
        return {"pending": pending, "in_progress": in_progress, "done": done, "total": total}

    # --- Project Activity ---

    async def log_project_activity(self, project_id: int, action: str, detail: str = "") -> None:
        await self._conn.execute(
            "INSERT INTO project_activity (project_id, action, detail) VALUES (?, ?, ?)",
            (project_id, action, detail),
        )
        await self._conn.commit()

    async def get_project_activity(
        self, project_id: int, limit: int = 20
    ) -> list[tuple[str, str, str]]:
        cursor = await self._conn.execute(
            "SELECT action, detail, created_at FROM project_activity "
            "WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        )
        rows = await cursor.fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    # --- Project Notes ---

    async def add_project_note(self, project_id: int, content: str) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO project_notes (project_id, content) VALUES (?, ?)",
            (project_id, content),
        )
        await self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def list_project_notes(self, project_id: int) -> list[ProjectNote]:
        cursor = await self._conn.execute(
            "SELECT id, project_id, content, created_at FROM project_notes "
            "WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        )
        rows = await cursor.fetchall()
        return [ProjectNote(id=r[0], project_id=r[1], content=r[2], created_at=r[3]) for r in rows]

    async def delete_project_note(self, note_id: int) -> bool:
        cursor = await self._conn.execute("DELETE FROM project_notes WHERE id = ?", (note_id,))
        await self._conn.commit()
        return cursor.rowcount > 0

    async def save_project_note_embedding(self, note_id: int, embedding: list[float]) -> None:
        blob = self._serialize_vector(embedding)
        await self._conn.execute(
            "INSERT OR REPLACE INTO vec_project_notes (note_id, embedding) VALUES (?, ?)",
            (note_id, blob),
        )
        await self._conn.commit()

    async def search_similar_project_notes(
        self, project_id: int, embedding: list[float], top_k: int = 5
    ) -> list[ProjectNote]:
        blob = self._serialize_vector(embedding)
        cursor = await self._conn.execute(
            "SELECT pn.id, pn.project_id, pn.content, pn.created_at "
            "FROM vec_project_notes v "
            "JOIN project_notes pn ON pn.id = v.note_id "
            "WHERE pn.project_id = ? AND v.embedding MATCH ? AND k = ? "
            "ORDER BY distance",
            (project_id, blob, top_k),
        )
        rows = await cursor.fetchall()
        return [ProjectNote(id=r[0], project_id=r[1], content=r[2], created_at=r[3]) for r in rows]

    # --- Tracing ---

    async def save_trace(
        self,
        trace_id: str,
        phone_number: str,
        input_text: str,
        message_type: str = "text",
    ) -> None:
        await self._conn.execute(
            "INSERT INTO traces (id, phone_number, input_text, message_type) VALUES (?, ?, ?, ?)",
            (trace_id, phone_number, input_text, message_type),
        )
        await self._conn.commit()

    async def finish_trace(
        self,
        trace_id: str,
        status: str,
        output_text: str | None = None,
        wa_message_id: str | None = None,
    ) -> None:
        await self._conn.execute(
            "UPDATE traces SET status = ?, output_text = ?, wa_message_id = ?, "
            "completed_at = datetime('now') WHERE id = ?",
            (status, output_text, wa_message_id, trace_id),
        )
        await self._conn.commit()

    async def save_trace_span(
        self,
        span_id: str,
        trace_id: str,
        name: str,
        kind: str = "span",
        parent_id: str | None = None,
    ) -> None:
        await self._conn.execute(
            "INSERT INTO trace_spans (id, trace_id, name, kind, parent_id) VALUES (?, ?, ?, ?, ?)",
            (span_id, trace_id, name, kind, parent_id),
        )
        await self._conn.commit()

    async def finish_trace_span(
        self,
        span_id: str,
        status: str,
        latency_ms: float,
        input_data: Any = None,
        output_data: Any = None,
        metadata: dict | None = None,
    ) -> None:
        await self._conn.execute(
            "UPDATE trace_spans SET status = ?, latency_ms = ?, input = ?, output = ?, "
            "metadata = ?, completed_at = datetime('now') WHERE id = ?",
            (
                status,
                latency_ms,
                json.dumps(input_data) if input_data is not None else None,
                json.dumps(output_data) if output_data is not None else None,
                json.dumps(metadata or {}),
                span_id,
            ),
        )
        await self._conn.commit()

    async def save_trace_score(
        self,
        trace_id: str,
        name: str,
        value: float,
        source: str = "system",
        comment: str | None = None,
        span_id: str | None = None,
    ) -> None:
        await self._conn.execute(
            "INSERT INTO trace_scores (trace_id, name, value, source, comment, span_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (trace_id, name, value, source, comment, span_id),
        )
        await self._conn.commit()

    async def get_latest_trace_id(self, phone_number: str) -> str | None:
        cursor = await self._conn.execute(
            "SELECT id FROM traces WHERE phone_number = ? AND status = 'completed' "
            "ORDER BY completed_at DESC LIMIT 1",
            (phone_number,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def get_trace_id_by_wa_message_id(self, wa_message_id: str) -> str | None:
        cursor = await self._conn.execute(
            "SELECT id FROM traces WHERE wa_message_id = ? LIMIT 1",
            (wa_message_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def get_trace_io_by_id(self, trace_id: str) -> tuple[str, str] | None:
        """Return (input_text, output_text) for a trace, or None if not found."""
        cursor = await self._conn.execute(
            "SELECT input_text, output_text FROM traces WHERE id = ?",
            (trace_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return row[0] or "", row[1] or ""

    async def get_trace_scores(self, trace_id: str) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT id, name, value, source, comment, span_id, created_at "
            "FROM trace_scores WHERE trace_id = ? ORDER BY created_at",
            (trace_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "value": r[2],
                "source": r[3],
                "comment": r[4],
                "span_id": r[5],
                "created_at": r[6],
            }
            for r in rows
        ]

    async def get_trace_with_spans(self, trace_id: str) -> dict | None:
        cursor = await self._conn.execute(
            "SELECT id, phone_number, input_text, output_text, wa_message_id, "
            "message_type, status, started_at, completed_at, metadata "
            "FROM traces WHERE id = ?",
            (trace_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        trace = {
            "id": row[0],
            "phone_number": row[1],
            "input_text": row[2],
            "output_text": row[3],
            "wa_message_id": row[4],
            "message_type": row[5],
            "status": row[6],
            "started_at": row[7],
            "completed_at": row[8],
            "metadata": json.loads(row[9]),
        }
        span_cursor = await self._conn.execute(
            "SELECT id, parent_id, name, kind, input, output, status, "
            "started_at, completed_at, latency_ms, metadata "
            "FROM trace_spans WHERE trace_id = ? ORDER BY started_at",
            (trace_id,),
        )
        span_rows = await span_cursor.fetchall()
        trace["spans"] = [
            {
                "id": s[0],
                "parent_id": s[1],
                "name": s[2],
                "kind": s[3],
                "input": json.loads(s[4]) if s[4] else None,
                "output": json.loads(s[5]) if s[5] else None,
                "status": s[6],
                "started_at": s[7],
                "completed_at": s[8],
                "latency_ms": s[9],
                "metadata": json.loads(s[10]) if s[10] else {},
            }
            for s in span_rows
        ]
        trace["scores"] = await self.get_trace_scores(trace_id)
        return trace

    async def get_recent_user_message_embeddings(
        self, conv_id: int, hours: int = 24, limit: int = 20
    ) -> list[list[float]]:
        """Return recent user message embeddings for repeated-question detection.

        Requires that message embeddings are stored in a separate vec table.
        Currently returns empty list (placeholder for future embedding-per-message support).
        """
        # Placeholder: message-level embeddings not yet implemented.
        # This returns [] which causes _is_repeated_question to skip the check gracefully.
        return []

    # --- Eval Dataset ---

    async def add_dataset_entry(
        self,
        trace_id: str,
        entry_type: str,
        input_text: str,
        output_text: str | None = None,
        expected_output: str | None = None,
        metadata: dict | None = None,
        tags: list[str] | None = None,
    ) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO eval_dataset "
            "(trace_id, entry_type, input_text, output_text, expected_output, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                trace_id,
                entry_type,
                input_text,
                output_text,
                expected_output,
                json.dumps(metadata or {}),
            ),
        )
        dataset_id = cursor.lastrowid  # type: ignore[assignment]
        if tags and dataset_id:
            await self._conn.executemany(
                "INSERT OR IGNORE INTO eval_dataset_tags (dataset_id, tag) VALUES (?, ?)",
                [(dataset_id, tag) for tag in tags],
            )
        await self._conn.commit()
        return dataset_id  # type: ignore[return-value]

    async def get_dataset_entries(
        self,
        entry_type: str | None = None,
        tag: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        params: list = []
        conditions: list[str] = []
        base = (
            "SELECT d.id, d.trace_id, d.entry_type, d.input_text, d.output_text, "
            "d.expected_output, d.metadata, d.created_at "
            "FROM eval_dataset d"
        )
        if tag:
            base += " JOIN eval_dataset_tags t ON t.dataset_id = d.id AND t.tag = ?"
            params.append(tag)
        if entry_type:
            conditions.append("d.entry_type = ?")
            params.append(entry_type)
        if conditions:
            base += " WHERE " + " AND ".join(conditions)
        base += " ORDER BY d.created_at DESC LIMIT ?"
        params.append(limit)
        cursor = await self._conn.execute(base, params)
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "trace_id": r[1],
                "entry_type": r[2],
                "input_text": r[3],
                "output_text": r[4],
                "expected_output": r[5],
                "metadata": json.loads(r[6]),
                "created_at": r[7],
            }
            for r in rows
        ]

    async def add_dataset_tags(self, dataset_id: int, tags: list[str]) -> None:
        await self._conn.executemany(
            "INSERT OR IGNORE INTO eval_dataset_tags (dataset_id, tag) VALUES (?, ?)",
            [(dataset_id, tag) for tag in tags],
        )
        await self._conn.commit()

    async def get_dataset_stats(self) -> dict:
        cursor = await self._conn.execute(
            "SELECT entry_type, COUNT(*) FROM eval_dataset GROUP BY entry_type"
        )
        rows = await cursor.fetchall()
        counts = {r[0]: r[1] for r in rows}
        total = sum(counts.values())
        tag_cursor = await self._conn.execute(
            "SELECT tag, COUNT(*) FROM eval_dataset_tags GROUP BY tag ORDER BY COUNT(*) DESC LIMIT 10"
        )
        tag_rows = await tag_cursor.fetchall()
        return {
            "total": total,
            "golden": counts.get("golden", 0),
            "failure": counts.get("failure", 0),
            "correction": counts.get("correction", 0),
            "top_tags": {r[0]: r[1] for r in tag_rows},
        }

    # --- Prompt Versioning ---

    async def save_prompt_version(
        self,
        prompt_name: str,
        version: int,
        content: str,
        created_by: str = "human",
    ) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO prompt_versions (prompt_name, version, content, created_by) "
            "VALUES (?, ?, ?, ?)",
            (prompt_name, version, content, created_by),
        )
        await self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_active_prompt_version(self, prompt_name: str) -> dict | None:
        cursor = await self._conn.execute(
            "SELECT id, prompt_name, version, content, is_active, scores, created_by, "
            "approved_at, created_at FROM prompt_versions "
            "WHERE prompt_name = ? AND is_active = 1 LIMIT 1",
            (prompt_name,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "prompt_name": row[1],
            "version": row[2],
            "content": row[3],
            "is_active": bool(row[4]),
            "scores": json.loads(row[5]),
            "created_by": row[6],
            "approved_at": row[7],
            "created_at": row[8],
        }

    async def get_prompt_version(self, prompt_name: str, version: int) -> dict | None:
        cursor = await self._conn.execute(
            "SELECT id, prompt_name, version, content, is_active, scores, created_by, "
            "approved_at, created_at FROM prompt_versions "
            "WHERE prompt_name = ? AND version = ?",
            (prompt_name, version),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "prompt_name": row[1],
            "version": row[2],
            "content": row[3],
            "is_active": bool(row[4]),
            "scores": json.loads(row[5]),
            "created_by": row[6],
            "approved_at": row[7],
            "created_at": row[8],
        }

    async def activate_prompt_version(self, prompt_name: str, version: int) -> None:
        """Deactivate all versions for prompt_name, then activate the given version.

        Runs as an atomic transaction so there is always exactly one active version.
        """
        await self._conn.execute(
            "UPDATE prompt_versions SET is_active = 0 WHERE prompt_name = ?",
            (prompt_name,),
        )
        await self._conn.execute(
            "UPDATE prompt_versions SET is_active = 1, approved_at = datetime('now') "
            "WHERE prompt_name = ? AND version = ?",
            (prompt_name, version),
        )
        await self._conn.commit()

    async def list_prompt_versions(self, prompt_name: str) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT id, version, is_active, created_by, approved_at, created_at "
            "FROM prompt_versions WHERE prompt_name = ? ORDER BY version DESC",
            (prompt_name,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "version": r[1],
                "is_active": bool(r[2]),
                "created_by": r[3],
                "approved_at": r[4],
                "created_at": r[5],
            }
            for r in rows
        ]

    async def seed_default_prompts(self, defaults: dict[str, str]) -> int:
        """Insert v1 for any prompt_name not yet in prompt_versions. Returns count seeded.

        Safe to call on every startup — idempotent (skips names that already have a v1).
        """
        seeded = 0
        for name, content in defaults.items():
            existing = await self.get_active_prompt_version(name)
            if existing is None:
                await self.save_prompt_version(
                    name, version=1, content=content, created_by="system"
                )
                await self.activate_prompt_version(name, version=1)
                seeded += 1
        if seeded:
            import logging as _logging

            _logging.getLogger(__name__).info("Seeded %d default prompt(s) into DB", seeded)
        return seeded

    async def list_all_active_prompts(self) -> list[dict]:
        """Return all active prompt versions across all prompt names."""
        cursor = await self._conn.execute(
            "SELECT prompt_name, version, created_by, approved_at, created_at "
            "FROM prompt_versions WHERE is_active = 1 ORDER BY prompt_name",
        )
        rows = await cursor.fetchall()
        return [
            {
                "prompt_name": r[0],
                "version": r[1],
                "created_by": r[2],
                "approved_at": r[3],
                "created_at": r[4],
            }
            for r in rows
        ]

    # --- Misc helpers ---

    async def get_latest_memory(self) -> Any:
        """Return the most recently inserted active memory (Memory model)."""
        from app.models import Memory

        cursor = await self._conn.execute(
            "SELECT id, content, category, active, created_at FROM memories "
            "WHERE active = 1 ORDER BY id DESC LIMIT 1",
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return Memory(
            id=row[0], content=row[1], category=row[2], active=bool(row[3]), created_at=row[4]
        )

    # --- Eval Skill queries ---

    async def get_eval_summary(self, days: int = 7) -> dict:
        """Aggregate score stats for the last N days, grouped by score name."""
        cursor = await self._conn.execute(
            "SELECT ts.name, ts.source, AVG(ts.value) as avg_val, "
            "MIN(ts.value) as min_val, MAX(ts.value) as max_val, COUNT(*) as n "
            "FROM trace_scores ts "
            "JOIN traces t ON t.id = ts.trace_id "
            "WHERE t.started_at > datetime('now', ? || ' days') "
            "GROUP BY ts.name, ts.source "
            "ORDER BY ts.name, ts.source",
            (f"-{days}",),
        )
        rows = await cursor.fetchall()

        trace_cursor = await self._conn.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) "
            "FROM traces WHERE started_at > datetime('now', ? || ' days')",
            (f"-{days}",),
        )
        trace_row = await trace_cursor.fetchone()
        return {
            "days": days,
            "total_traces": trace_row[0] or 0 if trace_row else 0,
            "completed_traces": trace_row[1] or 0 if trace_row else 0,
            "failed_traces": trace_row[2] or 0 if trace_row else 0,
            "scores": [
                {
                    "name": r[0],
                    "source": r[1],
                    "avg": round(r[2], 3),
                    "min": round(r[3], 3),
                    "max": round(r[4], 3),
                    "count": r[5],
                }
                for r in rows
            ],
        }

    async def get_failed_traces(self, limit: int = 10) -> list[dict]:
        """Return recent traces that have at least one score below 0.5."""
        cursor = await self._conn.execute(
            "SELECT DISTINCT t.id, t.phone_number, t.input_text, t.output_text, "
            "t.status, t.started_at, MIN(ts.value) as min_score "
            "FROM traces t "
            "JOIN trace_scores ts ON ts.trace_id = t.id "
            "WHERE ts.value < 0.5 "
            "GROUP BY t.id "
            "ORDER BY t.started_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "phone_number": r[1],
                "input_text": r[2],
                "output_text": r[3],
                "status": r[4],
                "started_at": r[5],
                "min_score": round(r[6], 3),
            }
            for r in rows
        ]

    async def cleanup_old_traces(self, days: int = 90) -> int:
        """Delete traces (and cascading spans/scores) older than N days.

        Returns the number of traces deleted.
        """
        cursor = await self._conn.execute(
            "SELECT id FROM traces WHERE started_at < datetime('now', ? || ' days')",
            (f"-{days}",),
        )
        old_ids = [r[0] for r in await cursor.fetchall()]
        if not old_ids:
            return 0

        placeholders = ",".join("?" * len(old_ids))
        # Delete spans and scores first (FK constraints)
        await self._conn.execute(
            f"DELETE FROM trace_spans WHERE trace_id IN ({placeholders})", old_ids
        )
        await self._conn.execute(
            f"DELETE FROM trace_scores WHERE trace_id IN ({placeholders})", old_ids
        )
        cursor = await self._conn.execute(
            f"DELETE FROM traces WHERE id IN ({placeholders})", old_ids
        )
        await self._conn.commit()
        return cursor.rowcount

    async def get_failure_trend(self, days: int = 30) -> list[dict]:
        """Return daily trace counts and failure counts for the last N days."""
        cursor = await self._conn.execute(
            """
            SELECT
                date(started_at) AS day,
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
            FROM traces
            WHERE started_at >= datetime('now', ? || ' days')
            GROUP BY day
            ORDER BY day DESC
            """,
            (f"-{days}",),
        )
        rows = await cursor.fetchall()
        return [{"day": r[0], "total": r[1], "failed": r[2] or 0} for r in rows]

    async def get_score_distribution(self) -> list[dict]:
        """Return per-check score stats: count, avg, and failure count (<0.5)."""
        cursor = await self._conn.execute(
            """
            SELECT
                name,
                COUNT(*) AS count,
                AVG(value) AS avg_score,
                SUM(CASE WHEN value < 0.5 THEN 1 ELSE 0 END) AS failures
            FROM trace_scores
            GROUP BY name
            ORDER BY failures DESC
            """
        )
        rows = await cursor.fetchall()
        return [
            {
                "check": r[0],
                "count": r[1],
                "avg_score": round(r[2], 3) if r[2] is not None else 0.0,
                "failures": r[3] or 0,
            }
            for r in rows
        ]

    async def get_overdue_tasks(self, phone_number: str) -> list[ProjectTask]:
        cursor = await self._conn.execute(
            "SELECT pt.id, pt.project_id, pt.title, pt.description, pt.status, pt.priority, "
            "pt.due_date, pt.created_at, pt.updated_at "
            "FROM project_tasks pt "
            "JOIN projects p ON p.id = pt.project_id "
            "WHERE p.phone_number = ? AND pt.due_date < datetime('now') AND pt.status != 'done' "
            "ORDER BY pt.due_date",
            (phone_number,),
        )
        rows = await cursor.fetchall()
        return [
            ProjectTask(
                id=r[0],
                project_id=r[1],
                title=r[2],
                description=r[3],
                status=r[4],
                priority=r[5],
                due_date=r[6],
                created_at=r[7],
                updated_at=r[8],
            )
            for r in rows
        ]

    # --- Cron Jobs ---

    async def create_cron_job(
        self,
        phone_number: str,
        cron_expr: str,
        message: str,
        timezone: str = "UTC",
    ) -> int:
        """Persist a user cron job and return its ID."""
        # Enforce max 20 active crons per user
        cursor = await self._conn.execute(
            "SELECT COUNT(*) FROM user_cron_jobs WHERE phone_number = ? AND active = 1",
            (phone_number,),
        )
        row = await cursor.fetchone()
        if row and row[0] >= 20:
            raise ValueError("Maximum of 20 active cron jobs per user reached.")
        cursor = await self._conn.execute(
            "INSERT INTO user_cron_jobs (phone_number, cron_expr, message, timezone) VALUES (?, ?, ?, ?)",
            (phone_number, cron_expr, message, timezone),
        )
        await self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def list_cron_jobs(self, phone_number: str) -> list[dict]:
        """Return all active cron jobs for a user."""
        cursor = await self._conn.execute(
            "SELECT id, cron_expr, message, timezone, created_at FROM user_cron_jobs "
            "WHERE phone_number = ? AND active = 1 ORDER BY id",
            (phone_number,),
        )
        rows = await cursor.fetchall()
        return [
            {"id": r[0], "cron_expr": r[1], "message": r[2], "timezone": r[3], "created_at": r[4]}
            for r in rows
        ]

    async def delete_cron_job(self, job_id: int, phone_number: str) -> bool:
        """Soft-delete a cron job (mark inactive). Returns True if found and deleted."""
        cursor = await self._conn.execute(
            "UPDATE user_cron_jobs SET active = 0 WHERE id = ? AND phone_number = ? AND active = 1",
            (job_id, phone_number),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def get_active_cron_jobs(self) -> list[dict]:
        """Return all active cron jobs across all users (for scheduler restore at boot)."""
        cursor = await self._conn.execute(
            "SELECT id, phone_number, cron_expr, message, timezone FROM user_cron_jobs WHERE active = 1 ORDER BY id",
        )
        rows = await cursor.fetchall()
        return [
            {"id": r[0], "phone_number": r[1], "cron_expr": r[2], "message": r[3], "timezone": r[4]}
            for r in rows
        ]

    # --- Debug / Planner-Orchestrator ---

    async def get_traces_by_phone(self, phone_number: str, limit: int = 10) -> list[dict]:
        """Return recent traces for a phone number with aggregated scores."""
        cursor = await self._conn.execute(
            "SELECT t.id, t.input_text, t.output_text, t.message_type, t.status, "
            "t.started_at, t.completed_at, "
            "MIN(s.value) AS min_score, AVG(s.value) AS avg_score, COUNT(s.id) AS score_count "
            "FROM traces t "
            "LEFT JOIN trace_scores s ON s.trace_id = t.id "
            "WHERE t.phone_number = ? "
            "GROUP BY t.id "
            "ORDER BY t.started_at DESC LIMIT ?",
            (phone_number, limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "input_text": r[1],
                "output_text": r[2],
                "message_type": r[3],
                "status": r[4],
                "started_at": r[5],
                "completed_at": r[6],
                "min_score": r[7],
                "avg_score": r[8],
                "score_count": r[9],
            }
            for r in rows
        ]

    async def get_trace_tool_calls(self, trace_id: str) -> list[dict]:
        """Return tool call spans for a trace with full input/output."""
        cursor = await self._conn.execute(
            "SELECT id, name, input, output, status, started_at, latency_ms "
            "FROM trace_spans WHERE trace_id = ? AND kind = 'tool' ORDER BY started_at",
            (trace_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "input": json.loads(r[2]) if r[2] else None,
                "output": json.loads(r[3]) if r[3] else None,
                "status": r[4],
                "started_at": r[5],
                "latency_ms": r[6],
            }
            for r in rows
        ]

    async def get_conversation_transcript(self, phone_number: str, limit: int = 20) -> list[dict]:
        """Reconstruct a readable conversation transcript from messages table."""
        conv_id = await self.get_conversation_id(phone_number)
        if conv_id is None:
            return []
        cursor = await self._conn.execute(
            "SELECT role, content, created_at FROM messages "
            "WHERE conversation_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (conv_id, limit),
        )
        rows = list(await cursor.fetchall())
        return [
            {"role": r[0], "content": r[1], "timestamp": r[2]}
            for r in reversed(rows)  # chronological order
        ]

    # --- Metrics Hardening (Plan 38) ---

    async def get_latency_percentiles(
        self, span_name: str | None = None, days: int = 7
    ) -> list[dict]:
        """Return p50/p95/p99 latency per span name for the last N days.

        If span_name is None, returns stats for the most frequent span names (top 10).
        Percentiles computed in Python (SQLite has no PERCENTILE_DISC).
        """
        if span_name:
            cursor = await self._conn.execute(
                """
                SELECT name, latency_ms FROM trace_spans
                WHERE name = ? AND latency_ms IS NOT NULL
                  AND started_at >= datetime('now', ? || ' days')
                ORDER BY latency_ms ASC
                """,
                (span_name, f"-{days}"),
            )
            rows = await cursor.fetchall()
            if not rows:
                return []
            return [_compute_percentiles(span_name, [r[1] for r in rows])]
        else:
            # Top frequent span names
            cursor = await self._conn.execute(
                """
                SELECT name, COUNT(*) AS n FROM trace_spans
                WHERE latency_ms IS NOT NULL
                  AND started_at >= datetime('now', ? || ' days')
                GROUP BY name
                ORDER BY n DESC
                LIMIT 10
                """,
                (f"-{days}",),
            )
            name_rows = await cursor.fetchall()
            results = []
            for sname, _ in name_rows:
                cursor2 = await self._conn.execute(
                    """
                    SELECT latency_ms FROM trace_spans
                    WHERE name = ? AND latency_ms IS NOT NULL
                      AND started_at >= datetime('now', ? || ' days')
                    ORDER BY latency_ms ASC
                    """,
                    (sname, f"-{days}"),
                )
                lat_rows = await cursor2.fetchall()
                results.append(_compute_percentiles(sname, [r[0] for r in lat_rows]))
            return results

    async def get_search_hit_rate(self, days: int = 7) -> list[dict]:
        """Return distribution of semantic search modes from span metadata.

        Requires that search_stats were stored in trace_spans.metadata_json under span
        name 'phase_b'. Returns empty list if no data is available.
        """
        cursor = await self._conn.execute(
            """
            SELECT
                json_extract(metadata_json, '$.search_mode') AS mode,
                COUNT(*) AS n,
                AVG(json_extract(metadata_json, '$.memories_retrieved')) AS avg_retrieved,
                AVG(json_extract(metadata_json, '$.memories_passed')) AS avg_passed
            FROM trace_spans
            WHERE name = 'phase_b'
              AND started_at >= datetime('now', ? || ' days')
              AND metadata_json IS NOT NULL
              AND json_extract(metadata_json, '$.search_mode') IS NOT NULL
            GROUP BY mode
            ORDER BY n DESC
            """,
            (f"-{days}",),
        )
        rows = await cursor.fetchall()
        return [
            {
                "mode": r[0],
                "n": r[1],
                "avg_retrieved": round(r[2], 1) if r[2] is not None else 0.0,
                "avg_passed": round(r[3], 1) if r[3] is not None else 0.0,
            }
            for r in rows
        ]
