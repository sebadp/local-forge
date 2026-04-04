"""Auto-Dream: Background memory consolidation inspired by Claude Code autoDream.

4-phase consolidation:
  Phase 1 — ORIENT: Read current memories and MEMORY.md index
  Phase 2 — GATHER: Read daily logs and recent messages since last dream
  Phase 3 — CONSOLIDATE: Merge, dedup, update, create memories (JSON actions)
  Phase 4 — PRUNE: Keep MEMORY.md index concise (max 40 entries)

Runs as a scheduled APScheduler job, gated by time + activity + lock.
Best-effort: errors are logged, never block the main pipeline.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.memory.consolidation_lock import (
    read_last_consolidated_at,
    release_lock,
    write_last_consolidated_at,
)
from app.models import ChatMessage

if TYPE_CHECKING:
    from pathlib import Path

    from app.database.repository import Repository
    from app.llm.client import OllamaClient
    from app.memory.daily_log import DailyLog
    from app.memory.markdown import MemoryFile

logger = logging.getLogger(__name__)


@dataclass
class DreamResult:
    """Metrics from a dream consolidation run."""

    removed: int = 0
    updated: int = 0
    created: int = 0
    pruned_from_index: int = 0
    error: str | None = None


_DREAM_PROMPT = """\
You are performing a memory consolidation ("dream"). Your job is to review \
the user's memories and daily logs, then clean up the memory system.

## Phase 1 — ORIENT: Current State

Current memories (ID — category — content):
{memories}

## Phase 2 — GATHER: Recent Activity

Daily logs since last consolidation:
{daily_logs}

Today's date: {today}

## Phase 3 — CONSOLIDATE

Analyze the memories and daily logs. Produce a JSON object with actions:

Actions you can take:
- "remove": delete a memory that is duplicate, obsolete, or superseded
- "update": modify a memory's content (fix facts, convert relative dates to absolute, merge two into one)
- "create": add a new memory extracted from daily logs that is worth persisting long-term

Rules:
- Do NOT remove memories that are still relevant and unique
- Convert relative dates ("yesterday", "last week") to absolute dates using today's date
- If two memories say the same thing, keep the more complete one and remove the other
- If a daily log mentions something repeatedly, extract it as a permanent memory
- If a memory contradicts recent activity in daily logs, update it
- Prefer updating over remove+create when the core fact is the same

## Phase 4 — PRUNE INDEX

Also include a "keep_ids" list: the IDs of memories that should appear in the \
MEMORY.md index (max 40 most important). Memories not in keep_ids will still \
exist in the database but won't be in the quick-reference index.

Return ONLY this JSON (no other text):
```json
{{
  "actions": [
    {{"type": "remove", "id": 12, "reason": "superseded by #5"}},
    {{"type": "update", "id": 5, "new_content": "Updated fact here"}},
    {{"type": "create", "content": "New fact extracted from logs", "category": "general"}}
  ],
  "keep_ids": [1, 3, 5, 7, 10]
}}
```

