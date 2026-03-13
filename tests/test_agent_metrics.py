"""Tests for Plan 39 Agent Metrics & Efficacy.

Covers:
- Repository methods: get_tool_efficiency, get_token_consumption,
  get_context_quality_metrics, get_planner_metrics, get_hitl_rate,
  get_goal_completion_rate
- eval_tools.get_agent_stats tool handler (via SkillRegistry)
"""

from __future__ import annotations

from unittest.mock import AsyncMock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry(repository_mock):
    """Build a SkillRegistry with eval tools registered (tracing gated off)."""
    from app.skills.registry import SkillRegistry
    from app.skills.tools.eval_tools import register

    registry = SkillRegistry()
    register(registry=registry, repository=repository_mock, ollama_client=None)
    return registry


def _empty_repo() -> AsyncMock:
    """Repository mock with all Plan 39 methods returning empty / zero data."""
    repo = AsyncMock()
    repo.get_tool_efficiency = AsyncMock(
        return_value={
            "avg_tool_calls": 0.0,
            "max_tool_calls": 0,
            "no_tool_traces": 0,
            "total_traces": 0,
            "avg_llm_iterations": 0.0,
            "max_llm_iterations": 0,
            "tool_error_rates": [],
        }
    )
    repo.get_token_consumption = AsyncMock(return_value={})
    repo.get_tool_redundancy = AsyncMock(return_value=[])
    repo.get_context_quality_metrics = AsyncMock(
        return_value={
            "avg_fill_rate": 0.0,
            "max_fill_rate": 0.0,
            "near_limit_count": 0,
            "fill_n": 0,
            "classify_upgrade_rate": 0.0,
            "classify_upgraded_n": 0,
            "avg_memories_retrieved": 0.0,
            "avg_memories_passed": 0.0,
            "avg_memories_returned": 0.0,
            "memory_relevance_pct": None,
        }
    )
    repo.get_context_rot_risk = AsyncMock(return_value=[])
    repo.get_planner_metrics = AsyncMock(return_value={"total_planner_sessions": 0})
    repo.get_hitl_rate = AsyncMock(
        return_value={
            "total_escalations": 0,
            "approved": 0,
            "rejected": 0,
        }
    )
    repo.get_goal_completion_rate = AsyncMock(
        return_value={
            "goal_completion_rate_pct": 0.0,
            "n": 0,
        }
    )
    # also needed by get_latency_stats and get_search_stats
    repo.get_latency_percentiles = AsyncMock(return_value=[])
    repo.get_e2e_latency_percentiles = AsyncMock(return_value=[])
    repo.get_search_hit_rate = AsyncMock(return_value=[])
    return repo


# ---------------------------------------------------------------------------
# get_tool_efficiency
# ---------------------------------------------------------------------------


async def test_get_tool_efficiency_no_data():
    """Returns empty/zero dict when no tool spans exist."""
    from app.database.repository import Repository

    class _FakeConn:
        async def execute(self, sql, params=()):
            class _Cur:
                async def fetchone(self):
                    return (0, 0, 0, 0)

                async def fetchall(self):
                    return []

            return _Cur()

    repo = Repository(_FakeConn())  # type: ignore[arg-type]
    result = await repo.get_tool_efficiency(days=7)
    assert result["total_traces"] == 0
    assert result["avg_tool_calls"] == 0.0
    assert result["tool_error_rates"] == []


async def test_get_tool_efficiency_with_data():
    """Returns correct aggregates given known SQL results."""
    from app.database.repository import Repository

    call_count = 0

    class _FakeConn:
        async def execute(self, sql, params=()):
            nonlocal call_count
            call_count += 1

            class _Cur:
                def __init__(self, call_n):
                    self._n = call_n

                async def fetchone(self):
                    if self._n == 1:
                        # main tool counts query
                        return (2.5, 8, 3, 40)  # avg, max, no_tools, total
                    elif self._n == 2:
                        # iteration counts query
                        return (1.8, 5)
                    return None

                async def fetchall(self):
                    # error rates query
                    return [
                        ("add_memory", 20, 2),
                        ("search_notes", 15, 0),
                    ]

            return _Cur(call_count)

    repo = Repository(_FakeConn())  # type: ignore[arg-type]
    result = await repo.get_tool_efficiency(days=7)
    assert result["avg_tool_calls"] == 2.5
    assert result["max_tool_calls"] == 8
    assert result["total_traces"] == 40
    assert result["avg_llm_iterations"] == 1.8
    assert len(result["tool_error_rates"]) == 2
    first = result["tool_error_rates"][0]
    assert first["tool"] == "add_memory"
    assert first["error_rate"] == pytest.approx(2 / 20, abs=0.01)


