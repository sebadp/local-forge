"""Tests for data provenance & lineage (Plan 44)."""

from __future__ import annotations

import aiosqlite

from app.database.db import init_db
from app.provenance.audit import AuditLogger
from app.provenance.models import Action, Actor, EntityType


async def _make_db() -> tuple[aiosqlite.Connection, bool]:
    conn, vec = await init_db(":memory:")
    return conn, vec


# --- AuditLogger core ---


async def test_log_mutation_and_retrieve():
    conn, _ = await _make_db()
    al = AuditLogger(conn)

    await al.log_mutation(
        EntityType.MEMORY,
        1,
        Action.CREATE,
        Actor.USER,
        after_snapshot="test memory content",
    )
    entries = await al.get_audit_log(EntityType.MEMORY, 1)
    assert len(entries) == 1
    assert entries[0].action == Action.CREATE
    assert entries[0].actor == Actor.USER
    assert entries[0].after_snapshot == "test memory content"
    await conn.close()


async def test_log_mutation_disabled():
    conn, _ = await _make_db()
    al = AuditLogger(conn, enabled=False)

    await al.log_mutation(EntityType.MEMORY, 1, Action.CREATE, Actor.USER)
    entries = await al.get_audit_log(EntityType.MEMORY, 1)
    assert len(entries) == 0
    await conn.close()


async def test_multiple_mutations():
    conn, _ = await _make_db()
    al = AuditLogger(conn)

    await al.log_mutation(EntityType.MEMORY, 1, Action.CREATE, Actor.USER, after_snapshot="v1")
    await al.log_mutation(EntityType.MEMORY, 1, Action.UPDATE, Actor.LLM_FLUSH, after_snapshot="v2")
    await al.log_mutation(EntityType.MEMORY, 1, Action.DELETE, Actor.LLM_CONSOLIDATOR)

    entries = await al.get_audit_log(EntityType.MEMORY, 1)
    assert len(entries) == 3
    # Most recent first
    assert entries[0].action == Action.DELETE
    assert entries[1].action == Action.UPDATE
    assert entries[2].action == Action.CREATE
    await conn.close()


async def test_log_mutation_with_metadata():
    conn, _ = await _make_db()
    al = AuditLogger(conn)

    await al.log_mutation(
        EntityType.MEMORY,
        5,
        Action.MERGE,
        Actor.LLM_CONSOLIDATOR,
        metadata={"merge_remove_ids": [3, 4]},
    )
    entries = await al.get_audit_log(EntityType.MEMORY, 5)
    assert len(entries) == 1
    assert '"merge_remove_ids"' in entries[0].metadata_json
    await conn.close()


async def test_log_mutation_with_trace_id():
    conn, _ = await _make_db()
    al = AuditLogger(conn)

    await al.log_mutation(
        EntityType.NOTE,
        10,
        Action.CREATE,
        Actor.TOOL,
        source_trace_id="abc-123-trace",
        after_snapshot="note content",
    )
    entries = await al.get_audit_log(EntityType.NOTE, 10)
    assert entries[0].source_trace_id == "abc-123-trace"
    await conn.close()


# --- Memory versioning ---


async def test_version_memory():
    conn, _ = await _make_db()
    al = AuditLogger(conn)

    await al.version_memory(1, "first version", Actor.USER)
    await al.version_memory(1, "second version", Actor.LLM_FLUSH)

    versions = await al.get_memory_versions(1)
    assert len(versions) == 2
    assert versions[0]["version"] == 1
    assert versions[0]["content"] == "first version"
    assert versions[0]["actor"] == Actor.USER
    assert versions[1]["version"] == 2
    assert versions[1]["content"] == "second version"
    await conn.close()


async def test_version_memory_disabled():
    conn, _ = await _make_db()
    al = AuditLogger(conn, enabled=False)

    await al.version_memory(1, "content", Actor.USER)
    versions = await al.get_memory_versions(1)
    assert len(versions) == 0
    await conn.close()


# --- Entity history ---


async def test_get_entity_history_all():
    conn, _ = await _make_db()
    al = AuditLogger(conn)

    await al.log_mutation(EntityType.MEMORY, 1, Action.CREATE, Actor.USER)
    await al.log_mutation(EntityType.NOTE, 1, Action.CREATE, Actor.TOOL)
    await al.log_mutation(EntityType.MEMORY, 2, Action.CREATE, Actor.LLM_FLUSH)

    entries = await al.get_entity_history()
    assert len(entries) == 3
    await conn.close()


async def test_get_entity_history_filtered_by_type():
    conn, _ = await _make_db()
    al = AuditLogger(conn)

    await al.log_mutation(EntityType.MEMORY, 1, Action.CREATE, Actor.USER)
    await al.log_mutation(EntityType.NOTE, 1, Action.CREATE, Actor.TOOL)

    entries = await al.get_entity_history(entity_type=EntityType.MEMORY)
    assert len(entries) == 1
    assert entries[0].entity_type == EntityType.MEMORY
    await conn.close()


async def test_get_entity_history_filtered_by_actor():
    conn, _ = await _make_db()
    al = AuditLogger(conn)

    await al.log_mutation(EntityType.MEMORY, 1, Action.CREATE, Actor.USER)
    await al.log_mutation(EntityType.MEMORY, 2, Action.CREATE, Actor.LLM_FLUSH)

    entries = await al.get_entity_history(actor=Actor.LLM_FLUSH)
    assert len(entries) == 1
    assert entries[0].actor == Actor.LLM_FLUSH
    await conn.close()


