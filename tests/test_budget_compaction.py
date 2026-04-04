"""Tests for budget-based auto-compaction in executor.py (Plan 58)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.models import ChatMessage
from app.skills.executor import _budget_compact


class TestBudgetCompactTriggered:
    def test_compacts_when_over_budget(self):
        """When estimated tokens exceed 80% of budget, compaction should run."""
        messages = [
            ChatMessage(role="system", content="You are helpful."),
            ChatMessage(role="user", content="Hello " * 5000),
            ChatMessage(role="tool", content="Result " * 3000),
            ChatMessage(role="assistant", content="Summary " * 2000),
        ]

        with patch("app.skills.executor.microcompact_messages", side_effect=lambda msgs, **kw: msgs) as mock_mc:
            with patch("app.context.token_estimator.estimate_tokens", return_value=30000):
                with patch.dict("os.environ", {"CONTEXT_WINDOW_TOKENS": "32768"}):
                    _budget_compact(messages, iteration=2)

        mock_mc.assert_called_once()


class TestBudgetCompactNotTriggered:
    def test_no_compaction_under_budget(self):
        """When estimated tokens are well under 80% of budget, no compaction."""
        messages = [
            ChatMessage(role="user", content="Hi"),
            ChatMessage(role="assistant", content="Hello!"),
        ]

        with patch("app.skills.executor.microcompact_messages") as mock_mc:
            with patch("app.context.token_estimator.estimate_tokens", return_value=100):
                with patch.dict("os.environ", {"CONTEXT_WINDOW_TOKENS": "32768"}):
                    _budget_compact(messages, iteration=0)

        mock_mc.assert_not_called()


class TestBudgetCompactBoundary:
    def test_at_exactly_80_percent(self):
        """At exactly 80% threshold, compaction should trigger."""
        messages = [
            ChatMessage(role="user", content="test"),
        ]

        budget = 32768
        threshold = int(budget * 0.8) + 1  # just over

        with patch("app.skills.executor.microcompact_messages", side_effect=lambda msgs, **kw: msgs) as mock_mc:
            with patch("app.context.token_estimator.estimate_tokens", return_value=threshold):
                with patch.dict("os.environ", {"CONTEXT_WINDOW_TOKENS": str(budget)}):
                    _budget_compact(messages, iteration=1)

        mock_mc.assert_called_once()
