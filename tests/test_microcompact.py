"""Tests for app.formatting.microcompact — selective tool result clearing."""

from app.formatting.microcompact import (
    _MIN_CONTENT_LEN,
    COMPACTABLE_TOOLS,
    microcompact_messages,
)
from app.models import ChatMessage


def _tool_call(name: str) -> list[dict]:
    return [{"function": {"name": name, "arguments": {}}}]


def _make_round(tool_name: str, result: str) -> list[ChatMessage]:
    """Helper: one assistant message with a tool call + one tool result."""
    return [
        ChatMessage(role="assistant", content="", tool_calls=_tool_call(tool_name)),
        ChatMessage(role="tool", content=result),
    ]


def test_no_compaction_when_too_few_rounds():
    msgs = [
        ChatMessage(role="user", content="hello"),
        *_make_round("web_search", "x" * 500),
    ]
    result = microcompact_messages(msgs, max_age_rounds=2, current_round=0)
    assert result == msgs


def test_compacts_old_round():
    """Round 0 should be compacted when current_round=2 and max_age=2."""
    msgs = [
        ChatMessage(role="user", content="hello"),
        *_make_round("web_search", "x" * 500),  # round 0
        *_make_round("web_search", "y" * 500),  # round 1
        *_make_round("web_search", "z" * 500),  # round 2
    ]
    result = microcompact_messages(msgs, max_age_rounds=2, current_round=2)
    # Round 0 tool result should be compacted
    assert "[Tool result from web_search cleared" in result[2].content
    # Rounds 1 and 2 should be intact
    assert result[4].content == "y" * 500
    assert result[6].content == "z" * 500


def test_does_not_compact_non_compactable_tool():
    """Tools not in COMPACTABLE_TOOLS should never be compacted."""
    msgs = [
        ChatMessage(role="user", content="hello"),
        *_make_round("calculate", "x" * 500),  # not compactable
        *_make_round("web_search", "y" * 500),
        *_make_round("web_search", "z" * 500),
    ]
    assert "calculate" not in COMPACTABLE_TOOLS
    result = microcompact_messages(msgs, max_age_rounds=2, current_round=2)
    # calculate result should stay intact even though it's old
    assert result[2].content == "x" * 500


def test_does_not_compact_short_results():
    """Results shorter than _MIN_CONTENT_LEN should be kept."""
    short = "x" * (_MIN_CONTENT_LEN - 1)
    msgs = [
        ChatMessage(role="user", content="hello"),
        *_make_round("web_search", short),
        *_make_round("web_search", "y" * 500),
        *_make_round("web_search", "z" * 500),
    ]
    result = microcompact_messages(msgs, max_age_rounds=2, current_round=2)
    assert result[2].content == short


def test_preserves_message_count():
    msgs = [
        ChatMessage(role="user", content="hello"),
        *_make_round("web_search", "x" * 500),
        *_make_round("web_search", "y" * 500),
        *_make_round("web_search", "z" * 500),
    ]
    result = microcompact_messages(msgs, max_age_rounds=2, current_round=2)
    assert len(result) == len(msgs)


def test_replacement_includes_char_count():
    msgs = [
        ChatMessage(role="user", content="hello"),
        *_make_round("web_search", "x" * 1234),
        *_make_round("web_search", "y" * 500),
        *_make_round("web_search", "z" * 500),
    ]
    result = microcompact_messages(msgs, max_age_rounds=2, current_round=2)
    assert "1234 chars" in result[2].content


def test_does_not_mutate_original():
    msgs = [
        ChatMessage(role="user", content="hello"),
        *_make_round("web_search", "x" * 500),
        *_make_round("web_search", "y" * 500),
        *_make_round("web_search", "z" * 500),
    ]
    original_content = msgs[2].content
    microcompact_messages(msgs, max_age_rounds=2, current_round=2)
    assert msgs[2].content == original_content


def test_multiple_tools_in_one_round():
    """Round with 2 tool calls — both should be compacted if old enough."""
    msgs = [
        ChatMessage(role="user", content="hello"),
        # Round 0: 2 tool calls
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[
                {"function": {"name": "web_search", "arguments": {}}},
                {"function": {"name": "search_source_code", "arguments": {}}},
            ],
        ),
        ChatMessage(role="tool", content="a" * 500),
        ChatMessage(role="tool", content="b" * 500),
        # Round 1
        *_make_round("web_search", "c" * 500),
        # Round 2
        *_make_round("web_search", "d" * 500),
    ]
    result = microcompact_messages(msgs, max_age_rounds=2, current_round=2)
    assert "[Tool result from web_search cleared" in result[2].content
    assert "[Tool result from search_source_code cleared" in result[3].content


def test_no_rounds_returns_same():
    msgs = [
        ChatMessage(role="user", content="hello"),
        ChatMessage(role="assistant", content="hi there"),
    ]
    result = microcompact_messages(msgs, max_age_rounds=2, current_round=0)
    assert result == msgs
