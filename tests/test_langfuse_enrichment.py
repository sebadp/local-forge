"""Tests for Phase 6 Langfuse enrichment: session_id, tags, and dataset sync."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# TraceRecorder.start_trace — session_id and platform
# ---------------------------------------------------------------------------


async def test_start_trace_sends_session_id():
    """start_trace must call start_span with trace_context and update_trace with user/session metadata."""
    mock_langfuse = MagicMock()
    # Mock the span returned by start_span
    mock_span = MagicMock()
    mock_langfuse.start_span.return_value = mock_span

    repo = AsyncMock()
    repo.save_trace = AsyncMock()

    from app.tracing.recorder import TraceRecorder

    with patch("app.tracing.recorder.Langfuse.create_trace_id", return_value="a" * 32):
        recorder = TraceRecorder(repository=repo, langfuse=mock_langfuse)
        await recorder.start_trace(
            trace_id="abc123",
            phone_number="+5491234567890",
            input_text="hello",
            message_type="text",
            platform="whatsapp",
        )

    mock_langfuse.start_span.assert_called_once()
    mock_span.update_trace.assert_called_once()
    call_kwargs = mock_span.update_trace.call_args.kwargs
    assert call_kwargs.get("session_id") == "+5491234567890"
    assert call_kwargs.get("user_id") == "+5491234567890"
    assert call_kwargs.get("metadata", {}).get("platform") == "whatsapp"


async def test_start_trace_platform_telegram():
    """Platform tag 'telegram' is forwarded to root span's update_trace metadata."""
    mock_langfuse = MagicMock()
    mock_span = MagicMock()
    mock_langfuse.start_span.return_value = mock_span

    repo = AsyncMock()
    repo.save_trace = AsyncMock()

    from app.tracing.recorder import TraceRecorder

    with patch("app.tracing.recorder.Langfuse.create_trace_id", return_value="b" * 32):
        recorder = TraceRecorder(repository=repo, langfuse=mock_langfuse)
        await recorder.start_trace(
            trace_id="def456",
            phone_number="tg_99999",
            input_text="hola",
            message_type="text",
            platform="telegram",
        )

    call_kwargs = mock_span.update_trace.call_args.kwargs
    assert call_kwargs.get("metadata", {}).get("platform") == "telegram"
    assert call_kwargs.get("session_id") == "tg_99999"


# ---------------------------------------------------------------------------
# TraceRecorder.update_trace_tags
# ---------------------------------------------------------------------------


async def test_update_trace_tags_called():
    """update_trace_tags must call root_span.update_trace with the tags."""
    mock_langfuse = MagicMock()
    repo = AsyncMock()

    from app.tracing.recorder import TraceRecorder

    recorder = TraceRecorder(repository=repo, langfuse=mock_langfuse)

    # Inject a fake root span
    mock_root_span = MagicMock()
    recorder._root_spans["trace_xyz"] = mock_root_span

    await recorder.update_trace_tags("trace_xyz", ["whatsapp", "math", "time"])

    mock_root_span.update_trace.assert_called_once_with(tags=["whatsapp", "math", "time"])


async def test_update_trace_tags_noop_without_langfuse():
    """update_trace_tags must be a no-op (no error) when no Langfuse client."""
    repo = AsyncMock()

    from app.tracing.recorder import TraceRecorder

    recorder = TraceRecorder(repository=repo, langfuse=None)
    # Must not raise
    await recorder.update_trace_tags("some_trace", ["math"])


async def test_update_trace_tags_noop_empty_tags():
    """update_trace_tags must skip langfuse call when tags list is empty."""
    mock_langfuse = MagicMock()
    repo = AsyncMock()

    from app.tracing.recorder import TraceRecorder

    recorder = TraceRecorder(repository=repo, langfuse=mock_langfuse)
    mock_root_span = MagicMock()
    recorder._root_spans["trace_xyz"] = mock_root_span

    await recorder.update_trace_tags("trace_xyz", [])

    mock_root_span.update_trace.assert_not_called()


# ---------------------------------------------------------------------------
# TraceRecorder.sync_dataset_to_langfuse
# ---------------------------------------------------------------------------


