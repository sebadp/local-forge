"""TraceRecorder: async SQLite persistence for traces and spans. Best-effort."""

from __future__ import annotations

import logging
from typing import Any

from langfuse import Langfuse

from app.config import Settings

logger = logging.getLogger(__name__)


class TraceRecorder:
    """Persists trace data to SQLite via the shared Repository, and optionally to Langfuse.

    All methods are best-effort: exceptions are caught and logged, never propagated.

    Use `TraceRecorder.create(repository)` to build the singleton instance at app startup.
    The constructor accepts a pre-initialized Langfuse client (or None) so the singleton
    can be reused across requests without creating a new background-flush thread each time.

    Langfuse v3 API: uses start_span()/start_generation() which return stateful span objects.
    Active spans are stored in _active_spans (span_id → LangfuseSpan) and root spans are
    stored in _root_spans (trace_id → LangfuseSpan) for the lifetime of a trace.
    """

    def __init__(self, repository, langfuse: Langfuse | None = None) -> None:
        self._repo = repository
        self.langfuse = langfuse
        # Stateful span storage: our_span_id → LangfuseSpan object
        self._active_spans: dict[str, Any] = {}
        # Root spans: our_trace_id → LangfuseSpan (the root span representing the trace)
        self._root_spans: dict[str, Any] = {}

    @classmethod
    def create(cls, repository) -> TraceRecorder:
        """Factory: read Settings, initialize Langfuse once, return a TraceRecorder.

        Call this during app startup and store the result in app.state.trace_recorder.
        """
        settings = Settings()  # type: ignore[call-arg]
        langfuse: Langfuse | None = None
        if settings.langfuse_public_key and settings.langfuse_secret_key:
            try:
                langfuse = Langfuse(
                    public_key=settings.langfuse_public_key,
                    secret_key=settings.langfuse_secret_key,
                    host=settings.langfuse_host,
                )
                logger.info("Langfuse tracing enabled")
            except Exception:
                logger.warning("Failed to initialize Langfuse client", exc_info=True)
        return cls(repository, langfuse)

    async def start_trace(
        self,
        trace_id: str,
        phone_number: str,
        input_text: str,
        message_type: str = "text",
        platform: str = "whatsapp",
    ) -> None:
        try:
            await self._repo.save_trace(trace_id, phone_number, input_text, message_type)
            if self.langfuse:
                # Create deterministic Langfuse trace_id from our UUID (must be 32 hex chars)
                lf_trace_id = Langfuse.create_trace_id(seed=trace_id)
                # Create root span representing the trace
                root_span = self.langfuse.start_span(
                    trace_context={"trace_id": lf_trace_id},
                    name="interaction",
                    input=input_text,
                )
                # Set trace metadata (user, session, platform, etc.)
                root_span.update_trace(
                    user_id=phone_number,
                    session_id=phone_number,
                    metadata={"message_type": message_type, "platform": platform},
                )
                self._root_spans[trace_id] = root_span
        except Exception:
            logger.warning("TraceRecorder.start_trace failed", exc_info=True)

    async def finish_trace(
        self,
        trace_id: str,
        status: str,
        output_text: str | None = None,
        wa_message_id: str | None = None,
    ) -> None:
        try:
            await self._repo.finish_trace(trace_id, status, output_text, wa_message_id)
            if self.langfuse:
                root_span = self._root_spans.pop(trace_id, None)
                if root_span:
                    root_span.update_trace(output=output_text, tags=[status])
                    root_span.end()
                self.langfuse.flush()
        except Exception:
            logger.warning("TraceRecorder.finish_trace failed", exc_info=True)

    async def start_span(
        self,
        trace_id: str,
        span_id: str,
        name: str,
        kind: str,
        parent_id: str | None,
    ) -> None:
        try:
            await self._repo.save_trace_span(span_id, trace_id, name, kind, parent_id)
            if self.langfuse:
                root_span = self._root_spans.get(trace_id)
                if root_span is None:
                    return
                # Get parent span object if there is one
                parent = self._active_spans.get(parent_id) if parent_id else None
                if parent is None:
                    parent = root_span
                # Create child span under parent
                if kind == "generation":
                    span = parent.start_generation(name=name)
                else:
                    span = parent.start_span(name=name)
                self._active_spans[span_id] = span
        except Exception:
            logger.warning("TraceRecorder.start_span failed", exc_info=True)

    async def finish_span(
        self,
        span_id: str,
        status: str,
        latency_ms: float,
        input_data: Any = None,
        output_data: Any = None,
        metadata: dict | None = None,
    ) -> None:
        try:
            await self._repo.finish_trace_span(
                span_id,
                status,
                latency_ms,
                input_data,
                output_data,
                metadata,
            )
            if self.langfuse:
                span = self._active_spans.pop(span_id, None)
                if span is None:
                    return
                level = "ERROR" if status == "failed" else "DEFAULT"
                md = dict(metadata) if metadata else {}

                # Extract OTel GenAI Semantic Conventions if present
                usage_details: dict[str, int] = {}
                in_tokens = md.pop("gen_ai.usage.input_tokens", None)
                out_tokens = md.pop("gen_ai.usage.output_tokens", None)
                if in_tokens is not None:
                    usage_details["input"] = in_tokens
                if out_tokens is not None:
                    usage_details["output"] = out_tokens
                model = md.pop("gen_ai.request.model", None)

                span.update(
                    input=input_data,
                    output=output_data,
                    level=level,
                    metadata=md if md else None,
                    model=model,
                    usage_details=usage_details if usage_details else None,
                )
                span.end()
        except Exception:
            logger.warning("TraceRecorder.finish_span failed", exc_info=True)

    async def add_score(
        self,
        trace_id: str,
        name: str,
        value: float,
        source: str = "system",
        comment: str | None = None,
        span_id: str | None = None,
    ) -> None:
        try:
            await self._repo.save_trace_score(trace_id, name, value, source, comment, span_id)
            if self.langfuse:
                root_span = self._root_spans.get(trace_id)
                if root_span is None:
                    return
                # Get observation_id if we have the span still active
                observation_id = None
                if span_id:
                    active_span = self._active_spans.get(span_id)
                    if active_span:
                        observation_id = active_span.id
                self.langfuse.create_score(
                    trace_id=root_span.trace_id,
                    observation_id=observation_id,
                    name=name,
                    value=value,
                    comment=comment,
                )
        except Exception:
            logger.warning("TraceRecorder.add_score failed", exc_info=True)

    async def update_trace_tags(self, trace_id: str, tags: list[str]) -> None:
        """Upsert tags on an existing Langfuse trace. Best-effort, no-op if no Langfuse."""
        if not self.langfuse or not tags:
            return
        try:
            root_span = self._root_spans.get(trace_id)
            if root_span:
                root_span.update_trace(tags=tags)
        except Exception:
            logger.warning("TraceRecorder.update_trace_tags failed", exc_info=True)

    async def sync_dataset_to_langfuse(
        self,
        dataset_name: str,
        input_text: str,
        expected_output: str | None,
        metadata: dict | None = None,
    ) -> None:
        """Push a dataset entry to Langfuse Datasets. Best-effort, no-op if no Langfuse."""
        if not self.langfuse:
            return
        try:
            self.langfuse.create_dataset_item(
                dataset_name=dataset_name,
                input={"text": input_text},
                expected_output={"text": expected_output} if expected_output else None,
                metadata=metadata or {},
            )
        except Exception:
            logger.warning("TraceRecorder.sync_dataset_to_langfuse failed", exc_info=True)

    async def set_trace_output(self, trace_id: str, output_text: str) -> None:
        # Output is set when finishing the trace; this is a no-op that can be
        # called mid-stream to cache the value before __aexit__
        pass  # stored in TraceContext._output_text

    async def set_trace_wa_message_id(self, trace_id: str, wa_message_id: str) -> None:
        # wa_message_id is set when finishing the trace; same pattern
        pass  # stored in TraceContext._wa_message_id
