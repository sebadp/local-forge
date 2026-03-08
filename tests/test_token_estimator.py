"""Tests for app/context/token_estimator.py"""

import logging
from unittest.mock import patch

from app.context.token_estimator import (
    estimate_messages_tokens,
    estimate_sections,
    estimate_tokens,
    log_context_budget,
    log_context_budget_breakdown,
)
from app.models import ChatMessage


def test_estimate_tokens_basic():
    # "hola" = 4 chars → 1 token (max(1, 4//4))
    assert estimate_tokens("hola") == 1


def test_estimate_tokens_longer():
    # 100 chars → 25 tokens
    text = "a" * 100
    assert estimate_tokens(text) == 25


def test_estimate_tokens_empty():
    # Empty string → min 1
    assert estimate_tokens("") == 1


def test_estimate_messages():
    messages = [
        ChatMessage(role="system", content="a" * 40),  # 10 tokens
        ChatMessage(role="user", content="b" * 20),  # 5 tokens
        ChatMessage(role="assistant", content="c" * 80),  # 20 tokens
    ]
    assert estimate_messages_tokens(messages) == 35


def test_estimate_messages_empty_list():
    assert estimate_messages_tokens([]) == 0


def test_log_warns_near_limit(caplog):
    # 81% of 32000 = 25920 tokens → 25920 * 4 = 103680 chars
    messages = [ChatMessage(role="user", content="a" * 103_680)]
    with caplog.at_level(logging.WARNING):
        result = log_context_budget(messages, context_limit=32_000)
    assert result >= 25_920
    assert any("near_limit" in r.message or "nearing" in r.message.lower() for r in caplog.records)


def test_log_errors_over_limit(caplog):
    # 100% + of 32000 → 32000 * 4 + extra chars
    messages = [ChatMessage(role="user", content="a" * 130_000)]
    with caplog.at_level(logging.ERROR):
        result = log_context_budget(messages, context_limit=32_000)
    assert result > 32_000
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_log_info_under_limit(caplog):
    messages = [ChatMessage(role="user", content="hola")]
    with caplog.at_level(logging.INFO):
        result = log_context_budget(messages, context_limit=32_000)
    assert result >= 1
    # No warning or error
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


def test_log_returns_estimate():
    messages = [ChatMessage(role="user", content="a" * 200)]
    result = log_context_budget(messages)
    assert result == 50


# ---------------------------------------------------------------------------
# estimate_sections (Phase 1 — Plan 38)
# ---------------------------------------------------------------------------


def test_estimate_sections_basic():
    """Dict of non-empty strings produces proportional positive ints."""
    result = estimate_sections({"system_prompt": "a" * 100, "history": "b" * 400})
    assert result["system_prompt"] == 25  # 100 // 4
    assert result["history"] == 100  # 400 // 4


def test_estimate_sections_none_values():
    """None sections must produce 0, not raise."""
    result = estimate_sections({"section_a": None, "section_b": "hello world"})
    assert result["section_a"] == 0
    assert result["section_b"] == estimate_tokens("hello world")


def test_estimate_sections_empty_string():
    """Empty string sections produce 0."""
    result = estimate_sections({"empty": "", "nonempty": "abc"})
    assert result["empty"] == 0
    assert result["nonempty"] > 0


# ---------------------------------------------------------------------------
# log_context_budget_breakdown (Phase 1 — Plan 38)
# ---------------------------------------------------------------------------


def test_log_context_budget_breakdown_emits_info():
    """Emits a single INFO log with token_breakdown, largest_section, and total."""
    sections = {"system_prompt": 800, "history": 7300, "user_memories": 3400}

    with patch("app.context.token_estimator.logger") as mock_logger:
        log_context_budget_breakdown(sections)

    assert mock_logger.info.called
    call_args = mock_logger.info.call_args
    # extra is passed as keyword arg
    extra = call_args.kwargs.get("extra")
    assert extra is not None
    assert "token_breakdown" in extra
    assert "largest_section" in extra
    assert "total" in extra
    assert extra["largest_section"] == "history"
    assert extra["total"] == 800 + 7300 + 3400


def test_log_context_budget_breakdown_no_warning():
    """Breakdown function must only emit INFO, never WARNING or ERROR."""
    sections = {"system_prompt": 500, "history": 1000}

    with patch("app.context.token_estimator.logger") as mock_logger:
        log_context_budget_breakdown(sections)

    assert not mock_logger.warning.called
    assert not mock_logger.error.called
    assert mock_logger.info.called


def test_log_context_budget_breakdown_empty_noop():
    """Empty sections dict must not emit any log."""
    with patch("app.context.token_estimator.logger") as mock_logger:
        log_context_budget_breakdown({})

    assert not mock_logger.info.called