# --- Cleanup ---


async def test_cleanup_old_entries():
    conn, _ = await _make_db()
    al = AuditLogger(conn)

    # Insert with a date far in the past
    await conn.execute(
        "INSERT INTO entity_audit_log "
        "(entity_type, entity_id, action, actor, created_at) "
        "VALUES (?, ?, ?, ?, datetime('now', '-100 days'))",
        (EntityType.MEMORY, 1, Action.CREATE, Actor.USER),
    )
    await conn.execute(
        "INSERT INTO entity_audit_log (entity_type, entity_id, action, actor) VALUES (?, ?, ?, ?)",
        (EntityType.MEMORY, 2, Action.CREATE, Actor.USER),
    )
    await conn.commit()

    deleted = await al.cleanup_old_entries(days=90)
    assert deleted == 1

    entries = await al.get_entity_history()
    assert len(entries) == 1
    assert entries[0].entity_id == "2"
    await conn.close()


# --- source_trace_id in memories/notes ---


async def test_add_memory_with_source_trace_id():
    conn, _ = await _make_db()
    from app.database.repository import Repository

    repo = Repository(conn)
    mem_id = await repo.add_memory("test fact", source_trace_id="trace-123")
    assert mem_id > 0

    cursor = await conn.execute(
        "SELECT source_trace_id FROM memories WHERE id = ?",
        (mem_id,),
    )
    row = await cursor.fetchone()
    assert row[0] == "trace-123"  # type: ignore[index]
    await conn.close()


async def test_save_note_with_source_trace_id():
    conn, _ = await _make_db()
    from app.database.repository import Repository

    repo = Repository(conn)
    note_id = await repo.save_note("title", "content", source_trace_id="trace-456")
    assert note_id > 0

    cursor = await conn.execute(
        "SELECT source_trace_id FROM notes WHERE id = ?",
        (note_id,),
    )
    row = await cursor.fetchone()
    assert row[0] == "trace-456"  # type: ignore[index]
    await conn.close()


# --- Lineage tool ---


async def test_lineage_tool_trace_data_origin():
    conn, _ = await _make_db()
    al = AuditLogger(conn)

    await al.log_mutation(
        EntityType.MEMORY,
        42,
        Action.CREATE,
        Actor.USER,
        after_snapshot="my github is foo",
    )
    await al.log_mutation(
        EntityType.MEMORY,
        42,
        Action.UPDATE,
        Actor.LLM_CONSOLIDATOR,
        before_snapshot="my github is foo",
        after_snapshot="my github username is foo",
    )
    await al.version_memory(42, "my github is foo", Actor.USER)
    await al.version_memory(42, "my github username is foo", Actor.LLM_CONSOLIDATOR)

    from app.skills.registry import SkillRegistry

    sr = SkillRegistry(skills_dir="skills")

    from app.provenance.lineage_tool import register

    register(sr, al)

    tool = sr.get_tool("trace_data_origin")
    assert tool is not None
    result = await tool.handler(entity_type="memory", entity_id=42)
    assert "CREATE by user" in result
    assert "UPDATE by llm_consolidator" in result
    assert "Version history (2 versions)" in result
    await conn.close()


async def test_lineage_tool_no_data():
    conn, _ = await _make_db()
    al = AuditLogger(conn)

    from app.skills.registry import SkillRegistry

    sr = SkillRegistry(skills_dir="skills")

    from app.provenance.lineage_tool import register

    register(sr, al)

    tool = sr.get_tool("trace_data_origin")
    result = await tool.handler(entity_type="memory", entity_id=999)
    assert "No provenance data found" in result
    await conn.close()


async def test_lineage_tool_invalid_type():
    conn, _ = await _make_db()
    al = AuditLogger(conn)

    from app.skills.registry import SkillRegistry

    sr = SkillRegistry(skills_dir="skills")

    from app.provenance.lineage_tool import register

    register(sr, al)

    tool = sr.get_tool("trace_data_origin")
    result = await tool.handler(entity_type="invalid", entity_id=1)
    assert "Invalid entity_type" in result
    await conn.close()


async def test_entity_history_tool():
    conn, _ = await _make_db()
    al = AuditLogger(conn)

    await al.log_mutation(EntityType.MEMORY, 1, Action.CREATE, Actor.USER)
    await al.log_mutation(EntityType.NOTE, 1, Action.CREATE, Actor.TOOL)

    from app.skills.registry import SkillRegistry

    sr = SkillRegistry(skills_dir="skills")

    from app.provenance.lineage_tool import register

    register(sr, al)

    tool = sr.get_tool("get_entity_history")
    result = await tool.handler()
    assert "2 entries" in result
    assert "CREATE memory#1" in result
    assert "CREATE note#1" in result
    await conn.close()


# --- Best-effort behavior ---


async def test_audit_logger_does_not_raise():
    """AuditLogger should never propagate exceptions."""
    conn, _ = await _make_db()
    al = AuditLogger(conn)
    await conn.close()  # Close connection to force errors

    # These should NOT raise
    await al.log_mutation(EntityType.MEMORY, 1, Action.CREATE, Actor.USER)
    await al.version_memory(1, "content", Actor.USER)
    entries = await al.get_audit_log(EntityType.MEMORY, 1)
    assert entries == []
    versions = await al.get_memory_versions(1)
    assert versions == []