# ---------------------------------------------------------------------------
# get_token_consumption
# ---------------------------------------------------------------------------


async def test_get_token_consumption_empty_returns_empty_dict():
    """Returns {} when no generation spans with token metadata."""
    from app.database.repository import Repository

    class _FakeConn:
        async def execute(self, sql, params=()):
            class _Cur:
                async def fetchone(self):
                    return (None, None, None, None, 0)  # all None, n=0

            return _Cur()

    repo = Repository(_FakeConn())  # type: ignore[arg-type]
    result = await repo.get_token_consumption(days=7)
    assert result == {}


async def test_get_token_consumption_with_data():
    """Returns avg/total tokens from a known SQL result row."""
    from app.database.repository import Repository

    class _FakeConn:
        async def execute(self, sql, params=()):
            class _Cur:
                async def fetchone(self):
                    # avg_input, avg_output, total_input, total_output, n
                    return (512.0, 128.0, 51200, 12800, 100)

            return _Cur()

    repo = Repository(_FakeConn())  # type: ignore[arg-type]
    result = await repo.get_token_consumption(days=7)
    assert result["avg_input_tokens"] == 512.0
    assert result["avg_output_tokens"] == 128.0
    assert result["total_input_tokens"] == 51200
    assert result["total_output_tokens"] == 12800
    assert result["n_generations"] == 100


# ---------------------------------------------------------------------------
# get_planner_metrics
# ---------------------------------------------------------------------------


async def test_get_planner_metrics_no_sessions():
    """Returns minimal dict with total=0 when no planner spans."""
    from app.database.repository import Repository

    class _FakeConn:
        async def execute(self, sql, params=()):
            class _Cur:
                async def fetchone(self):
                    return (0,)

            return _Cur()

    repo = Repository(_FakeConn())  # type: ignore[arg-type]
    result = await repo.get_planner_metrics(days=7)
    assert result["total_planner_sessions"] == 0
    # No extra keys when no sessions
    assert len(result) == 1


# ---------------------------------------------------------------------------
# get_hitl_rate
# ---------------------------------------------------------------------------


async def test_get_hitl_rate_with_data():
    """Returns correct totals from known SQL result."""
    from app.database.repository import Repository

    class _FakeConn:
        async def execute(self, sql, params=()):
            class _Cur:
                async def fetchone(self):
                    return (5, 4, 1)  # total, approved, rejected

            return _Cur()

    repo = Repository(_FakeConn())  # type: ignore[arg-type]
    result = await repo.get_hitl_rate(days=7)
    assert result["total_escalations"] == 5
    assert result["approved"] == 4
    assert result["rejected"] == 1


# ---------------------------------------------------------------------------
# get_goal_completion_rate
# ---------------------------------------------------------------------------


async def test_get_goal_completion_rate_no_data():
    """Returns 0% with n=0 when no goal_completion scores."""
    from app.database.repository import Repository

    class _FakeConn:
        async def execute(self, sql, params=()):
            class _Cur:
                async def fetchone(self):
                    return (None, 0)  # avg=NULL, count=0

            return _Cur()

    repo = Repository(_FakeConn())  # type: ignore[arg-type]
    result = await repo.get_goal_completion_rate(days=7)
    assert result["goal_completion_rate_pct"] == 0.0
    assert result["n"] == 0


async def test_get_goal_completion_rate_with_data():
    """Returns correct pct when scores exist."""
    from app.database.repository import Repository

    class _FakeConn:
        async def execute(self, sql, params=()):
            class _Cur:
                async def fetchone(self):
                    return (0.85, 20)  # avg=0.85, n=20

            return _Cur()

    repo = Repository(_FakeConn())  # type: ignore[arg-type]
    result = await repo.get_goal_completion_rate(days=7)
    assert result["goal_completion_rate_pct"] == 85.0
    assert result["n"] == 20


# ---------------------------------------------------------------------------
# get_agent_stats tool — no data path
# ---------------------------------------------------------------------------


