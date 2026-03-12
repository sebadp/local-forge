"""Ontology data models: Entity, Relation, GraphResult."""

from __future__ import annotations

from dataclasses import dataclass, field

ENTITY_TYPES = ("memory", "note", "project", "task", "conversation", "topic", "person")

RELATION_TYPES = (
    "extracted_from",  # Memory → Conversation
    "mentioned_in",  # Entity → Message
    "belongs_to",  # Task → Project
    "related_to",  # Entity → Entity (semantic)
    "about_topic",  # Entity → Topic
    "created_by",  # Entity → Person
    "supersedes",  # Memory → Memory
    "references",  # Note → Project
    "derived_from",  # Entity → Trace
)


@dataclass
class Entity:
    id: str
    entity_type: str
    ref_id: str
    name: str
    metadata: dict = field(default_factory=dict)
    created_at: str = ""


@dataclass
class Relation:
    id: int
    source_id: str
    relation_type: str
    target_id: str
    confidence: float = 1.0
    source_trace_id: str | None = None
    metadata: dict = field(default_factory=dict)
    created_at: str = ""


@dataclass
class GraphResult:
    """Result of a graph traversal: root entity + related entities grouped by type."""

    root: Entity | None
    related: dict[str, list[Entity]] = field(default_factory=dict)
    # related = {"memory": [...], "note": [...], "project": [...]}

    def total(self) -> int:
        return sum(len(v) for v in self.related.values())

    def to_text(self, budget_chars: int = 2000) -> str:
        """Format graph result as text, respecting character budget."""
        lines = []
        used = 0
        for etype, entities in self.related.items():
            if not entities:
                continue
            header = f"\n[{etype}s]"
            lines.append(header)
            used += len(header)
            for e in entities:
                entry = f"- {e.name[:200]}"
                if used + len(entry) > budget_chars:
                    lines.append("... (truncated)")
                    return "\n".join(lines)
                lines.append(entry)
                used += len(entry)
        return "\n".join(lines)
