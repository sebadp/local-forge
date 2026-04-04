"""MicroCompact: selective clearing of old tool results to free context window.

Inspired by Claude Code's microCompact — deterministic (no LLM), replaces
verbose tool results from older rounds with compact stubs.

Only clears tools known to produce large outputs. Current-round results
are never touched.
"""

from __future__ import annotations

import logging

from app.models import ChatMessage

logger = logging.getLogger(__name__)

# Tools whose results tend to be large and safe to compact once stale.
COMPACTABLE_TOOLS: set[str] = {
    "web_search",
    "web_research",
    "search_source_code",
    "read_source_file",
    "read_lines",
    "get_recent_messages",
    "search_notes",
    "run_command",
    "get_file_outline",
    "get_file_contents",
    "search_repositories",
    "fetch_markdown",
    "fetch",
    "fetch_txt",
    "get_conversation_transcript",
    "review_interactions",
}

# Minimum result length (chars) to bother compacting — short results cost little.
_MIN_CONTENT_LEN = 200

_REPLACEMENT = "[Tool result from {tool_name} cleared — returned {n_chars} chars]"


def microcompact_messages(
    messages: list[ChatMessage],
    max_age_rounds: int = 2,
    current_round: int = 0,
) -> list[ChatMessage]:
    """Return a new message list with old verbose tool results replaced by stubs.

    A "round" is an assistant message with tool_calls followed by one or more
    tool-role messages.  Round numbering counts backwards from *current_round*
    (the iteration that is about to call the LLM).

    Only tool results from rounds that are >= ``max_age_rounds`` older than
    *current_round* are compacted, and only if the tool name is in
    ``COMPACTABLE_TOOLS`` and the result exceeds ``_MIN_CONTENT_LEN``.
    """
    if current_round < max_age_rounds:
        return messages  # nothing old enough to compact

    # Identify round boundaries by scanning for assistant messages with tool_calls.
    # Each such message starts a new round.  The round index counts from the end
    # (most recent assistant+tools = round ``current_round``).
    round_starts: list[int] = []
    for i, m in enumerate(messages):
        if m.role == "assistant" and m.tool_calls:
            round_starts.append(i)

    if not round_starts:
        return messages

    # Assign a round number (0-based from oldest) to each round_start.
    # The most recent round_start gets number = current_round.
    n_rounds = len(round_starts)
    # offset so that last round_start maps to current_round
    base = current_round - (n_rounds - 1)

    # Build set of message indices to compact: tool messages that belong to old rounds.
    indices_to_compact: set[int] = set()
    for r_idx, start_pos in enumerate(round_starts):
        round_number = base + r_idx
        if current_round - round_number < max_age_rounds:
            continue  # too recent, keep intact
        # Find tool messages after this assistant message, up to next round or end.
        end_pos = round_starts[r_idx + 1] if r_idx + 1 < n_rounds else len(messages)
        for j in range(start_pos + 1, end_pos):
            if messages[j].role == "tool":
                indices_to_compact.add(j)

    if not indices_to_compact:
        return messages

    # Also need to know which tool_name each tool message corresponds to.
    # Convention: tool messages follow the assistant message in the same order
    # as tool_calls in the assistant message.
    # Build a mapping: message_index -> tool_name
    tool_name_map: dict[int, str] = {}
    for r_idx, start_pos in enumerate(round_starts):
        assistant_msg = messages[start_pos]
        if not assistant_msg.tool_calls:
            continue
        end_pos = round_starts[r_idx + 1] if r_idx + 1 < n_rounds else len(messages)
        tool_positions = [j for j in range(start_pos + 1, end_pos) if messages[j].role == "tool"]
        for t_idx, pos in enumerate(tool_positions):
            if t_idx < len(assistant_msg.tool_calls):
                tc = assistant_msg.tool_calls[t_idx]
                tool_name_map[pos] = tc.get("function", {}).get("name", "unknown")
            else:
                tool_name_map[pos] = "unknown"

    # Build new list
    new_messages: list[ChatMessage] = []
    compacted = 0
    for i, m in enumerate(messages):
        if i in indices_to_compact:
            tool_name = tool_name_map.get(i, "unknown")
            if tool_name in COMPACTABLE_TOOLS and len(m.content) > _MIN_CONTENT_LEN:
                new_messages.append(
                    ChatMessage(
                        role="tool",
                        content=_REPLACEMENT.format(tool_name=tool_name, n_chars=len(m.content)),
                    )
                )
                compacted += 1
                continue
        new_messages.append(m)

    if compacted:
        logger.debug("microcompact: cleared %d tool results (round %d)", compacted, current_round)

    return new_messages