If nothing needs changing: {{"actions": [], "keep_ids": [list all current IDs]}}
"""


def _format_memories_for_dream(memories: list) -> str:
    """Format memories with ID, category, and content for the dream prompt."""
    if not memories:
        return "(no memories yet)"
    lines = []
    for m in memories:
        cat = f"[{m.category}]" if m.category else "[general]"
        lines.append(f"[{m.id}] {cat} {m.content}")
    return "\n".join(lines)


def _parse_dream_response(response: str) -> dict:
    """Parse the LLM JSON response, tolerating markdown fences."""
    text = response.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(line for line in lines if not line.strip().startswith("```")).strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting JSON object
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    return {"actions": [], "keep_ids": []}


async def run_dream(
    repository: Repository,
    ollama_client: OllamaClient,
    memory_file: MemoryFile,
    daily_log: DailyLog,
    data_dir: Path,
) -> DreamResult:
    """Execute the 4-phase dream consolidation.

    Returns DreamResult with metrics. Best-effort: catches all errors.
    """
    result = DreamResult()

    try:
        # Load current state
        memories = await repository.list_memories()
        if not memories:
            logger.info("dream.skipped: no memories to consolidate")
            return result

        # Load daily logs since last dream (or last 7 days as fallback)
        last = read_last_consolidated_at(data_dir)
        days_since = 7
        if last:
            days_since = max(1, int((datetime.now(UTC) - last).total_seconds() / 86400) + 1)
            days_since = min(days_since, 14)  # cap at 14 days

        logs_content = await daily_log.load_recent(days=days_since)
        if not logs_content:
            logs_content = "(no daily logs found)"

        # Build prompt
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        prompt = _DREAM_PROMPT.format(
            memories=_format_memories_for_dream(memories),
            daily_logs=logs_content,
            today=today,
        )

        # Single LLM call
        from app.tracing.context import get_current_trace

        trace = get_current_trace()
        messages = [ChatMessage(role="user", content=prompt)]

        if trace:
            async with trace.span("dream:consolidation", kind="generation") as span:
                span.set_input({"memory_count": len(memories), "days_scanned": days_since})
                response = await ollama_client.chat(messages, think=False)
                span.set_output({"response_length": len(response) if response else 0})
        else:
            response = await ollama_client.chat(messages, think=False)

        if not response:
            result.error = "Empty LLM response"
            return result

        # Parse response
        data = _parse_dream_response(response)
        actions = data.get("actions", [])
        keep_ids = data.get("keep_ids", [])

        # Validate IDs
        valid_ids = {m.id for m in memories}
        memory_by_id = {m.id: m for m in memories}

        # Execute actions
        for action in actions:
            action_type = action.get("type")
            action_id = action.get("id")

            if action_type == "remove" and isinstance(action_id, int) and action_id in valid_ids:
                memory = memory_by_id[action_id]
                success = await repository.remove_memory(memory.content)
                if success:
                    result.removed += 1
                    valid_ids.discard(action_id)
                    logger.info(
                        "dream.remove: [%d] %s (reason: %s)",
                        action_id,
                        memory.content[:60],
                        action.get("reason", "n/a"),
                    )

            elif action_type == "update" and isinstance(action_id, int) and action_id in valid_ids:
                new_content = action.get("new_content", "")
                if new_content:
                    memory = memory_by_id[action_id]
                    # Remove old + create updated
                    await repository.remove_memory(memory.content)
                    await repository.save_memory(new_content, category=memory.category or "general")
                    result.updated += 1
                    logger.info("dream.update: [%d] -> %s", action_id, new_content[:60])

            elif action_type == "create":
                content = action.get("content", "")
                category = action.get("category", "general")
                if content:
                    await repository.save_memory(content, category=category)
                    result.created += 1
                    logger.info("dream.create: [%s] %s", category, content[:60])

        # Prune index: rebuild MEMORY.md with only keep_ids (+ any newly created)
        updated_memories = await repository.list_memories()
        if keep_ids and len(keep_ids) < len(updated_memories):
            keep_set = set(keep_ids)
            indexed_memories = [m for m in updated_memories if m.id in keep_set]
            # Also include recently created memories (they won't have IDs in keep_ids)
            new_ids = {m.id for m in updated_memories} - {m.id for m in memories}
            for m in updated_memories:
                if m.id in new_ids and m not in indexed_memories:
                    indexed_memories.append(m)
            result.pruned_from_index = len(updated_memories) - len(indexed_memories)
            await memory_file.sync(indexed_memories)
        else:
            await memory_file.sync(updated_memories)

        # Persist timestamp
        write_last_consolidated_at(data_dir)
        release_lock(data_dir)

        logger.info(
            "dream.completed",
            extra={
                "removed": result.removed,
                "updated": result.updated,
                "created": result.created,
                "pruned_from_index": result.pruned_from_index,
            },
        )

    except Exception as e:
        result.error = str(e)
        logger.exception("dream.failed: %s", e)
        release_lock(data_dir)

    return result
