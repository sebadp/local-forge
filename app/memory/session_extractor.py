"""Session Memory: LLM-powered fact extraction from recent messages.

Runs as a background task every N user messages. Extracts new facts
(preferences, technical context, temporal events, corrections) and
persists them as memories.

Complementary to:
- fact_extractor.py (regex, instant, limited patterns)
- dream.py (consolidation, every 24h, all memories)
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.models import ChatMessage

if TYPE_CHECKING:
    from app.database.repository import Repository
    from app.llm.client import OllamaClient
    from app.memory.daily_log import DailyLog
    from app.memory.markdown import MemoryFile

logger = logging.getLogger(__name__)

# In-memory counters: phone -> messages since last extraction
_message_counters: dict[str, int] = {}


def should_extract(phone: str, interval: int = 10) -> bool:
    """Check if we should run extraction for this phone number.

    Increments counter on every call. Returns True every `interval` messages.
    """
    count = _message_counters.get(phone, 0) + 1
    _message_counters[phone] = count
    if count >= interval:
        _message_counters[phone] = 0
        return True
    return False


def reset_counter(phone: str) -> None:
    """Reset the extraction counter for a phone number."""
    _message_counters.pop(phone, None)


_SESSION_EXTRACT_PROMPT = """\
Analyze these recent messages and extract NEW facts about the user.

Messages:
{messages}

Existing known facts:
{existing_facts}

Today's date: {today}

Rules:
- Only extract facts NOT already captured in existing known facts
- Convert relative dates to absolute using today's date
- Categories: preference, personal, technical, temporal, correction
- If a fact contradicts an existing one, use category "correction"
- Be concise: one sentence per fact
- Only extract clearly stated facts, not guesses

Return ONLY this JSON (no other text):
```json
{{"facts": [
  {{"content": "The user prefers concise responses", "category": "preference"}},
  {{"content": "The user has a React project with TypeScript", "category": "technical"}}
]}}
```
If no new facts: {{"facts": []}}
"""


def _format_messages_for_extraction(messages: list[ChatMessage]) -> str:
    """Format recent messages for the extraction prompt (only user+assistant)."""
    lines = []
    for m in messages:
        if m.role not in ("user", "assistant"):
            continue
        role = "User" if m.role == "user" else "Assistant"
        content = m.content[:500] if m.content else ""
        lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else "(no messages)"


def _parse_extraction_response(response: str) -> list[dict]:
    """Parse the LLM JSON response, tolerating markdown fences."""
    text = response.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(line for line in lines if not line.strip().startswith("```")).strip()

    try:
        data = json.loads(text)
        return data.get("facts", [])
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start:end])
            return data.get("facts", [])
        except json.JSONDecodeError:
            pass

    return []


async def extract_session_facts(
    messages: list[ChatMessage],
    existing_memories: list[str],
    repository: Repository,
    ollama_client: OllamaClient,
    daily_log: DailyLog | None = None,
    memory_file: MemoryFile | None = None,
) -> int:
    """Extract new facts from recent messages and persist them.

    Returns the number of facts extracted and saved.
    """
    if not messages:
        return 0

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    existing_text = "\n".join(f"- {m}" for m in existing_memories) if existing_memories else "(none)"

    prompt = _SESSION_EXTRACT_PROMPT.format(
        messages=_format_messages_for_extraction(messages),
        existing_facts=existing_text,
        today=today,
    )

    from app.tracing.context import get_current_trace

    trace = get_current_trace()
    chat_messages = [ChatMessage(role="user", content=prompt)]

    if trace:
        async with trace.span("session_extraction:llm", kind="generation") as span:
            span.set_input({"message_count": len(messages), "existing_facts": len(existing_memories)})
            response = await ollama_client.chat(chat_messages, think=False)
            span.set_output({"response_length": len(response) if response else 0})
    else:
        response = await ollama_client.chat(chat_messages, think=False)

    if not response:
        return 0

    facts = _parse_extraction_response(response)
    if not facts:
        return 0

    saved = 0
    for fact in facts:
        content = fact.get("content", "").strip()
        category = fact.get("category", "general").strip()
        if not content:
            continue

        # Validate category
        valid_categories = {"preference", "personal", "technical", "temporal", "correction", "general"}
        if category not in valid_categories:
            category = "general"

        await repository.save_memory(content, category=category)
        saved += 1
        logger.info("session_extract.fact: [%s] %s", category, content[:80])

        if daily_log:
            await daily_log.append(f"[session-extract] [{category}] {content}")

    if memory_file and saved > 0:
        updated = await repository.list_memories()
        await memory_file.sync(updated)

    logger.info("session_extract.completed: extracted %d facts", saved)
    return saved
