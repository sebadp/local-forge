"""Tests for app.agent.subagent and plan mode."""

import json
from unittest.mock import AsyncMock, MagicMock

from app.agent.subagent import SubagentConfig, run_subagent, should_use_subagent

# --- should_use_subagent ---


def test_simple_task_no_subagent():
    assert should_use_subagent("Read the README file", "reader") is False


def test_complex_task_triggers_subagent():
    desc = "Create a new API endpoint, implement the handler, write tests, and update documentation"
    assert should_use_subagent(desc, "coder") is True


def test_long_description_triggers_subagent():
    desc = "x" * 151
    assert should_use_subagent(desc, "general") is True


def test_non_eligible_worker_type():
    desc = "Create and build and implement and write and test"
    assert should_use_subagent(desc, "reader") is False
    assert should_use_subagent(desc, "reporter") is False


def test_exactly_3_action_words():
    desc = "Create the file, write content, and test it"
    assert should_use_subagent(desc, "coder") is True


def test_2_action_words_no_subagent():
    desc = "Read the file and analyze it"
    assert should_use_subagent(desc, "general") is False


# --- run_subagent ---


async def test_run_subagent_returns_result():
    """Subagent should run execute_tool_loop and return result."""
    config = SubagentConfig(objective="List all Python files")

    ollama = AsyncMock()
    registry = MagicMock()
    registry.get_ollama_tools.return_value = []
    registry.has_tools.return_value = True

    # Mock execute_tool_loop
    import app.skills.executor as executor_module

    original = executor_module.execute_tool_loop

    async def mock_loop(**kwargs):
        return "Found 10 Python files"

    executor_module.execute_tool_loop = mock_loop
    try:
        result = await run_subagent(config, ollama, registry)
        assert "10 Python files" in result
    finally:
        executor_module.execute_tool_loop = original


async def test_run_subagent_timeout():
    """Subagent should handle timeout gracefully."""
    config = SubagentConfig(objective="Long task", timeout_seconds=0.01)

    ollama = AsyncMock()
    registry = MagicMock()
    registry.get_ollama_tools.return_value = []
    registry.has_tools.return_value = True

    import asyncio

    import app.skills.executor as executor_module

    original = executor_module.execute_tool_loop

    async def slow_loop(**kwargs):
        await asyncio.sleep(10)
        return "done"

    executor_module.execute_tool_loop = slow_loop
    try:
        result = await run_subagent(config, ollama, registry)
        assert "timed out" in result
    finally:
        executor_module.execute_tool_loop = original


async def test_run_subagent_error():
    """Subagent should handle errors gracefully."""
    config = SubagentConfig(objective="Failing task")

    ollama = AsyncMock()
    registry = MagicMock()
    registry.get_ollama_tools.return_value = []
    registry.has_tools.return_value = True

    import app.skills.executor as executor_module

    original = executor_module.execute_tool_loop

    async def error_loop(**kwargs):
        raise RuntimeError("connection refused")

    executor_module.execute_tool_loop = error_loop
    try:
        result = await run_subagent(config, ollama, registry)
        assert "error" in result
        assert "connection refused" in result
    finally:
        executor_module.execute_tool_loop = original


# --- replan_with_feedback ---


async def test_replan_with_feedback():
    from app.agent.models import AgentPlan, TaskStep
    from app.agent.planner import replan_with_feedback

    plan = AgentPlan(
        objective="Build an API",
        tasks=[
            TaskStep(id=1, description="Read structure", worker_type="reader"),
            TaskStep(id=2, description="Write code", worker_type="coder", depends_on=[1]),
        ],
    )

    ollama = AsyncMock()
    # Mock response with new plan
    new_plan_json = json.dumps({
        "context_summary": "Revised plan",
        "tasks": [
            {"id": 1, "description": "Read structure", "worker_type": "reader", "depends_on": []},
            {"id": 2, "description": "Write API endpoints", "worker_type": "coder", "depends_on": [1]},
            {"id": 3, "description": "Write tests", "worker_type": "coder", "depends_on": [2]},
        ],
    })
    mock_response = MagicMock()
    mock_response.content = new_plan_json
    mock_response.input_tokens = 100
    mock_response.output_tokens = 50
    mock_response.model = "test"
    ollama.chat_with_tools = AsyncMock(return_value=mock_response)

    result = await replan_with_feedback("Build an API", plan, "also add tests", ollama)
    assert len(result.tasks) == 3
    assert any("tests" in t.description.lower() for t in result.tasks)
