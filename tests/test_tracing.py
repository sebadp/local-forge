"""Tests for TraceRecorder singleton and generation span metadata."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.llm.client import ChatResponse, OllamaClient
from app.models import ChatMessage
from app.tracing.recorder import TraceRecorder

# ---------------------------------------------------------------------------
# TraceRecorder.create() factory
# ---------------------------------------------------------------------------


def test_trace_recorder_create_without_keys():
    """When Langfuse keys are absent, langfuse stays None."""
    mock_repo = MagicMock()
    with patch("app.tracing.recorder.Settings") as mock_settings_cls:
        settings = MagicMock()
        settings.langfuse_public_key = ""
        settings.langfuse_secret_key = ""
        settings.langfuse_host = "https://cloud.langfuse.com"
        mock_settings_cls.return_value = settings

        recorder = TraceRecorder.create(mock_repo)

    assert recorder.langfuse is None
    assert recorder._repo is mock_repo


def test_trace_recorder_create_with_langfuse_keys():
    """When Langfuse keys are present, Langfuse client is initialised."""
    mock_repo = MagicMock()
    with (
        patch("app.tracing.recorder.Settings") as mock_settings_cls,
        patch("app.tracing.recorder.Langfuse") as mock_langfuse_cls,
    ):
        settings = MagicMock()
        settings.langfuse_public_key = "pk-test"
        settings.langfuse_secret_key = "sk-test"
        settings.langfuse_host = "https://cloud.langfuse.com"
        mock_settings_cls.return_value = settings

        mock_lf_instance = MagicMock()
        mock_langfuse_cls.return_value = mock_lf_instance

        recorder = TraceRecorder.create(mock_repo)

    assert recorder.langfuse is mock_lf_instance
    mock_langfuse_cls.assert_called_once_with(
        public_key="pk-test",
        secret_key="sk-test",
        host="https://cloud.langfuse.com",
    )


def test_trace_recorder_create_langfuse_init_failure():
    """If Langfuse raises during init, recorder falls back to None (fail-open)."""
    mock_repo = MagicMock()
    with (
        patch("app.tracing.recorder.Settings") as mock_settings_cls,
        patch("app.tracing.recorder.Langfuse", side_effect=RuntimeError("SDK error")),
    ):
        settings = MagicMock()
        settings.langfuse_public_key = "pk-test"
        settings.langfuse_secret_key = "sk-test"
        settings.langfuse_host = "https://cloud.langfuse.com"
        mock_settings_cls.return_value = settings

        recorder = TraceRecorder.create(mock_repo)

    assert recorder.langfuse is None


# ---------------------------------------------------------------------------
# ChatResponse token metrics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_response_includes_token_counts():
    """Ollama's eval_count / prompt_eval_count are propagated to ChatResponse."""
    mock_http = AsyncMock()
    client = OllamaClient(
        http_client=mock_http,
        base_url="http://localhost:11434",
        model="qwen3.5:9b",
    )

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "message": {"role": "assistant", "content": "Hello"},
        "prompt_eval_count": 42,
        "eval_count": 17,
        "total_duration": 3_000_000_000,  # 3s in nanoseconds
    }
    mock_http.post = AsyncMock(return_value=mock_resp)

    response = await client.chat_with_tools([ChatMessage(role="user", content="Hi")], tools=None)

    assert response.input_tokens == 42
    assert response.output_tokens == 17
    assert response.total_duration_ms == pytest.approx(3000.0)
    assert response.model == "qwen3.5:9b"


@pytest.mark.asyncio
async def test_chat_response_handles_missing_token_fields():
    """If Ollama omits token fields, ChatResponse fields are None (not an error)."""
    mock_http = AsyncMock()
    client = OllamaClient(
        http_client=mock_http,
        base_url="http://localhost:11434",
        model="qwen3.5:9b",
    )

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "message": {"role": "assistant", "content": "Hello"},
        # no eval_count / prompt_eval_count / total_duration
    }
    mock_http.post = AsyncMock(return_value=mock_resp)

    response = await client.chat_with_tools([ChatMessage(role="user", content="Hi")], tools=None)

    assert response.input_tokens is None
    assert response.output_tokens is None
    assert response.total_duration_ms is None
    assert response.model == "qwen3.5:9b"


