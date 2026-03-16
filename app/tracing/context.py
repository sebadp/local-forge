"""TraceContext: async context manager that tracks a single interaction trace.

Uses contextvars to propagate the current trace through asyncio tasks without
changing function signatures.
"""

from __future__ import annotations

import contextvars
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.tracing.recorder import TraceRecorder

# Module-level contextvar: holds the current TraceContext for the running Task.
# asyncio.create_task() copies the context, so sub-tasks inherit the trace automatically.
_current_trace: contextvars.ContextVar[TraceContext | None] = contextvars.ContextVar(
    "current_trace",
    default=None,
)


def get_current_trace() -> TraceContext | None:
    """Get the TraceContext for the currently running asyncio Task."""
    return _current_trace.get()


class SpanData:
    """Mutable data bag for a span. Updated via set_* methods during span execution."""

    def __init__(self, span_id: str, name: str, kind: str) -> None:
        self.span_id = span_id
        self.name = name
        self.kind = kind
        self._status = "completed"
        self._input: dict | None = None
        self._output: dict | None = None
        self._metadata: dict = {}

    def set_input(self, data: dict) -> None:
        self._input = data

    def set_output(self, data: dict) -> None:
        self._output = data

    def set_metadata(self, data: dict) -> None:
        self._metadata.update(data)

    def set_model(self, model: str) -> None:
        """Convenience: set the model name for this span (OTel GenAI convention)."""
        self._metadata["gen_ai.request.model"] = model


class TraceContext:
    """Context manager for a single interaction trace.

    Usage:
        async with TraceContext(phone, text, recorder) as trace:
            async with trace.span("phase_a") as span:
                span.set_input({"query": "..."})
                ...
            await trace.set_output(reply)
            await trace.set_wa_message_id(wa_id)
    """

    def __init__(
        self,
        phone_number: str,
        input_text: str,
        recorder: TraceRecorder,
        message_type: str = "text",
        platform: str = "whatsapp",
        metadata: dict | None = None,
    ) -> None:
        self.trace_id = uuid.uuid4().hex
        self.phone_number = phone_number
        self.input_text = input_text
        self.message_type = message_type
        self.platform = platform
        self._recorder = recorder
        self._metadata = metadata
        self._token: contextvars.Token | None = None
        self._output_text: str | None = None
        self._wa_message_id: str | None = None

    async def __aenter__(self) -> TraceContext:
        self._token = _current_trace.set(self)
        await self._recorder.start_trace(
            self.trace_id,
            self.phone_number,
            self.input_text,
            self.message_type,
            platform=self.platform,
            metadata=self._metadata,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        status = "failed" if exc_type else "completed"
        await self._recorder.finish_trace(
            self.trace_id,
            status,
            self._output_text,
            self._wa_message_id,
        )
        if self._token is not None:
            _current_trace.reset(self._token)
        return False  # don't swallow exceptions

    @asynccontextmanager
    async def span(
        self,
        name: str,
        kind: str = "span",
        parent_id: str | None = None,
    ) -> AsyncIterator[SpanData]:
        span_id = uuid.uuid4().hex
        start = time.monotonic()
        span_data = SpanData(span_id=span_id, name=name, kind=kind)
        await self._recorder.start_span(self.trace_id, span_id, name, kind, parent_id)
        try:
            yield span_data
        except Exception:
            span_data._status = "failed"
            raise
        finally:
            latency_ms = (time.monotonic() - start) * 1000
            # Inject span kind so recorder can apply model fallback for generations
            final_metadata = dict(span_data._metadata)
            if kind == "generation":
                final_metadata.setdefault("_span_kind", "generation")
            await self._recorder.finish_span(
                span_id,
                span_data._status,
                latency_ms,
                input_data=span_data._input,
                output_data=span_data._output,
                metadata=final_metadata if final_metadata else None,
            )

    async def add_score(
        self,
        name: str,
        value: float,
        source: str = "system",
        comment: str | None = None,
        span_id: str | None = None,
    ) -> None:
        await self._recorder.add_score(
            self.trace_id,
            name,
            value,
            source,
            comment,
            span_id,
        )

    def set_output(self, output_text: str) -> None:
        """Cache the output text to be saved when the trace completes."""
        self._output_text = output_text

    def set_wa_message_id(self, wa_message_id: str) -> None:
        """Cache the wa_message_id to be saved when the trace completes."""
        self._wa_message_id = wa_message_id
