"""Tests for eval tools — specifically run_quick_eval with multi-criteria judge."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock


def _make_registry_and_register(repository_mock, ollama_mock):
    """Helper: build a minimal registry and call register() from eval_tools."""
    from app.skills.registry import SkillRegistry

    registry = SkillRegistry()

    from app.skills.tools.eval_tools import register

    register(
        registry=registry,
        repository=repository_mock,
        ollama_client=ollama_mock,
    )
    return registry


def _judge_json(correctness=0.9, completeness=0.8, conciseness=0.7, tool_usage=1.0, reasoning="ok"):
    return json.dumps({
        "correctness": correctness, "completeness": completeness,
        "conciseness": conciseness, "tool_usage": tool_usage,
        "reasoning": reasoning,
    })


# ---------------------------------------------------------------------------
# run_quick_eval — multi-criteria LLM-as-judge
# ---------------------------------------------------------------------------


async def test_run_quick_eval_passes_with_high_scores():
    """High judge scores → passed=True → ✅ in output."""
    repository_mock = AsyncMock()
    repository_mock.get_dataset_entries = AsyncMock(
        return_value=[
            {
                "id": 1,
                "input_text": "¿Cuánto es 2+2?",
                "output_text": "La respuesta es cuatro.",
                "expected_output": "4",
                "trace_id": "trace-1",
            }
        ]
    )

    # chat is called twice: inference response, then judge JSON
    ollama_mock = AsyncMock()
    ollama_mock.chat = AsyncMock(
        side_effect=["La respuesta es cuatro.", _judge_json()]
    )

    registry = _make_registry_and_register(repository_mock, ollama_mock)
    handler = registry._tools["run_quick_eval"].handler
    result = await handler(category="all")

    assert "✅" in result
    assert "1/1" in result
    # Multi-criteria scores should appear
    assert "correctness" in result


async def test_run_quick_eval_fails_with_low_scores():
    """Low judge scores → passed=False → ❌ in output."""
    repository_mock = AsyncMock()
    repository_mock.get_dataset_entries = AsyncMock(
        return_value=[
            {
                "id": 2,
                "input_text": "Dime la capital de Francia",
                "output_text": "No sé.",
                "expected_output": "París",
            }
        ]
    )

    ollama_mock = AsyncMock()
    ollama_mock.chat = AsyncMock(
        side_effect=["No sé.", _judge_json(correctness=0.1, completeness=0.1, conciseness=0.5)]
    )

    registry = _make_registry_and_register(repository_mock, ollama_mock)
    handler = registry._tools["run_quick_eval"].handler
    result = await handler(category="all")

    assert "❌" in result
    assert "0/1" in result


async def test_run_quick_eval_judge_uses_think_false():
    """Judge call must pass think=False for deterministic JSON output."""
    repository_mock = AsyncMock()
    repository_mock.get_dataset_entries = AsyncMock(
        return_value=[
            {
                "id": 3,
                "input_text": "¿Hola?",
                "output_text": "Hola",
                "expected_output": "Hola",
            }
        ]
    )

    ollama_mock = AsyncMock()
    ollama_mock.chat = AsyncMock(side_effect=["Hola", _judge_json()])

    registry = _make_registry_and_register(repository_mock, ollama_mock)
    handler = registry._tools["run_quick_eval"].handler
    await handler()

    # Second call (judge) must have think=False kwarg
    judge_call = ollama_mock.chat.call_args_list[1]
    assert judge_call.kwargs.get("think") is False


async def test_run_quick_eval_skips_entries_without_expected_output():
    """Entries without expected_output must be skipped gracefully."""
    repository_mock = AsyncMock()
    repository_mock.get_dataset_entries = AsyncMock(
        return_value=[
            {"id": 10, "input_text": "test", "output_text": "output", "expected_output": None},
        ]
    )

    ollama_mock = AsyncMock()
    ollama_mock.chat = AsyncMock()

    registry = _make_registry_and_register(repository_mock, ollama_mock)
    handler = registry._tools["run_quick_eval"].handler
    result = await handler()

    assert "No correction entries" in result
    ollama_mock.chat.assert_not_called()


async def test_run_quick_eval_no_entries_returns_helpful_message():
    """Empty dataset returns actionable message."""
    repository_mock = AsyncMock()
    repository_mock.get_dataset_entries = AsyncMock(return_value=[])

    ollama_mock = AsyncMock()

    registry = _make_registry_and_register(repository_mock, ollama_mock)
    handler = registry._tools["run_quick_eval"].handler
    result = await handler()

    assert "No dataset entries" in result
    assert "add_to_dataset" in result


async def test_run_quick_eval_records_trace_scores():
    """When entry has trace_id, per-criterion scores are recorded via repository."""
    repository_mock = AsyncMock()
    repository_mock.get_dataset_entries = AsyncMock(
        return_value=[
            {
                "id": 1,
                "input_text": "test",
                "output_text": "out",
                "expected_output": "expected",
                "trace_id": "trace-abc",
            }
        ]
    )
    repository_mock.save_trace_score = AsyncMock()

    ollama_mock = AsyncMock()
    ollama_mock.chat = AsyncMock(side_effect=["response", _judge_json()])

    registry = _make_registry_and_register(repository_mock, ollama_mock)
    handler = registry._tools["run_quick_eval"].handler
    await handler()

    # Should have 4 calls to save_trace_score (one per criterion)
    assert repository_mock.save_trace_score.call_count == 4
    score_names = [c.kwargs["name"] for c in repository_mock.save_trace_score.call_args_list]
    assert "eval:correctness" in score_names
    assert "eval:completeness" in score_names
    assert "eval:conciseness" in score_names
    assert "eval:tool_usage" in score_names
