"""Tests for the agentic session subsystem.

Tests cover:
- AgentSession model behavior
- Task memory tools (create_task_plan, get_task_plan, update_task_status)
- HITL mechanism (resolve_hitl, pending state)
- Git tools (basic IO with mocked subprocess)
- Write tools in selfcode_tools (write_source_file, apply_patch)
- /cancel and /agent commands
- loop.py helpers (create_session, get_active_session, cancel_session)
- Worker empty result detection
- Synthesizer honesty prompt
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.hitl import has_pending_approval, resolve_hitl
from app.agent.loop import (
    _active_sessions,
    cancel_session,
    create_session,
    get_active_session,
)
from app.agent.models import AgentSession, AgentStatus
from app.agent.task_memory import register_task_memory_tools
from app.skills.models import ToolCall
from app.skills.registry import SkillRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_registry():
    """An empty SkillRegistry with no skills directory dependency."""
    return SkillRegistry(skills_dir="/nonexistent")


@pytest.fixture(autouse=True)
def clear_active_sessions():
    """Ensure _active_sessions is empty before and after each test."""
    _active_sessions.clear()
    yield
    _active_sessions.clear()


@pytest.fixture
def sample_session() -> AgentSession:
    return AgentSession(
        session_id="abc123",
        phone_number="5491112345678",
        objective="Fix the login bug",
        max_iterations=10,
    )


# ---------------------------------------------------------------------------
# AgentSession model
# ---------------------------------------------------------------------------


def test_agent_session_defaults(sample_session):
    assert sample_session.status == AgentStatus.RUNNING
    assert sample_session.task_plan is None
    assert sample_session.iteration == 0


def test_agent_status_values():
    assert AgentStatus.RUNNING == "running"
    assert AgentStatus.COMPLETED == "completed"
    assert AgentStatus.CANCELLED == "cancelled"
    assert AgentStatus.FAILED == "failed"
    assert AgentStatus.WAITING_USER == "waiting_user"


# ---------------------------------------------------------------------------
# loop.py helpers
# ---------------------------------------------------------------------------


def test_create_session():
    session = create_session("5491112345678", "Do something")
    assert session.phone_number == "5491112345678"
    assert session.objective == "Do something"
    assert session.session_id  # non-empty UUID
    assert session.status == AgentStatus.RUNNING


def test_get_active_session_empty():
    assert get_active_session("5491112345678") is None


def test_get_and_cancel_active_session(sample_session):
    _active_sessions[sample_session.phone_number] = sample_session
    retrieved = get_active_session(sample_session.phone_number)
    assert retrieved is sample_session

    result = cancel_session(sample_session.phone_number)
    assert result is True
    assert sample_session.status == AgentStatus.CANCELLED


def test_cancel_no_active_session():
    result = cancel_session("5491112345678")
    assert result is False


def test_cancel_already_completed(sample_session):
    sample_session.status = AgentStatus.COMPLETED
    _active_sessions[sample_session.phone_number] = sample_session
    result = cancel_session(sample_session.phone_number)
    # Should not cancel a completed session
    assert result is False


# ---------------------------------------------------------------------------
# Task memory tools
# ---------------------------------------------------------------------------


@pytest.fixture
async def registry_with_tasks(fresh_registry, sample_session):
    register_task_memory_tools(fresh_registry, lambda: sample_session)
    return fresh_registry, sample_session


async def test_task_plan_full_lifecycle(registry_with_tasks):
    reg, session = registry_with_tasks

    # No plan yet
    result = await reg.execute_tool(ToolCall(name="get_task_plan", arguments={}))
    assert "No task plan" in result.content

    # Create plan
    plan = "- [ ] Step 1\n- [ ] Step 2\n- [ ] Step 3"
    result = await reg.execute_tool(ToolCall(name="create_task_plan", arguments={"plan": plan}))
    assert "3 pending" in result.content
    assert session.task_plan == plan

    # Read plan
    result = await reg.execute_tool(ToolCall(name="get_task_plan", arguments={}))
    assert "Step 1" in result.content

    # Mark step 1 done
    result = await reg.execute_tool(
        ToolCall(name="update_task_status", arguments={"task_index": 1, "done": True})
    )
    assert "[x]" in session.task_plan
    assert "[ ] Step 2" in session.task_plan

    # Mark step 2 done
    await reg.execute_tool(
        ToolCall(name="update_task_status", arguments={"task_index": 2, "done": True})
    )
    assert session.task_plan.count("[x]") == 2
    assert session.task_plan.count("[ ]") == 1


async def test_update_task_status_invalid_index(registry_with_tasks):
    reg, session = registry_with_tasks
    await reg.execute_tool(
        ToolCall(
            name="create_task_plan",
            arguments={"plan": "- [ ] Only step"},
        )
    )
    result = await reg.execute_tool(
        ToolCall(name="update_task_status", arguments={"task_index": 99})
    )
    assert "not found" in result.content.lower()


async def test_update_task_status_no_plan(registry_with_tasks):
    reg, session = registry_with_tasks
    result = await reg.execute_tool(
        ToolCall(name="update_task_status", arguments={"task_index": 1})
    )
    assert "No task plan" in result.content


async def test_task_memory_no_active_session(fresh_registry):
    register_task_memory_tools(fresh_registry, lambda: None)
    result = await fresh_registry.execute_tool(
        ToolCall(name="create_task_plan", arguments={"plan": "- [ ] test"})
    )
    assert "No active" in result.content


# ---------------------------------------------------------------------------
# HITL
# ---------------------------------------------------------------------------


async def test_resolve_hitl_no_pending():
    result = resolve_hitl("5491112345678", "yes")
    assert result is False


async def test_hitl_resolve_consumes_message():
    phone = "5491112345678"
    mock_wa = AsyncMock()

    async def fake_approval_wait():
        # Simulate user replying after a short delay
        await asyncio.sleep(0.05)
        resolve_hitl(phone, "¡Sí, adelante!")

    from app.agent.hitl import request_user_approval

    asyncio.create_task(fake_approval_wait())
    result = await request_user_approval(phone, "¿Continúo?", mock_wa, timeout=5)

    assert result == "¡Sí, adelante!"
    assert not has_pending_approval(phone)
    mock_wa.send_message.assert_called_once()


async def test_hitl_timeout():
    phone = "5491112345678_timeout"
    mock_wa = AsyncMock()

    from app.agent.hitl import request_user_approval

    result = await request_user_approval(phone, "¿Continúo?", mock_wa, timeout=0.1)
    assert "TIMEOUT" in result
    assert not has_pending_approval(phone)


# ---------------------------------------------------------------------------
# Write tools (selfcode_tools)
# ---------------------------------------------------------------------------


@pytest.fixture
def write_enabled_settings(tmp_path):
    from app.config import Settings

    return Settings(
        whatsapp_access_token="t",
        whatsapp_phone_number_id="p",
        whatsapp_verify_token="v",
        whatsapp_app_secret="s",
        allowed_phone_numbers=["123"],
        agent_write_enabled=True,
        database_path=":memory:",
    )


@pytest.fixture
def write_disabled_settings():
    from app.config import Settings

    return Settings(
        whatsapp_access_token="t",
        whatsapp_phone_number_id="p",
        whatsapp_verify_token="v",
        whatsapp_app_secret="s",
        allowed_phone_numbers=["123"],
        agent_write_enabled=False,
        database_path=":memory:",
    )


async def test_write_source_file_disabled(write_disabled_settings, fresh_registry):
    from app.skills.tools.selfcode_tools import register

    register(fresh_registry, write_disabled_settings)
    result = await fresh_registry.execute_tool(
        ToolCall(
            name="write_source_file",
            arguments={"path": "test_output.txt", "content": "hello"},
        )
    )
    assert "disabled" in result.content.lower()


async def test_write_source_file_blocked_sensitive(
    write_enabled_settings, fresh_registry, tmp_path
):
    """Cannot write to .env or password files even when write is enabled."""
    from app.skills.tools.selfcode_tools import register

    register(fresh_registry, write_enabled_settings)
    result = await fresh_registry.execute_tool(
        ToolCall(
            name="write_source_file",
            arguments={"path": ".env", "content": "SECRET=bad"},
        )
    )
    assert "Blocked" in result.content


async def test_apply_patch_disabled(write_disabled_settings, fresh_registry):
    from app.skills.tools.selfcode_tools import register

    register(fresh_registry, write_disabled_settings)
    result = await fresh_registry.execute_tool(
        ToolCall(
            name="apply_patch",
            arguments={"path": "some.py", "search": "old", "replace": "new"},
        )
    )
    assert "disabled" in result.content.lower()


# ---------------------------------------------------------------------------
# Git tools — basic mocked tests
# ---------------------------------------------------------------------------


async def test_git_status_success(fresh_registry):
    from app.skills.tools.git_tools import register

    register(fresh_registry)
    with patch("app.skills.tools.git_tools._run_git", return_value=(0, "M  app/main.py", "")):
        result = await fresh_registry.execute_tool(ToolCall(name="git_status", arguments={}))
    assert "M  app/main.py" in result.content


async def test_git_create_branch_success(fresh_registry):
    from app.skills.tools.git_tools import register

    register(fresh_registry)
    with patch(
        "app.skills.tools.git_tools._run_git",
        return_value=(0, "Switched to a new branch 'feat/test'", ""),
    ):
        result = await fresh_registry.execute_tool(
            ToolCall(name="git_create_branch", arguments={"branch_name": "feat/test"})
        )
    assert "feat/test" in result.content


async def test_git_create_branch_invalid_name(fresh_registry):
    from app.skills.tools.git_tools import register

    register(fresh_registry)
    result = await fresh_registry.execute_tool(
        ToolCall(name="git_create_branch", arguments={"branch_name": "bad name"})
    )
    assert "cannot" in result.content.lower() or "Error" in result.content


async def test_git_commit_empty_message(fresh_registry):
    from app.skills.tools.git_tools import register

    register(fresh_registry)
    result = await fresh_registry.execute_tool(
        ToolCall(name="git_commit", arguments={"message": "  "})
    )
    assert "empty" in result.content.lower()


async def test_git_commit_nothing_to_commit(fresh_registry):
    from app.skills.tools.git_tools import register

    register(fresh_registry)
    with patch(
        "app.skills.tools.git_tools._run_git",
        side_effect=[(0, "", ""), (1, "", "nothing to commit, working tree clean")],
    ):
        result = await fresh_registry.execute_tool(
            ToolCall(name="git_commit", arguments={"message": "test"})
        )
    assert "nothing to commit" in result.content.lower()


# ---------------------------------------------------------------------------
# /cancel and /agent commands
# ---------------------------------------------------------------------------


async def test_cmd_cancel_no_session(repository, memory_file):
    from app.commands.builtins import cmd_cancel
    from app.commands.context import CommandContext

    ctx = CommandContext(
        phone_number="5491112345678",
        repository=repository,
        memory_file=memory_file,
    )
    result = await cmd_cancel("", ctx)
    assert "No hay" in result or "ninguna" in result


async def test_cmd_cancel_active_session(repository, memory_file, sample_session):
    from app.commands.builtins import cmd_cancel
    from app.commands.context import CommandContext

    _active_sessions[sample_session.phone_number] = sample_session

    ctx = CommandContext(
        phone_number=sample_session.phone_number,
        repository=repository,
        memory_file=memory_file,
    )
    result = await cmd_cancel("", ctx)
    assert "cancelada" in result.lower() or "🛑" in result
    assert sample_session.status == AgentStatus.CANCELLED


async def test_cmd_agent_no_session(repository, memory_file):
    from app.commands.builtins import cmd_agent
    from app.commands.context import CommandContext

    ctx = CommandContext(
        phone_number="5491112345678",
        repository=repository,
        memory_file=memory_file,
    )
    result = await cmd_agent("", ctx)
    assert "No hay" in result


async def test_cmd_agent_with_session(repository, memory_file, sample_session):
    from app.commands.builtins import cmd_agent
    from app.commands.context import CommandContext

    sample_session.task_plan = "- [x] Step 1\n- [ ] Step 2"
    _active_sessions[sample_session.phone_number] = sample_session

    ctx = CommandContext(
        phone_number=sample_session.phone_number,
        repository=repository,
        memory_file=memory_file,
    )
    result = await cmd_agent("", ctx)
    assert "running" in result
    assert "Fix the login bug" in result


# ---------------------------------------------------------------------------
# Worker empty result detection
# ---------------------------------------------------------------------------


async def test_worker_empty_result_returns_sentinel():
    """execute_worker returns sentinel when tool loop returns empty string."""
    from app.agent.models import TaskStep
    from app.agent.workers import execute_worker

    task = TaskStep(id=1, description="Search for info", worker_type="general")
    mock_client = AsyncMock()
    mock_registry = AsyncMock()

    with patch("app.agent.workers.execute_tool_loop", return_value=""):
        result = await execute_worker(
            task=task,
            objective="Find data",
            ollama_client=mock_client,
            skill_registry=mock_registry,
        )

    assert "(no data found" in result


async def test_worker_nonempty_result_passes_through():
    """execute_worker returns the actual result when tool loop returns content."""
    from app.agent.models import TaskStep
    from app.agent.workers import execute_worker

    task = TaskStep(id=1, description="Search for info", worker_type="general")
    mock_client = AsyncMock()
    mock_registry = AsyncMock()

    with patch("app.agent.workers.execute_tool_loop", return_value="Found 5 results about AI"):
        result = await execute_worker(
            task=task,
            objective="Find data",
            ollama_client=mock_client,
            skill_registry=mock_registry,
        )

    assert result == "Found 5 results about AI"


async def test_worker_injects_dependency_context():
    """execute_worker injects results from depends_on tasks into messages."""
    from app.agent.models import AgentPlan, TaskStep
    from app.agent.workers import execute_worker

    task1 = TaskStep(
        id=1, description="Step 1", worker_type="general", status="done", result="Data from step 1"
    )
    task2 = TaskStep(id=2, description="Step 2", worker_type="general", depends_on=[1])
    plan = AgentPlan(objective="Test", tasks=[task1, task2])

    mock_client = AsyncMock()
    mock_registry = AsyncMock()
    captured_messages = []

    async def capture_messages(messages, **kwargs):
        captured_messages.extend(messages)
        return "Done"

    with patch("app.agent.workers.execute_tool_loop", side_effect=capture_messages):
        await execute_worker(
            task=task2,
            objective="Test",
            ollama_client=mock_client,
            skill_registry=mock_registry,
            plan=plan,
        )

    # Should have system prompt, user msg, and dependency context
    dep_msg = [m for m in captured_messages if "CONTEXT FROM PREVIOUS STEPS" in m.content]
    assert len(dep_msg) == 1
    assert "Data from step 1" in dep_msg[0].content


# ---------------------------------------------------------------------------
# Synthesizer honesty prompt
# ---------------------------------------------------------------------------


def test_synthesize_prompt_includes_honesty_clause():
    """Verify the synthesize system prompt includes anti-hallucination directive."""
    from app.agent.planner import _SYNTHESIZE_SYSTEM_PROMPT

    assert "HONESTLY" in _SYNTHESIZE_SYSTEM_PROMPT
    assert "Do NOT fabricate" in _SYNTHESIZE_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Dependency cascade failure
# ---------------------------------------------------------------------------


def test_next_task_cascades_failure_to_blocked_tasks():
    """When a dependency fails, all tasks depending on it should be marked failed."""
    from app.agent.models import AgentPlan, TaskStep

    t1 = TaskStep(
        id=1, description="Read repo", worker_type="reader", status="failed", result="Error"
    )
    t2 = TaskStep(id=2, description="Analyze code", worker_type="analyzer", depends_on=[1])
    t3 = TaskStep(id=3, description="Check deps", worker_type="analyzer", depends_on=[1])
    t4 = TaskStep(id=4, description="Report", worker_type="reporter", depends_on=[2, 3])

    plan = AgentPlan(objective="Test", tasks=[t1, t2, t3, t4])

    # next_task should cascade-fail t2, t3, t4 and return None
    result = plan.next_task()
    assert result is None
    assert t2.status == "failed"
    assert t3.status == "failed"
    assert t4.status == "failed"
    assert "Blocked" in (t2.result or "")
    assert plan.all_done()


def test_next_task_does_not_cascade_when_dep_done():
    """Tasks with satisfied (done) dependencies should still be runnable."""
    from app.agent.models import AgentPlan, TaskStep

    t1 = TaskStep(id=1, description="Read repo", worker_type="reader", status="done", result="OK")
    t2 = TaskStep(id=2, description="Analyze code", worker_type="analyzer", depends_on=[1])

    plan = AgentPlan(objective="Test", tasks=[t1, t2])

    result = plan.next_task()
    assert result is t2
    assert t2.status == "pending"


def test_all_done_with_failures_triggers_has_failures():
    """all_done + has_failures should distinguish success from cascade failure."""
    from app.agent.models import AgentPlan, TaskStep

    t1 = TaskStep(id=1, description="Read", worker_type="reader", status="failed", result="Error")
    t2 = TaskStep(
        id=2, description="Analyze", worker_type="analyzer", status="failed", depends_on=[1]
    )

    plan = AgentPlan(objective="Test", tasks=[t1, t2])

    assert plan.all_done() is True
    assert plan.has_failures() is True
    assert plan.success_count() == 0


def test_all_done_no_failures():
    """When all tasks succeed, has_failures is False."""
    from app.agent.models import AgentPlan, TaskStep

    t1 = TaskStep(id=1, description="Read", worker_type="reader", status="done", result="OK")
    t2 = TaskStep(id=2, description="Report", worker_type="reporter", status="done", result="OK")

    plan = AgentPlan(objective="Test", tasks=[t1, t2])

    assert plan.all_done() is True
    assert plan.has_failures() is False
    assert plan.success_count() == 2
