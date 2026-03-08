"""Auto-curation of traces into the eval dataset.

3-tier logic (evaluated after trace completes):
  - Failure  : any system guardrail score < 0.3 OR negative user signal (score < 0.3)
  - Golden   : all system scores high (>= 0.8) AND positive user signal (score >= 0.8)
  - Candidate: all system scores high, no user signal — saved as golden with confirmed=False

Correction pairs are handled separately (call add_correction_pair directly from router).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def maybe_curate_to_dataset(
    trace_id: str,
    input_text: str,
    output_text: str | None,
    repository: object,
    failed_check_names: list[str] | None = None,
    trace_recorder: object | None = None,
) -> None:
    """Auto-curate a completed trace to the eval dataset. Best-effort — never raises.

    failed_check_names: list of guardrail check names that failed (e.g. ["language_match"]).
    Used to populate guardrail:<name> tags on failure entries so the dataset is filterable.

    trace_recorder: optional TraceRecorder — if provided, golden/correction entries are also
    synced to Langfuse Datasets (best-effort).
    """
    try:
        scores = await repository.get_trace_scores(trace_id)  # type: ignore[attr-defined]
        if not scores:
            return

        system_scores = [s for s in scores if s["source"] == "system"]
        user_scores = [s for s in scores if s["source"] in ("user", "human")]

        any_system_failure = any(s["value"] < 0.3 for s in system_scores)
        all_system_high = system_scores and all(s["value"] >= 0.8 for s in system_scores)
        has_positive_user = any(s["value"] >= 0.8 for s in user_scores)
        has_negative_user = any(s["value"] < 0.3 for s in user_scores)

        # Failure takes priority — detecting problems is most valuable
        if any_system_failure or has_negative_user:
            failure_tags = (
                [f"guardrail:{name}" for name in failed_check_names] if failed_check_names else None
            )
            await repository.add_dataset_entry(  # type: ignore[attr-defined]
                trace_id=trace_id,
                entry_type="failure",
                input_text=input_text,
                output_text=output_text,
                tags=failure_tags,
            )
            logger.debug("Curated trace %s as failure (tags=%s)", trace_id, failure_tags)
            return

        # Golden confirmed: system OK + user confirmed quality
        if all_system_high and has_positive_user:
            await repository.add_dataset_entry(  # type: ignore[attr-defined]
                trace_id=trace_id,
                entry_type="golden",
                input_text=input_text,
                output_text=output_text,
                metadata={"confirmed": True},
            )
            logger.debug("Curated trace %s as golden (confirmed)", trace_id)
            if trace_recorder is not None:
                await trace_recorder.sync_dataset_to_langfuse(  # type: ignore[attr-defined]
                    dataset_name="localforge-eval",
                    input_text=input_text,
                    expected_output=output_text,
                    metadata={"entry_type": "golden", "trace_id": trace_id, "confirmed": True},
                )
            return

        # Golden candidate: system OK, no user signal yet
        if all_system_high and not user_scores:
            await repository.add_dataset_entry(  # type: ignore[attr-defined]
                trace_id=trace_id,
                entry_type="golden",
                input_text=input_text,
                output_text=output_text,
                metadata={"confirmed": False},
            )
            logger.debug("Curated trace %s as golden (candidate)", trace_id)

    except Exception:
        logger.exception("Failed to curate trace %s to dataset", trace_id)


async def add_correction_pair(
    previous_trace_id: str,
    input_text: str,
    bad_output: str | None,
    correction_text: str,
    repository: object,
    trace_recorder: object | None = None,
) -> None:
    """Save a correction pair: previous (bad) trace + user correction as expected output.

    Called from router when a high-confidence correction is detected.

    trace_recorder: optional TraceRecorder — if provided, also syncs to Langfuse Datasets.
    """
    try:
        await repository.add_dataset_entry(  # type: ignore[attr-defined]
            trace_id=previous_trace_id,
            entry_type="correction",
            input_text=input_text,
            output_text=bad_output,
            expected_output=correction_text,
        )
        logger.debug("Saved correction pair for trace %s", previous_trace_id)
        if trace_recorder is not None:
            await trace_recorder.sync_dataset_to_langfuse(  # type: ignore[attr-defined]
                dataset_name="localforge-eval",
                input_text=input_text,
                expected_output=correction_text,
                metadata={
                    "entry_type": "correction",
                    "trace_id": previous_trace_id,
                    "confirmed": True,
                },
            )
    except Exception:
        logger.exception("Failed to save correction pair for trace %s", previous_trace_id)
