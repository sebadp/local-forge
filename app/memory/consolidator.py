from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app.database.repository import Repository
from app.llm.client import OllamaClient
from app.models import ChatMessage

if TYPE_CHECKING:
    from app.memory.markdown import MemoryFile

logger = logging.getLogger(__name__)

MIN_MEMORIES = 8

CONSOLIDATE_PROMPT = (
    "Review these user memories. Your job:\n"
    "1. Identify duplicates or near-duplicates → keep the better one\n"
    "2. Identify contradictions → keep the most recent one (higher ID = more recent)\n"
    "3. Do NOT remove anything that isn't clearly duplicate or contradicted\n\n"
    "Current memories (oldest first):\n{memories}\n\n"
    'Return JSON: {{"remove_ids": [id1, id2]}}\n'
    'If nothing to remove: {{"remove_ids": []}}'
)


def _format_memories(memories: list) -> str:
    """Format memories with IDs for the consolidation prompt."""
    lines = []
    for m in memories:
        lines.append(f"[{m.id}] {m.content}")
    return "\n".join(lines)


async def consolidate_memories(
    repository: Repository,
    ollama_client: OllamaClient,
    memory_file: MemoryFile,
    min_memories: int = MIN_MEMORIES,
) -> int:
    """Consolidate memories by removing duplicates and contradictions.

    Returns the number of memories removed.
    """
    memories = await repository.list_memories()
    if len(memories) < min_memories:
        return 0

    try:
        from app.eval.prompt_manager import get_active_prompt

        consolidate_template = await get_active_prompt(
            "consolidator", repository, CONSOLIDATE_PROMPT
        )
    except Exception:
        consolidate_template = CONSOLIDATE_PROMPT

    prompt = consolidate_template.format(memories=_format_memories(memories))
    messages = [ChatMessage(role="user", content=prompt)]
    response = await ollama_client.chat(messages, think=False)

    # Parse JSON response
    try:
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            text = "\n".join(lines)
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to parse consolidation response as JSON: %s", response[:200])
        return 0

    remove_ids = data.get("remove_ids", [])
    if not remove_ids:
        return 0

    # Validate IDs: only remove IDs that actually exist in current memories
    valid_ids = {m.id for m in memories}
    remove_ids = [rid for rid in remove_ids if isinstance(rid, int) and rid in valid_ids]

    removed_count = 0
    for memory_id in remove_ids:
        # Find the memory content for logging
        memory = next((m for m in memories if m.id == memory_id), None)
        if memory:
            success = await repository.remove_memory(memory.content)
            if success:
                removed_count += 1
                logger.info("Consolidated memory [%d]: %s", memory_id, memory.content[:80])
                # Audit log (best-effort)
                try:
                    from app.provenance.context import get_audit_logger
                    from app.provenance.models import Action, Actor, EntityType

                    _al = get_audit_logger()
                    if _al:
                        await _al.log_mutation(
                            EntityType.MEMORY,
                            memory_id,
                            Action.MERGE,
                            Actor.LLM_CONSOLIDATOR,
                            before_snapshot=memory.content,
                            metadata={"merge_remove_ids": remove_ids},
                        )
                except Exception:
                    pass
                # Remove embedding (best-effort)
                try:
                    from app.embeddings.indexer import remove_memory_embedding

                    await remove_memory_embedding(memory_id, repository)
                except Exception:
                    logger.warning(
                        "Failed to delete embedding for consolidated memory %d", memory_id
                    )

    # Sync MEMORY.md if any were removed
    if removed_count > 0:
        updated_memories = await repository.list_memories()
        await memory_file.sync(updated_memories)

    logger.info("Memory consolidation: removed %d of %d memories", removed_count, len(memories))
    return removed_count
