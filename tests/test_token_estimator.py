"""Tests for app/context/token_estimator.py"""

import logging

from app.context.token_estimator import (
    estimate_messages_tokens,
    estimate_tokens,
    log_context_budget,
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