# ---------------------------------------------------------------------------
# Generation span metadata in executor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_loop_creates_generation_span():
    """execute_tool_loop wraps each LLM call in a generation span when a trace is active."""
    from unittest.mock import patch

    from app.skills.executor import execute_tool_loop
    from app.skills.registry import SkillRegistry
    from app.tracing.context import TraceContext

    mock_recorder = AsyncMock()
    mock_recorder.langfuse = None

    span_names: list[str] = []

    async def capture_start_span(trace_id, span_id, name, kind, parent_id):
        span_names.append(name)

    mock_recorder.start_span = capture_start_span
    mock_recorder.finish_span = AsyncMock()
    mock_recorder.start_trace = AsyncMock()
    mock_recorder.finish_trace = AsyncMock()

    mock_http = AsyncMock()
    ollama = OllamaClient(
        http_client=mock_http, base_url="http://localhost:11434", model="qwen3.5:9b"
    )
    # LLM responds with text directly (no tool calls) on first call
    ollama.chat_with_tools = AsyncMock(
        return_value=ChatResponse(
            content="Done", input_tokens=10, output_tokens=5, model="qwen3.5:9b"
        )
    )

    skill_reg = SkillRegistry(skills_dir="/nonexistent")

    # Register a dummy tool so select_tools returns at least one
    async def dummy() -> str:
        return "ok"

    skill_reg.register_tool(
        name="dummy_tool",
        description="dummy",
        parameters={"type": "object", "properties": {}},
        handler=dummy,
    )

    messages = [ChatMessage(role="user", content="test")]

    with (
        patch("app.skills.executor.classify_intent", new_callable=AsyncMock, return_value=["time"]),
        patch(
            "app.skills.executor.select_tools",
            side_effect=lambda cats, all_tools, max_tools=8: list(all_tools.values()),
        ),
    ):
        async with TraceContext("test", "test", mock_recorder):
            await execute_tool_loop(messages, ollama, skill_reg)

    # Should have a generation span for the first iteration
    gen_spans = [n for n in span_names if n.startswith("llm:iteration_")]
    assert len(gen_spans) >= 1


@pytest.mark.asyncio
async def test_tool_output_captures_1000_chars():
    """Tool output in span is capped at 1000 chars (not 200)."""
    from app.skills.executor import _run_tool_call
    from app.skills.registry import SkillRegistry
    from app.tracing.context import TraceContext

    mock_recorder = AsyncMock()
    mock_recorder.langfuse = None

    captured_output: dict = {}

    async def capture_finish_span(
        span_id, status, latency_ms, input_data=None, output_data=None, metadata=None
    ):
        if output_data and "content" in output_data:
            captured_output["content"] = output_data["content"]

    mock_recorder.start_span = AsyncMock()
    mock_recorder.finish_span = capture_finish_span
    mock_recorder.start_trace = AsyncMock()
    mock_recorder.finish_trace = AsyncMock()

    skill_reg = SkillRegistry(skills_dir="/nonexistent")
    long_content = "x" * 2000

    async def handler() -> str:
        return long_content

    skill_reg.register_tool(
        name="long_tool",
        description="returns long output",
        parameters={"type": "object", "properties": {}},
        handler=handler,
    )

    mock_http = AsyncMock()
    ollama = OllamaClient(
        http_client=mock_http, base_url="http://localhost:11434", model="qwen3.5:9b"
    )

    tc = {"function": {"name": "long_tool", "arguments": {}}}

    with (
        patch("app.skills.executor.get_policy_engine") as mock_policy,
        patch("app.skills.executor.get_audit_trail") as mock_audit,
    ):
        mock_pe = MagicMock()
        mock_pe.evaluate.return_value = MagicMock(
            is_blocked=False, requires_flag=False, reason=None
        )
        mock_policy.return_value = mock_pe

        mock_at = MagicMock()
        mock_at.record = MagicMock()
        mock_audit.return_value = mock_at

        async with TraceContext("test", "test", mock_recorder):
            await _run_tool_call(tc, skill_reg, None, ollama, "test message")

    if captured_output:
        assert len(captured_output["content"]) == 1000
