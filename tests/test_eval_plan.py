"""Tests for agent plan benchmark mode (Plan 62 Phase 3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@dataclass
class FakeTaskStep:
    id: int
    description: str
    worker_type: str = "general"
    tools: list[str] = field(default_factory=list)
    status: str = "pending"
    result: str | None = None
    depends_on: list[int] = field(default_factory=list)


@dataclass
class FakePlan:
    objective: str
    context_summary: str = ""
    tasks: list[FakeTaskStep] = field(default_factory=list)


async def test_run_plan_scores_task_count():
    """Plan mode checks minimum task count."""
    from scripts.run_eval import _run_plan

    entries = [
        {
            "id": 1,
            "input_text": "Refactorizar el auth module",
            "metadata": '{"expected_plan_tasks": 2, "expected_plan_categories": ["auth"], "section": "agent"}',
        }
    ]

    fake_plan = FakePlan(
        objective="Refactorizar el auth module",
        tasks=[
            FakeTaskStep(1, "Analyze auth module structure", tools=["search_source_code"]),
            FakeTaskStep(2, "Refactor auth endpoints", tools=["write_source_file"]),
            FakeTaskStep(3, "Test auth changes", tools=["run_shell"]),
        ],
    )

    mock_client = AsyncMock()
    # Judge response
    mock_judge_resp = MagicMock()
    mock_judge_resp.content = "1. COHERENCE: YES\n2. COMPLETENESS: YES\nVERDICT: PASS"
    mock_client.chat_with_tools.return_value = mock_judge_resp

    with patch("app.agent.planner.create_plan", return_value=fake_plan):
        results = await _run_plan(entries, mock_client)

    assert len(results) == 1
    r = results[0]
    assert r["passed"] is True
    assert r["score"] > 0


async def test_run_plan_skips_without_metadata():
    """Entries without plan metadata are skipped."""
    from scripts.run_eval import _run_plan

    entries = [
        {
            "id": 1,
            "input_text": "Hola",
            "metadata": '{"section": "chat"}',
        }
    ]

    results = await _run_plan(entries, AsyncMock())
    assert len(results) == 0


async def test_run_plan_handles_planner_error():
    """If planner throws, entry scores 0."""
    from scripts.run_eval import _run_plan

    entries = [
        {
            "id": 1,
            "input_text": "Migrate to PostgreSQL",
            "metadata": '{"expected_plan_tasks": 3, "section": "agent"}',
        }
    ]

    with patch("app.agent.planner.create_plan", side_effect=RuntimeError("LLM down")):
        results = await _run_plan(entries, AsyncMock())

    assert len(results) == 1
    assert results[0]["passed"] is False
    assert "ERROR" in results[0]["detail"]
