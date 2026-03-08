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
    """

    def __init__(self, repository, langfuse: Langfuse | None = None) -> None:
        self._repo = repository
        self.langfuse = langfuse

    @classmethod
    def create(cls, repository) -> TraceRecorder:
        """Factory: read Settings, initialize Langfuse once, return a TraceRecorder.

        Call this during app startup and store the result in app.state.trace_recorder.
        """
        settings = Settings()  # type: ignore[call-arg]
        langfuse: Langfuse | None = None
        if settings.langfuse_public_key and settings.langfuse_secret_key:
            try:
                # Validate API compatibility — Langfuse v3 removed trace()/span()/generation()
                if not hasattr(Langfuse, "trace"):
                    logger.warning(
                        "Langfuse SDK is incompatible (v3+ detected). "
                        "Pin langfuse<3.0.0 in requirements.txt. "
                        "Cloud upload disabled; SQLite tracing still active."
                    )
                else:
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
                self.langfuse.trace(
                    id=trace_id,
                    name="interaction",
                    user_id=phone_number,
                    session_id=phone_number,  # groups conversations by user in Langfuse Sessions
                    input=input_text,
                    metadata={"message_type": message_type, "platform": platform},
                )
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
                # Langfuse traces don't have a direct "end" state modification like spans,
                # but we can update the trace with the final output and tags
                self.langfuse.trace(
                    id=trace_id,
                    output=output_text,
                    tags=[status],
                )
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
                if kind == "generation":
                    self.langfuse.generation(
                        id=span_id,
                        trace_id=trace_id,
                        parent_observation_id=parent_id,
                        name=name,
                    )
                else:
                    self.langfuse.span(
                        id=span_id,
                        trace_id=trace_id,
                        parent_observation_id=parent_id,
                        name=name,
                    )
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
                level = "ERROR" if status == "failed" else "DEFAULT"
                md = dict(metadata) if metadata else {}

                # Extract OTel GenAI Semantic Conventions if present
                usage: dict[str, int] = {}
                in_tokens = md.pop("gen_ai.usage.input_tokens", None)
                out_tokens = md.pop("gen_ai.usage.output_tokens", None)
                if in_tokens is not None:
                    usage["input"] = in_tokens
                if out_tokens is not None:
                    usage["output"] = out_tokens
                model = md.pop("gen_ai.request.model", None)

                # Check what type of observation this was by trying to update context on it
                # The python SDK requires us to call the right method
                if usage or model:
                    self.langfuse.generation(
                        id=span_id,
                        output=output_data,
                        input=input_data,
                        level=level,
                        metadata=md,
                        model=model,
                        usage=usage,
                    )
                else:
                    self.langfuse.span(
                        id=span_id,
                        output=output_data,
                        input=input_data,
                        level=level,
                        metadata=md,
                    )
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
                self.langfuse.score(
                    trace_id=trace_id,
                    observation_id=span_id,
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
            self.langfuse.trace(id=trace_id, tags=tags)
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
