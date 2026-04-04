"""Multi-criteria LLM-as-judge for eval quality scoring.

Returns structured scores across 4 dimensions with reasoning.
Uses think=False for deterministic JSON output.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_JUDGE_PROMPT = """\
You are an expert quality evaluator. Score the assistant's response on these criteria:

1. **correctness** (0.0-1.0): Is the response factually correct and does it solve the problem?
2. **completeness** (0.0-1.0): Does it cover all aspects of the question?
3. **conciseness** (0.0-1.0): Is it appropriately concise without losing information? Penalize excessive verbosity, unnecessary disclaimers, or repetition.
4. **tool_usage** (0.0-1.0): Were tools used correctly and efficiently? Set to 1.0 if no tools were involved.

User question: {question}
Expected answer: {expected}
Actual answer: {actual}

Respond with ONLY a JSON object (no markdown, no explanation outside the JSON):
{{"correctness": 0.0, "completeness": 0.0, "conciseness": 0.0, "tool_usage": 0.0, "reasoning": "Brief explanation"}}
"""

_CRITERIA = ("correctness", "completeness", "conciseness", "tool_usage")


@dataclass
class JudgeResult:
    correctness: float = 0.0
    completeness: float = 0.0
    conciseness: float = 0.0
    tool_usage: float = 1.0
    reasoning: str = ""
    raw_response: str = ""
    parse_error: bool = False

    @property
    def average(self) -> float:
        return (self.correctness + self.completeness + self.conciseness + self.tool_usage) / 4

    @property
    def passed(self) -> bool:
        """Binary pass: average >= 0.6 and no criterion below 0.3."""
        return self.average >= 0.6 and all(getattr(self, c) >= 0.3 for c in _CRITERIA)

    def to_dict(self) -> dict:
        return {
            "correctness": self.correctness,
            "completeness": self.completeness,
            "conciseness": self.conciseness,
            "tool_usage": self.tool_usage,
            "average": round(self.average, 2),
            "passed": self.passed,
            "reasoning": self.reasoning,
        }


async def judge_response(
    question: str,
    expected: str,
    actual: str,
    ollama_client: object,
) -> JudgeResult:
    """Run multi-criteria judge on a response. Fail-open: returns default scores on error."""
    from app.models import ChatMessage

    prompt = _JUDGE_PROMPT.format(
        question=question[:500],
        expected=expected[:500],
        actual=actual[:500],
    )

    try:
        response = await ollama_client.chat(  # type: ignore[attr-defined]
            [ChatMessage(role="user", content=prompt)],
            think=False,
        )
        raw = str(response).strip()
        return _parse_judge_response(raw)
    except Exception as e:
        logger.warning("judge_response failed: %s", e)
        return JudgeResult(
            correctness=0.5,
            completeness=0.5,
            conciseness=0.5,
            tool_usage=1.0,
            reasoning=f"Judge error: {e}",
            parse_error=True,
        )


def _clamp(val: object, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        return max(lo, min(hi, float(val)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.5


def _parse_judge_response(raw: str) -> JudgeResult:
    """Parse JSON response from judge LLM. Tolerant of markdown fences."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[^{}]+\}", text)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                return JudgeResult(
                    reasoning="Failed to parse judge response",
                    raw_response=raw,
                    parse_error=True,
                )
        else:
            return JudgeResult(
                reasoning="No JSON found in judge response",
                raw_response=raw,
                parse_error=True,
            )

    return JudgeResult(
        correctness=_clamp(data.get("correctness", 0.5)),
        completeness=_clamp(data.get("completeness", 0.5)),
        conciseness=_clamp(data.get("conciseness", 0.5)),
        tool_usage=_clamp(data.get("tool_usage", 1.0)),
        reasoning=str(data.get("reasoning", ""))[:500],
        raw_response=raw,
    )
