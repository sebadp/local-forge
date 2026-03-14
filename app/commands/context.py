from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.database.repository import Repository
    from app.memory.markdown import MemoryFile


@dataclass
class CommandContext:
    repository: Repository
    memory_file: MemoryFile
    phone_number: str
    registry: Any = field(default=None, repr=False)
    skill_registry: Any = field(default=None, repr=False)
    mcp_manager: Any = field(default=None, repr=False)
    ollama_client: Any = field(default=None, repr=False)
    daily_log: Any = field(default=None, repr=False)
    embed_model: str | None = field(default=None, repr=False)
    wa_client: Any = field(default=None, repr=False)
    settings: Any = field(default=None, repr=False)
    trace_recorder: Any = field(default=None, repr=False)
    audit_logger: Any = field(default=None, repr=False)