async def test_sync_dataset_golden_to_langfuse():
    """sync_dataset_to_langfuse must call langfuse.create_dataset_item with correct args."""
    mock_langfuse = MagicMock()
    repo = AsyncMock()

    from app.tracing.recorder import TraceRecorder

    recorder = TraceRecorder(repository=repo, langfuse=mock_langfuse)
    await recorder.sync_dataset_to_langfuse(
        dataset_name="localforge-eval",
        input_text="¿Cuánto es 2+2?",
        expected_output="4",
        metadata={"entry_type": "golden", "trace_id": "abc", "confirmed": True},
    )

    mock_langfuse.create_dataset_item.assert_called_once()
    call_kwargs = mock_langfuse.create_dataset_item.call_args.kwargs
    assert call_kwargs["dataset_name"] == "localforge-eval"
    assert call_kwargs["input"] == {"text": "¿Cuánto es 2+2?"}
    assert call_kwargs["expected_output"] == {"text": "4"}
    assert call_kwargs["metadata"]["entry_type"] == "golden"


async def test_sync_dataset_noop_without_langfuse():
    """sync_dataset_to_langfuse must be a no-op when no Langfuse client."""
    repo = AsyncMock()

    from app.tracing.recorder import TraceRecorder

    recorder = TraceRecorder(repository=repo, langfuse=None)
    # Must not raise
    await recorder.sync_dataset_to_langfuse(
        dataset_name="localforge-eval",
        input_text="test",
        expected_output="expected",
    )


async def test_sync_dataset_none_expected_output():
    """sync_dataset_to_langfuse handles None expected_output gracefully."""
    mock_langfuse = MagicMock()
    repo = AsyncMock()

    from app.tracing.recorder import TraceRecorder

    recorder = TraceRecorder(repository=repo, langfuse=mock_langfuse)
    await recorder.sync_dataset_to_langfuse(
        dataset_name="localforge-eval",
        input_text="test input",
        expected_output=None,
    )

    call_kwargs = mock_langfuse.create_dataset_item.call_args.kwargs
    assert call_kwargs["expected_output"] is None


# ---------------------------------------------------------------------------
# maybe_curate_to_dataset — golden triggers Langfuse sync
# ---------------------------------------------------------------------------


async def test_maybe_curate_golden_syncs_to_langfuse():
    """Golden entries must trigger sync_dataset_to_langfuse on the trace_recorder."""
    repo = AsyncMock()
    # Simulate all-system-high + positive user signal
    repo.get_trace_scores = AsyncMock(
        return_value=[
            {"source": "system", "name": "not_empty", "value": 1.0},
            {"source": "user", "name": "thumbs_up", "value": 1.0},
        ]
    )
    repo.add_dataset_entry = AsyncMock()

    recorder = AsyncMock()
    recorder.sync_dataset_to_langfuse = AsyncMock()

    from app.eval.dataset import maybe_curate_to_dataset

    await maybe_curate_to_dataset(
        trace_id="trace_golden",
        input_text="test question",
        output_text="correct answer",
        repository=repo,
        trace_recorder=recorder,
    )

    recorder.sync_dataset_to_langfuse.assert_called_once()
    call_kwargs = recorder.sync_dataset_to_langfuse.call_args.kwargs
    assert call_kwargs["dataset_name"] == "localforge-eval"
    assert call_kwargs["input_text"] == "test question"
    assert call_kwargs["expected_output"] == "correct answer"


async def test_maybe_curate_failure_does_not_sync_to_langfuse():
    """Failure entries must NOT trigger sync_dataset_to_langfuse."""
    repo = AsyncMock()
    repo.get_trace_scores = AsyncMock(
        return_value=[
            {"source": "system", "name": "not_empty", "value": 0.0},  # failure
        ]
    )
    repo.add_dataset_entry = AsyncMock()

    recorder = AsyncMock()
    recorder.sync_dataset_to_langfuse = AsyncMock()

    from app.eval.dataset import maybe_curate_to_dataset

    await maybe_curate_to_dataset(
        trace_id="trace_fail",
        input_text="test",
        output_text="bad output",
        repository=repo,
        trace_recorder=recorder,
    )

    recorder.sync_dataset_to_langfuse.assert_not_called()


async def test_maybe_curate_without_recorder_no_error():
    """maybe_curate_to_dataset must not raise when trace_recorder is None."""
    repo = AsyncMock()
    repo.get_trace_scores = AsyncMock(
        return_value=[
            {"source": "system", "name": "not_empty", "value": 1.0},
            {"source": "user", "name": "thumbs_up", "value": 1.0},
        ]
    )
    repo.add_dataset_entry = AsyncMock()

    from app.eval.dataset import maybe_curate_to_dataset

    # Must not raise
    await maybe_curate_to_dataset(
        trace_id="trace_no_recorder",
        input_text="test",
        output_text="answer",
        repository=repo,
        trace_recorder=None,
    )
