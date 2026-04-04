"""Tests for multi-criteria LLM-as-judge (Plan 60, Phase 3)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

from app.eval.judge import JudgeResult, _parse_judge_response, judge_response

# ---------------------------------------------------------------------------
# _parse_judge_response
# ---------------------------------------------------------------------------


def test_parse_valid_json():
    raw = json.dumps({
        "correctness": 0.8, "completeness": 0.9,
        "conciseness": 0.7, "tool_usage": 1.0,
        "reasoning": "Good answer",
    })
    result = _parse_judge_response(raw)
    assert result.correctness == 0.8
    assert result.completeness == 0.9
    assert result.conciseness == 0.7
    assert result.tool_usage == 1.0
    assert result.reasoning == "Good answer"
    assert not result.parse_error


def test_parse_json_with_markdown_fences():
    raw = '```json\n{"correctness": 0.5, "completeness": 0.5, "conciseness": 0.5, "tool_usage": 0.5, "reasoning": "ok"}\n```'
    result = _parse_judge_response(raw)
    assert result.correctness == 0.5
    assert not result.parse_error


def test_parse_invalid_json_fallback():
    result = _parse_judge_response("this is not json at all")
    assert result.parse_error


def test_parse_json_embedded_in_text():
    raw = 'Here is my evaluation: {"correctness": 0.6, "completeness": 0.7, "conciseness": 0.8, "tool_usage": 1.0, "reasoning": "decent"}'
    result = _parse_judge_response(raw)
    assert result.correctness == 0.6
    assert not result.parse_error


def test_clamp_out_of_range():
    raw = json.dumps({
        "correctness": 1.5, "completeness": -0.2,
        "conciseness": 0.5, "tool_usage": 0.5,
    })
    result = _parse_judge_response(raw)
    assert result.correctness == 1.0
    assert result.completeness == 0.0


# ---------------------------------------------------------------------------
# JudgeResult properties
# ---------------------------------------------------------------------------


def test_judge_result_average():
    r = JudgeResult(correctness=0.8, completeness=0.6, conciseness=0.4, tool_usage=1.0)
    assert r.average == (0.8 + 0.6 + 0.4 + 1.0) / 4


def test_judge_result_passed():
    r = JudgeResult(correctness=0.8, completeness=0.7, conciseness=0.6, tool_usage=0.9)
    assert r.passed


def test_judge_result_failed_low_criterion():
    r = JudgeResult(correctness=0.2, completeness=0.9, conciseness=0.9, tool_usage=0.9)
    assert not r.passed  # correctness < 0.3


def test_judge_result_failed_low_average():
    r = JudgeResult(correctness=0.3, completeness=0.3, conciseness=0.3, tool_usage=0.3)
    assert not r.passed  # average = 0.3 < 0.6


def test_to_dict():
    r = JudgeResult(correctness=0.8, completeness=0.7, conciseness=0.6, tool_usage=1.0)
    d = r.to_dict()
    assert d["correctness"] == 0.8
    assert d["passed"] is True
    assert "average" in d
    assert "reasoning" in d


# ---------------------------------------------------------------------------
# judge_response integration
# ---------------------------------------------------------------------------


async def test_judge_response_integration():
    mock_client = AsyncMock()
    mock_client.chat.return_value = json.dumps({
        "correctness": 0.9, "completeness": 0.8,
        "conciseness": 0.7, "tool_usage": 1.0,
        "reasoning": "Correct and complete",
    })

    result = await judge_response("What is 2+2?", "4", "The answer is 4.", mock_client)
    assert result.correctness == 0.9
    assert result.passed
    assert not result.parse_error


async def test_judge_response_error_failopen():
    mock_client = AsyncMock()
    mock_client.chat.side_effect = RuntimeError("connection refused")

    result = await judge_response("q", "a", "actual", mock_client)
    assert result.parse_error
    assert result.correctness == 0.5
    assert "Judge error" in result.reasoning
