from __future__ import annotations

import difflib
import json
import logging
from typing import TYPE_CHECKING

from app.database.repository import Repository
from app.llm.client import OllamaClient
from app.models import ChatMessage

if TYPE_CHECKING:
    from app.memory.daily_log import DailyLog
    from app.memory.markdown import MemoryFile

logger = logging.getLogger(__name__)

SUMMARIZE_PROMPT = (
    "Summarize the following conversation in 2-3 short paragraphs, "
    "capturing the main topics, decisions, and important details. "
    "Write the summary in the same language the conversation is in."
)

FLUSH_PROMPT = (
    "Review this conversation fragment. Extract ONLY what's worth remembering long-term.\n\n"
    "Existing memories (do NOT repeat these):\n{existing_memories}\n\n"
    "Conversation:\n{conversation}\n\n"
    'Respond in JSON only:\n{{"facts": ["new stable fact 1"], "events": ["notable event 1"]}}\n'
    'If nothing new, respond: {{"facts": [], "events": []}}'
)

DEDUP_THRESHOLD = 0.8


def _is_duplicate(new_fact: str, existing: list[str]) -> bool:
    """Check if a fact is too similar to any existing memory."""
    for existing_fact in existing:
        ratio = difflib.SequenceMatcher(None, new_fact.lower(), existing_fact.lower()).ratio()
        if ratio > DEDUP_THRESHOLD:
            return True
    return False


async def flush_to_memory(
    old_messages: list[ChatMessage],
    repository: Repository,
    ollama_client: OllamaClient,
    daily_log: DailyLog,
    memory_file: MemoryFile,
    embed_model: str | None = None,
) -> int:
    """Extract facts and events from messages before they are deleted.

    Returns the number of new facts added.
    """
    existing_memories = await repository.get_active_memories()

    # Format conversation
    conversation_lines = []
    for msg in old_messages:
        conversation_lines.append(f"{msg.role}: {msg.content}")
    conversation_text = "\n".join(conversation_lines)

    # Format existing memories for the prompt
    if existing_memories:
        memories_text = "\n".join(f"- {m}" for m in existing_memories)
    else:
        memories_text = "(none)"

    try:
        from app.eval.prompt_manager import get_active_prompt

        flush_template = await get_active_prompt("flush_to_memory", repository, FLUSH_PROMPT)
    except Exception:
        flush_template = FLUSH_PROMPT

    prompt = flush_template.format(
        existing_memories=memories_text,
        conversation=conversation_text,
    )

    messages = [ChatMessage(role="user", content=prompt)]
    response = await ollama_client.chat(messages, think=False)

    # Parse JSON response
    try:
        # Strip markdown code fences if present
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first line (```json) and last line (```)
            lines = [line for line in lines if not line.strip().startswith("```")]
            text = "\n".join(lines)
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to parse flush response as JSON: %s", response[:200])
        return 0

    facts = data.get("facts", [])
    events = data.get("events", [])
    added_count = 0

    # Save new facts (with dedup)
    for fact in facts:
        if not isinstance(fact, str) or not fact.strip():
            continue
        if _is_duplicate(fact, existing_memories):
            logger.debug("Skipping duplicate fact: %s", fact[:80])
            continue
        memory_id = await repository.add_memory(fact.strip())
        existing_memories.append(fact.strip())
        added_count += 1
        logger.info("Auto-extracted memory: %s", fact[:80])

        # Audit log (best-effort)
        try:
            from app.provenance.context import get_audit_logger
            from app.provenance.models import Action, Actor, EntityType

            _al = get_audit_logger()
            if _al:
                await _al.log_mutation(
                    EntityType.MEMORY,
                    memory_id,
                    Action.CREATE,
                    Actor.LLM_FLUSH,
                    after_snapshot=fact.strip(),
                )
                await _al.version_memory(memory_id, fact.strip(), Actor.LLM_FLUSH)
        except Exception:
            logger.warning("Audit log failed during flush_to_memory", exc_info=True)

        # Embed the new fact (best-effort)
        if embed_model:
            try:
                from app.embeddings.indexer import embed_memory

                await embed_memory(
                    memory_id,
                    fact.strip(),
                    repository,
                    ollama_client,
                    embed_model,
                )
            except Exception:
                logger.warning("Failed to embed extracted fact %d", memory_id)

    # Save events to daily log
    for event in events:
        if not isinstance(event, str) or not event.strip():
            continue
        await daily_log.append(event.strip())
        logger.info("Auto-logged event: %s", event[:80])

    # Sync MEMORY.md if facts were added
    if added_count > 0:
        memories = await repository.list_memories()
        await memory_file.sync(memories)

    return added_count


async def maybe_summarize(
    conversation_id: int,
    repository: Repository,
    ollama_client: OllamaClient,
    threshold: int,
    max_messages: int,
    daily_log: DailyLog | None = None,
    memory_file: MemoryFile | None = None,
    flush_enabled: bool = False,
    embed_model: str | None = None,
) -> None:
    try:
        count = await repository.get_message_count(conversation_id)
        if count <= threshold:
            return

        previous_summary = await repository.get_latest_summary(conversation_id)
        all_messages = await repository.get_recent_messages(conversation_id, count)
        old_messages = all_messages[: len(all_messages) - max_messages]

        if not old_messages:
            return

        # Pre-compaction flush: extract facts and events before summarizing
        new_facts_count = 0
        if flush_enabled and daily_log and memory_file:
            try:
                new_facts_count = await flush_to_memory(
                    old_messages,
                    repository,
                    ollama_client,
                    daily_log,
                    memory_file,
                    embed_model=embed_model,
                )
            except Exception:
                logger.exception("Memory flush failed for conversation %d", conversation_id)

        try:
            from app.eval.prompt_manager import get_active_prompt

            summarize_prompt = await get_active_prompt("summarizer", repository, SUMMARIZE_PROMPT)
        except Exception:
            summarize_prompt = SUMMARIZE_PROMPT

        prompt_parts = [summarize_prompt]
        if previous_summary:
            prompt_parts.append(f"\nPrevious summary:\n{previous_summary}")
        prompt_parts.append("\nConversation to summarize:")
        for msg in old_messages:
            prompt_parts.append(f"{msg.role}: {msg.content}")

        summary_prompt = "\n".join(prompt_parts)
        messages = [ChatMessage(role="user", content=summary_prompt)]
        summary = await ollama_client.chat(messages, think=False)

        await repository.save_summary(conversation_id, summary, count)
        await repository.delete_old_messages(conversation_id, max_messages)

        logger.info(
            "Summarized conversation %d: %d messages → kept %d (flushed %d facts)",
            conversation_id,
            count,
            max_messages,
            new_facts_count,
        )

        # Memory consolidation after flush (5D will integrate here)
        if new_facts_count > 0 and daily_log and memory_file:
            try:
                from app.memory.consolidator import consolidate_memories

                await consolidate_memories(repository, ollama_client, memory_file)
            except Exception:
                logger.exception("Memory consolidation failed for conversation %d", conversation_id)

    except Exception:
        logger.exception("Summarization failed for conversation %d", conversation_id)
