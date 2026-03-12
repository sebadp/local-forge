"""Tests for Phase 2+3 metrics tools: get_latency_stats and get_search_stats.

Also tests _compute_percentiles helper and ConversationContext.search_stats field.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from app.database.repository import _compute_percentiles

# ---------------------------------------------------------------------------
# _compute_percentiles helper
# ---------------------------------------------------------------------------


def test_compute_percentiles_empty():
    """Empty list → all zeros, no crash."""
    result = _compute_percentiles("my_span", [])
    assert result["span"] == "my_span"
    assert result["n"] == 0
    assert result["p50"] == 0.0
    assert result["p95"] == 0.0
    assert result["p99"] == 0.0
    assert result["max"] == 0.0


def test_compute_percentiles_single():
    """Single-element list → p50=p95=p99=max=that value."""
    result = _compute_percentiles("embed", [100.0])
    assert result["n"] == 1
    assert result["p50"] == 100.0
    assert result["p95"] == 100.0
    assert result["p99"] == 100.0
    assert result["max"] == 100.0


def test_compute_percentiles_sorted():
    """Multiple values → p50 is median-ish, p95 near the high end."""
    values = [10.0, 50.0, 90.0, 100.0]
    result = _compute_percentiles("classify", values)
    assert result["n"] == 4
    # p50: idx = max(0, int(4*50/100)-1) = max(0, 1) = 1 → values[1] = 50.0
    assert result["p50"] == 50.0
    # p95: idx = max(0, int(4*95/100)-1) = max(0, 2) = 2 → values[3] → but idx can be 3
    # either way, it should be >= 90
    assert result["p95"] >= 90.0
    assert result["max"] == 100.0


# ---------------------------------------------------------------------------
# get_latency_stats tool
# ---------------------------------------------------------------------------


def _make_registry_with_eval(repository_mock):
    """Build a minimal registry and register eval tools."""
    from app.skills.registry import SkillRegistry
    from app.skills.tools.eval_tools import register

    registry = SkillRegistry()
    register(registry=registry, repository=repository_mock, ollama_client=None)
    return registry


async def test_get_latency_stats_no_data():
    """Tool returns descriptive message when no spans found."""
    repo = AsyncMock()
    repo.get_latency_percentiles = AsyncMock(return_value=[])
    repo.get_e2e_latency_percentiles = AsyncMock(return_value=[])

    registry = _make_registry_with_eval(repo)
    handler = registry._tools["get_latency_stats"].handler
    result = await handler(span_name="all", days=7)

    assert "No latency data" in result
    assert "7" in result


async def test_get_latency_stats_all():
    """Tool formats latency data correctly when repository returns stats."""
    repo = AsyncMock()
    repo.get_e2e_latency_percentiles = AsyncMock(
        return_value=[
            {
                "span": "end_to_end",
                "n": 143,
                "p50": 1850.0,
                "p95": 4200.0,
                "p99": 6500.0,
                "max": 8000.0,
            },
        ]
    )
    repo.get_latency_percentiles = AsyncMock(
        return_value=[
            {
                "span": "classify_intent",
                "n": 143,
                "p50": 210.0,
                "p95": 480.0,
                "p99": 820.0,
                "max": 1100.0,
            },
            {"span": "embed", "n": 143, "p50": 95.0, "p95": 200.0, "p99": 310.0, "max": 450.0},
        ]
    )

    registry = _make_registry_with_eval(repo)
    handler = registry._tools["get_latency_stats"].handler
    result = await handler(span_name="all", days=7)

    assert "classify_intent" in result
    assert "embed" in result
    assert "p50" in result
    assert "p95" in result
    assert "143" in result


async def test_get_latency_stats_specific_span():
    """Tool passes span_name correctly when not 'all'."""
    repo = AsyncMock()
    repo.get_latency_percentiles = AsyncMock(
        return_value=[
            {"span": "guardrails", "n": 10, "p50": 12.0, "p95": 28.0, "p99": 45.0, "max": 60.0}
        ]
    )

    registry = _make_registry_with_eval(repo)
    handler = registry._tools["get_latency_stats"].handler
    result = await handler(span_name="guardrails", days=14)

    repo.get_latency_percentiles.assert_called_once_with("guardrails", days=14, enabled=True)
    assert "guardrails" in result


async def test_get_latency_stats_error():
    """Tool returns error string on repository exception."""
    repo = AsyncMock()
    repo.get_latency_percentiles = AsyncMock(side_effect=RuntimeError("db error"))

    registry = _make_registry_with_eval(repo)
    handler = registry._tools["get_latency_stats"].handler
    result = await handler()

    assert "Error" in result


# ---------------------------------------------------------------------------
# get_search_stats tool
# ---------------------------------------------------------------------------


async def test_search_stats_no_data():
    """Tool returns descriptive message when no search stats available."""
    repo = AsyncMock()
    repo.get_search_hit_rate = AsyncMock(return_value=[])

    registry = _make_registry_with_eval(repo)
    handler = registry._tools["get_search_stats"].handler
    result = await handler(days=7)

    assert "No hay datos" in result or "No search" in result.lower() or "datos" in result


async def test_search_stats_formats_correctly():
    """Tool formats search mode distribution with percentages."""
    repo = AsyncMock()
    repo.get_search_hit_rate = AsyncMock(
        return_value=[
            {"mode": "semantic", "n": 89, "avg_retrieved": 5.0, "avg_passed": 2.3},
            {"mode": "fallback_threshold", "n": 31, "avg_retrieved": 5.0, "avg_passed": 0.0},
            {"mode": "full_fallback", "n": 23, "avg_retrieved": 0.0, "avg_passed": 0.0},
        ]
    )

    registry = _make_registry_with_eval(repo)
    handler = registry._tools["get_search_stats"].handler
    result = await handler(days=7)

    assert "semantic" in result
    assert "fallback_threshold" in result
    assert "full_fallback" in result
    # Total = 89 + 31 + 23 = 143
    assert "143" in result
    # Percentages should be present
    assert "%" in result


async def test_search_stats_error():
    """Tool returns error string on repository exception."""
    repo = AsyncMock()
    repo.get_search_hit_rate = AsyncMock(side_effect=RuntimeError("db error"))

    registry = _make_registry_with_eval(repo)
    handler = registry._tools["get_search_stats"].handler
    result = await handler()

    assert "Error" in result


# ---------------------------------------------------------------------------
# ConversationContext.search_stats field
# ---------------------------------------------------------------------------


def test_conversation_context_search_stats_field_defaults_empty():
    """ConversationContext must have a search_stats field that defaults to empty dict."""
    from app.context.conversation_context import ConversationContext

    ctx = ConversationContext(phone_number="+1234", user_text="hi", conv_id=1)
    assert hasattr(ctx, "search_stats")
    assert isinstance(ctx.search_stats, dict)
    assert ctx.search_stats == {}
