"""Tests for agent loop tracing — spans for planner/worker/reactive phases."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.loop import _active_sessions, create_session, run_agent_session
from app.agent.models import AgentPlan, AgentStatus, TaskStep
from app.llm.client import ChatResponse, OllamaClient
from app.skills.registry import SkillRegistry
from app.tracing.recorder import TraceRecorder


@pytest.fixture(autouse=True)
def clear_active_sessions():
    _active_sessions.clear()
    yield
    _active_sessions.clear()


@pytest.fixture
def mock_recorder():
    """A TraceRecorder with all async methods mocked and no Langfuse."""
    recorder = MagicMock(spec=TraceRecorder)
    recorder.langfuse = None
    recorder.start_trace = AsyncMock()
    recorder.finish_trace = AsyncMock()
    recorder.start_span = AsyncMock()
    recorder.finish_span = AsyncMock()
    recorder.add_score = AsyncMock()
    return recorder


@pytest.fixture
def mock_wa_client():
    client = MagicMock()
    client.send_message = AsyncMock()
    return client


@pytest.fixture
def mock_ollama():
    client = MagicMock(spec=OllamaClient)
    # Planner returns minimal JSON plan; worker/synthesize returns text
    client.chat_with_tools = AsyncMock(
        return_value=ChatResponse(
            content='{"context_summary":"test","tasks":[{"id":1,"description":"do it","worker_type":"general","depends_on":[]}]}',
            input_tokens=10,
            output_tokens=20,
            model="qwen3.5:9b",
        )
    )
    client.chat = AsyncMock(return_value="done")
    return client


@pytest.fixture
def mock_skill_registry(tmp_path):
    reg = SkillRegistry(skills_dir=str(tmp_path))
    return reg


# ---------------------------------------------------------------------------
# run_agent_session creates a TraceContext
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_session_creates_trace_when_recorder_provided(
    mock_recorder, mock_wa_client, mock_ollama, mock_skill_registry
):
    """When recorder is passed, run_agent_session opens a trace."""
    session = create_session("5491100000001", "test objective")

    with (
        patch("app.agent.loop._register_session_tools", return_value=mock_skill_registry),
        patch("app.agent.loop._build_security_hitl_callback", return_value=None),
        patch("app.agent.loop.execute_worker", new_callable=AsyncMock, return_value="step result"),
        patch("app.agent.loop.create_plan") as mock_create_plan,
        patch("app.agent.loop.synthesize", new_callable=AsyncMock, return_value="final answer"),
        patch("app.agent.loop.replan", new_callable=AsyncMock, return_value=None),
        patch("app.agent.hitl.request_user_approval", new_callable=AsyncMock, return_value="sí"),
    ):
        mock_create_plan.return_value = AgentPlan(
            objective="test objective",
            context_summary="ctx",
            tasks=[TaskStep(id=1, description="step", worker_type="general")],
        )

        await run_agent_session(
            session=session,
            ollama_client=mock_ollama,
            skill_registry=mock_skill_registry,
            wa_client=mock_wa_client,
            recorder=mock_recorder,
        )

    # TraceContext calls start_trace and finish_trace via the recorder
    mock_recorder.start_trace.assert_called_once()
    mock_recorder.finish_trace.assert_called_once()

    # First call should use phone_number as user_id
    call_args = mock_recorder.start_trace.call_args
    assert call_args.args[1] == "5491100000001"  # phone_number
    assert call_args.args[3] == "agent"  # message_type


@pytest.mark.asyncio
async def test_agent_session_no_trace_without_recorder(
    mock_wa_client, mock_ollama, mock_skill_registry
):
    """Without a recorder, no trace is created."""
    session = create_session("5491100000002", "test objective no recorder")

    with (
        patch("app.agent.loop._register_session_tools", return_value=mock_skill_registry),
        patch("app.agent.loop._build_security_hitl_callback", return_value=None),
        patch("app.agent.loop.execute_worker", new_callable=AsyncMock, return_value="step result"),
        patch("app.agent.loop.create_plan") as mock_create_plan,
        patch("app.agent.loop.synthesize", new_callable=AsyncMock, return_value="final answer"),
        patch("app.agent.loop.replan", new_callable=AsyncMock, return_value=None),
        patch("app.agent.hitl.request_user_approval", new_callable=AsyncMock, return_value="sí"),
    ):
        mock_create_plan.return_value = AgentPlan(
            objective="test objective no recorder",
            context_summary="ctx",
            tasks=[TaskStep(id=1, description="step", worker_type="general")],
        )

        # Should complete without error even without recorder
        await run_agent_session(
            session=session,
            ollama_client=mock_ollama,
            skill_registry=mock_skill_registry,
            wa_client=mock_wa_client,
            recorder=None,
        )

    assert session.status == AgentStatus.COMPLETED


# ---------------------------------------------------------------------------
# Planner spans
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planner_creates_span(
    mock_recorder, mock_wa_client, mock_ollama, mock_skill_registry
):
    """run_agent_session creates planner:create_plan and planner:synthesize spans."""
    session = create_session("5491100000003", "test planner spans")

    span_names_created: list[str] = []

    async def capture_start_span(trace_id, span_id, name, kind, parent_id):
        span_names_created.append(name)

    mock_recorder.start_span = capture_start_span

    with (
        patch("app.agent.loop._register_session_tools", return_value=mock_skill_registry),
        patch("app.agent.loop._build_security_hitl_callback", return_value=None),
        patch("app.agent.loop.execute_worker", new_callable=AsyncMock, return_value="step result"),
        patch("app.agent.loop.create_plan") as mock_create_plan,
        patch("app.agent.loop.synthesize", new_callable=AsyncMock, return_value="final answer"),
        patch("app.agent.loop.replan", new_callable=AsyncMock, return_value=None),
        patch("app.agent.hitl.request_user_approval", new_callable=AsyncMock, return_value="sí"),
    ):
        mock_create_plan.return_value = AgentPlan(
            objective="test planner spans",
            context_summary="ctx",
            tasks=[TaskStep(id=1, description="step", worker_type="general")],
        )

        await run_agent_session(
            session=session,
            ollama_client=mock_ollama,
            skill_registry=mock_skill_registry,
            wa_client=mock_wa_client,
            recorder=mock_recorder,
        )

    assert "planner:create_plan" in span_names_created
    assert "planner:synthesize" in span_names_created


@pytest.mark.asyncio
async def test_worker_creates_span_with_parent(
    mock_recorder, mock_wa_client, mock_ollama, mock_skill_registry
):
    """Worker tasks create worker:task_N spans under the active trace."""
    session = create_session("5491100000004", "test worker spans")

    span_names_created: list[str] = []

    async def capture_start_span(trace_id, span_id, name, kind, parent_id):
        span_names_created.append(name)

    mock_recorder.start_span = capture_start_span

    with (
        patch("app.agent.loop._register_session_tools", return_value=mock_skill_registry),
        patch("app.agent.loop._build_security_hitl_callback", return_value=None),
        patch("app.agent.loop.execute_worker", new_callable=AsyncMock, return_value="step result"),
        patch("app.agent.loop.create_plan") as mock_create_plan,
        patch("app.agent.loop.synthesize", new_callable=AsyncMock, return_value="final answer"),
        patch("app.agent.loop.replan", new_callable=AsyncMock, return_value=None),
        patch("app.agent.hitl.request_user_approval", new_callable=AsyncMock, return_value="sí"),
    ):
        mock_create_plan.return_value = AgentPlan(
            objective="test worker spans",
            context_summary="ctx",
            tasks=[
                TaskStep(id=1, description="step one", worker_type="general"),
                TaskStep(id=2, description="step two", worker_type="reader"),
            ],
        )

        await run_agent_session(
            session=session,
            ollama_client=mock_ollama,
            skill_registry=mock_skill_registry,
            wa_client=mock_wa_client,
            recorder=mock_recorder,
        )

    worker_spans = [n for n in span_names_created if n.startswith("worker:task_")]
    assert len(worker_spans) == 2
    assert "worker:task_1" in worker_spans
    assert "worker:task_2" in worker_spans
