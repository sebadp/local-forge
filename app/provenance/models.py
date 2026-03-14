"""Constants and data models for provenance tracking."""

from __future__ import annotations

from dataclasses import dataclass


# Actor constants — who caused the mutation
class Actor:
    USER = "user"  # /remember, /forget commands
    LLM_FLUSH = "llm_flush"  # flush_to_memory (summarizer)
    LLM_CONSOLIDATOR = "llm_consolidator"  # consolidate_memories
    TOOL = "tool"  # tool execution (save_note, etc.)
    AGENT = "agent"  # agent session results
    SYSTEM = "system"  # self-correction, background tasks
    FILE_SYNC = "file_sync"  # MEMORY.md watcher sync


# Action constants
class Action:
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    MERGE = "MERGE"


# Entity type constants
class EntityType:
    MEMORY = "memory"
    NOTE = "note"
    PROJECT = "project"
    PROJECT_NOTE = "project_note"
    PROJECT_TASK = "project_task"


@dataclass
class AuditEntry:
    """A single audit log entry."""

    id: int
    entity_type: str
    entity_id: int
    action: str
    actor: str
    source_trace_id: str | None
    before_snapshot: str | None
    after_snapshot: str | None
    metadata_json: str
    created_at: str
