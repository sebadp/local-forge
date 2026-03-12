"""Lineage query tool — lets the LLM trace data origins."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app.skills.registry import SkillRegistry

if TYPE_CHECKING:
    from app.provenance.audit import AuditLogger

logger = logging.getLogger(__name__)


def register(registry: SkillRegistry, audit_logger: AuditLogger) -> None:
    async def trace_data_origin(
        entity_type: str,
        entity_id: int,
        **_extra: object,
    ) -> str:
        """Trace the origin and mutation history of a memory, note, or project."""
        valid_types = {"memory", "note", "project", "project_note", "project_task"}
        if entity_type not in valid_types:
            return f"Invalid entity_type '{entity_type}'. Use: {', '.join(sorted(valid_types))}"

        entries = await audit_logger.get_audit_log(entity_type, entity_id, limit=20)
        if not entries:
            return f"No provenance data found for {entity_type} #{entity_id}."

        lines = [f"Provenance for {entity_type} #{entity_id} ({len(entries)} events):"]
        for e in reversed(entries):  # chronological order
            meta = json.loads(e.metadata_json) if e.metadata_json != "{}" else None
            meta_str = f" [{meta}]" if meta else ""
            trace_str = f" (trace: {e.source_trace_id[:8]}...)" if e.source_trace_id else ""
            lines.append(f"  [{e.created_at}] {e.action} by {e.actor}{trace_str}{meta_str}")
            if e.before_snapshot:
                lines.append(f"    before: {e.before_snapshot[:120]}")
            if e.after_snapshot:
                lines.append(f"    after: {e.after_snapshot[:120]}")

        # Include memory versions if it's a memory
        if entity_type == "memory":
            versions = await audit_logger.get_memory_versions(entity_id)
            if versions:
                lines.append(f"\nVersion history ({len(versions)} versions):")
                for v in versions:
                    lines.append(
                        f"  v{v['version']} [{v['created_at']}] by {v['actor']}: "
                        f"{v['content'][:100]}"
                    )

        return "\n".join(lines)

    async def get_entity_history(
        entity_type: str = "",
        actor: str = "",
        limit: int = 20,
        **_extra: object,
    ) -> str:
        """Browse recent audit log entries with optional filters."""
        entries = await audit_logger.get_entity_history(
            entity_type=entity_type or None,
            actor=actor or None,
            limit=min(limit, 50),
        )
        if not entries:
            filters = []
            if entity_type:
                filters.append(f"type={entity_type}")
            if actor:
                filters.append(f"actor={actor}")
            filter_str = f" (filters: {', '.join(filters)})" if filters else ""
            return f"No audit entries found{filter_str}."

        lines = [f"Recent mutations ({len(entries)} entries):"]
        for e in entries:
            lines.append(
                f"  [{e.created_at}] {e.action} {e.entity_type}#{e.entity_id} by {e.actor}"
            )
        return "\n".join(lines)

    registry.register_tool(
        name="trace_data_origin",
        description=(
            "Trace the origin and mutation history of a memory, note, or project. "
            "Shows who created it, who modified it, and when."
        ),
        parameters={
            "type": "object",
            "properties": {
                "entity_type": {
                    "type": "string",
                    "description": "Type: memory, note, project, project_note, project_task",
                },
                "entity_id": {
                    "type": "integer",
                    "description": "ID of the entity to trace",
                },
            },
            "required": ["entity_type", "entity_id"],
        },
        handler=trace_data_origin,
        skill_name="provenance",
    )

    registry.register_tool(
        name="get_entity_history",
        description=(
            "Browse recent data mutations (creations, updates, deletions). "
            "Optional filters by entity_type (memory, note, project) and actor "
            "(user, llm_flush, llm_consolidator, tool, agent, system, file_sync)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "entity_type": {
                    "type": "string",
                    "description": "Filter by type (memory, note, project, etc.)",
                },
                "actor": {
                    "type": "string",
                    "description": "Filter by actor (user, llm_flush, tool, etc.)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max entries to return (default 20, max 50)",
                },
            },
        },
        handler=get_entity_history,
        skill_name="provenance",
    )