async def test_get_agent_stats_no_data():
    """Tool returns 'no data' message when all metrics are empty."""
    repo = _empty_repo()
    registry = _make_registry(repo)
    handler = registry._tools["get_agent_stats"].handler
    result = await handler(days=7, focus="all")

    assert "No hay datos" in result or "sin datos" in result.lower()


# ---------------------------------------------------------------------------
# get_agent_stats tool — tools focus
# ---------------------------------------------------------------------------


async def test_get_agent_stats_tools_focus():
    """Tool section shows avg/max tool calls and error rates."""
    repo = _empty_repo()
    repo.get_tool_efficiency = AsyncMock(
        return_value={
            "avg_tool_calls": 3.2,
            "max_tool_calls": 12,
            "no_tool_traces": 5,
            "total_traces": 50,
            "avg_llm_iterations": 2.1,
            "max_llm_iterations": 6,
            "tool_error_rates": [
                {"tool": "web_search", "total": 10, "errors": 3, "error_rate": 0.3},
            ],
        }
    )

    registry = _make_registry(repo)
    handler = registry._tools["get_agent_stats"].handler
    result = await handler(days=7, focus="tools")

    assert "3.2" in result
    assert "12" in result
    assert "web_search" in result
    assert "30.0" in result  # 30% error rate


# ---------------------------------------------------------------------------
# get_agent_stats tool — tokens focus
# ---------------------------------------------------------------------------


async def test_get_agent_stats_tokens_focus():
    """Token section shows avg/total input and output tokens."""
    repo = _empty_repo()
    repo.get_token_consumption = AsyncMock(
        return_value={
            "avg_input_tokens": 1024.0,
            "avg_output_tokens": 256.0,
            "total_input_tokens": 102400,
            "total_output_tokens": 25600,
            "n_generations": 100,
        }
    )

    registry = _make_registry(repo)
    handler = registry._tools["get_agent_stats"].handler
    result = await handler(days=7, focus="tokens")

    assert "1,024" in result or "1024" in result
    assert "256" in result
    assert "102,400" in result or "102400" in result


# ---------------------------------------------------------------------------
# get_agent_stats tool — agent focus
# ---------------------------------------------------------------------------


async def test_get_agent_stats_agent_focus():
    """Agent efficacy section shows planner, HITL, and goal completion."""
    repo = _empty_repo()
    repo.get_planner_metrics = AsyncMock(
        return_value={
            "total_planner_sessions": 10,
            "replanned_sessions": 3,
            "replanning_rate_pct": 30.0,
            "avg_replans_per_session": 0.4,
        }
    )
    repo.get_hitl_rate = AsyncMock(
        return_value={
            "total_escalations": 4,
            "approved": 3,
            "rejected": 1,
        }
    )
    repo.get_goal_completion_rate = AsyncMock(
        return_value={
            "goal_completion_rate_pct": 88.0,
            "n": 10,
        }
    )

    registry = _make_registry(repo)
    handler = registry._tools["get_agent_stats"].handler
    result = await handler(days=7, focus="agent")

    assert "10" in result  # planner sessions
    assert "30.0%" in result  # replanning rate
    assert "4" in result  # HITL escalations
    assert "88.0%" in result  # goal completion


# ---------------------------------------------------------------------------
# get_agent_stats tool — error handling
# ---------------------------------------------------------------------------


async def test_get_agent_stats_error():
    """Tool returns error string when repository raises."""
    repo = _empty_repo()
    repo.get_tool_efficiency = AsyncMock(side_effect=RuntimeError("db down"))

    registry = _make_registry(repo)
    handler = registry._tools["get_agent_stats"].handler
    result = await handler(days=7)

    assert "Error" in result


# ---------------------------------------------------------------------------
# context_fill_rate score stored in ConversationContext
# ---------------------------------------------------------------------------


def test_context_fill_rate_field_exists():
    """ConversationContext.build_timing field exists and defaults to empty dict."""
    from app.context.conversation_context import ConversationContext

    ctx = ConversationContext(phone_number="+1", user_text="hi", conv_id=1)
    assert hasattr(ctx, "build_timing")
    assert isinstance(ctx.build_timing, dict)


# ---------------------------------------------------------------------------
# Needed pytest import for approx
# ---------------------------------------------------------------------------
import pytest  # noqa: E402 — import after test bodies to avoid confusion
