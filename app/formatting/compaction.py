"""Context compaction and management utilities.

Strategy (ordered by preference):
1. If small enough, return as-is
2. Try JSON-aware field extraction (no LLM, no hallucination risk)
3. Fall back to LLM summarization with a strict prompt
4. Hard truncate as last resort
"""

from __future__ import annotations

import json
import logging

from app.llm.client import OllamaClient
from app.models import ChatMessage
from app.tracing.context import get_current_trace

logger = logging.getLogger(__name__)

# Key fields to extract per response type. Extensible by adding new profiles.
# These are the fields that are most useful for follow-up tool calls.
_JSON_KEY_FIELDS: list[str] = [
    "name",
    "full_name",
    "id",
    "number",
    "title",
    "description",
    "html_url",
    "url",
    "clone_url",
    "ssh_url",
    "updated_at",
    "created_at",
    "pushed_at",
    "language",
    "state",
    "private",
    "fork",
    "stargazers_count",
    "open_issues_count",
    "default_branch",
    "login",  # user objects
    "body",  # issue/PR body (truncated)
    "content",  # file content from get_file_contents
    "path",  # file/dir path
    "type",  # "file" or "dir"
    "size",  # file size in bytes
]


def _try_json_extraction(text: str, max_length: int) -> str | None:
    """Try to extract key fields from a JSON payload without using LLM.

    Returns a compact JSON string preserving exact identifiers, or None if
    the text is not parseable JSON or can't be meaningfully reduced.

    This eliminates the hallucination risk (LLM replacing "localforge-assistant"
    with "[repo-name]") for structured API responses.
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None

    # GitHub Search API: {"total_count": N, "items": [...]}
    if isinstance(data, dict) and "items" in data:
        items = data["items"]
        total = data.get("total_count")
        return _extract_item_list(items, max_length, total_count=total)

    # Direct list response (list_repos, list_issues, list_pull_requests, etc.)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return _extract_item_list(data, max_length)

    # Single object — just filter to key fields
    if isinstance(data, dict):
        compact = _pick_fields(data)
        if not compact:
            # No known key fields found — fall through to LLM summarization
            return None
        result = json.dumps(compact, indent=2, ensure_ascii=False)
        return result if len(result) <= max_length else None

    return None


def _pick_fields(item: dict, content_limit: int = 12000) -> dict:
    """Pick only key fields from a dict, flattening known nested objects."""
    compact: dict = {}
    for k in _JSON_KEY_FIELDS:
        if k not in item:
            continue
        val = item[k]
        # Flatten user objects like {"login": "sebadp", "id": 12345} → "sebadp"
        if isinstance(val, dict) and "login" in val:
            compact[k] = val["login"]
        elif isinstance(val, str) and k == "body" and len(val) > 300:
            compact[k] = val[:300] + "…"
        elif isinstance(val, str) and k == "content" and len(val) > content_limit:
            compact[k] = val[:content_limit] + "\n…[truncated]"
        else:
            compact[k] = val
    return compact


def _extract_item_list(
    items: list,
    max_length: int,
    total_count: int | None = None,
) -> str | None:
    """Extract key fields from a list of dicts, truncating until it fits max_length."""
    if not items or not isinstance(items[0], dict):
        return None

    extracted = [_pick_fields(item) for item in items]

    # Progressively drop items from the end until it fits
    while extracted:
        suffix = ""
        if total_count and total_count > len(extracted):
            suffix = f"\n\n(Showing {len(extracted)} of {total_count} total results)"
        result = json.dumps(extracted, indent=2, ensure_ascii=False) + suffix
        if len(result) <= max_length:
            return result
        extracted.pop()

    return None  # Could not fit even one item


async def compact_tool_output(
    tool_name: str,
    text: str,
    user_request: str,
    ollama_client: OllamaClient,
    max_length: int | None = None,
) -> str:
    """Implement intelligent summarization for tool outputs that exceed max_length.

    Tries JSON-aware extraction first (deterministic, no hallucination risk),
    then falls back to LLM summarization, then to hard truncation.
    """
    if max_length is None:
        try:
            from app.config import Settings

            max_length = Settings().compaction_threshold  # type: ignore[call-arg]
        except Exception:
            max_length = 20000

    if len(text) <= max_length:
        return text

    logger.warning(
        "Tool '%s' returned %d chars (limit %d). Starting compaction.",
        tool_name,
        len(text),
        max_length,
    )

    # Step 1: Try JSON-aware extraction (fast, deterministic, zero LLM cost)
    structured = _try_json_extraction(text, max_length)
    if structured:
        logger.info(
            "Tool '%s' compacted via JSON extraction: %d → %d chars",
            tool_name,
            len(text),
            len(structured),
        )
        return structured

    # Step 2: LLM-based summarization
    prompt = (
        f"The tool '{tool_name}' returned {len(text)} characters.\n"
        f'The user\'s request was: "{user_request}"\n\n'
        "Summarize, extracting ONLY fields that answer the user's request.\n"
        "CRITICAL RULES:\n"
        "1. PRESERVE EXACT names, IDs, URLs — NEVER substitute with [placeholder].\n"
        "2. Discard metadata not useful for answering the request.\n"
        "3. Output must be readable by another AI or a human.\n\n"
        f"--- PAYLOAD ---\n{text[:15000]}\n--- END ---"
    )

    try:
        messages = [
            ChatMessage(
                role="system",
                content=(
                    "You are a context-compaction agent. "
                    "Preserve exact identifiers. Never hallucinate placeholder names."
                ),
            ),
            ChatMessage(role="user", content=prompt),
        ]

        trace = get_current_trace()
        if trace:
            async with trace.span("llm:compact_output", kind="generation") as _span:
                _span.set_input({"tool_name": tool_name, "original_len": len(text)})
                summary = await ollama_client.chat(messages, model=None, think=False)
                _span.set_output({"compacted_len": len(summary) if summary else 0})
        else:
            summary = await ollama_client.chat(messages, model=None, think=False)

        if not summary or summary.isspace():
            raise ValueError("Empty summary from LLM")

        logger.info(
            "Tool '%s' compacted via LLM: %d → %d chars", tool_name, len(text), len(summary)
        )
        return summary
    except Exception as e:
        logger.error("LLM compaction failed for '%s': %s", tool_name, e, exc_info=True)
        # Hard truncate as last resort
        return text[:max_length] + "\n…[truncated]"
