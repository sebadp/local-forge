"""Guardrail pipeline: orchestrates all checks and returns a consolidated report."""

from __future__ import annotations

import asyncio
import logging
import time

from app.guardrails.checks import (
    check_excessive_length,
    check_language_match,
    check_no_pii,
    check_no_raw_tool_json,
    check_not_empty,
)
from app.guardrails.models import GuardrailReport, GuardrailResult

logger = logging.getLogger(__name__)


async def run_guardrails(
    user_text: str,
    reply: str,
    tool_calls_used: bool = False,
    settings=None,
    ollama_client=None,
) -> GuardrailReport:
    """Run all enabled guardrail checks (deterministic + optional LLM-based).

    Returns a GuardrailReport. Errors in individual checks are caught and
    treated as passing (fail open) to avoid blocking the response.
    """
    start = time.monotonic()
    results: list[GuardrailResult] = []

    # Always-on deterministic checks
    _run_check(results, check_not_empty, reply)
    _run_check(results, check_excessive_length, reply)
    _run_check(results, check_no_raw_tool_json, reply)

    # Language check (configurable)
    language_enabled = settings is None or getattr(settings, "guardrails_language_check", True)
    if language_enabled:
        default_lang = getattr(settings, "guardrails_default_language", None) if settings else None
        _run_check(results, check_language_match, user_text, reply, default_lang)

    # PII check (configurable)
    pii_enabled = settings is None or getattr(settings, "guardrails_pii_check", True)
    if pii_enabled:
        _run_check(results, check_no_pii, user_text, reply)

    # LLM-based checks (opt-in via guardrails_llm_checks, require ollama_client)
    llm_checks_enabled = settings is not None and getattr(settings, "guardrails_llm_checks", False)
    if llm_checks_enabled and ollama_client is not None:
        from app.guardrails.checks import check_hallucination, check_tool_coherence

        llm_timeout = getattr(settings, "guardrails_llm_timeout", 3.0) if settings else 3.0
        if tool_calls_used:
            await _run_async_check(
                results,
                "tool_coherence",
                check_tool_coherence(user_text, reply, ollama_client),
                timeout=llm_timeout,
            )
        await _run_async_check(
            results,
            "hallucination_check",
            check_hallucination(user_text, reply, ollama_client),
            timeout=llm_timeout,
        )

    total_latency_ms = (time.monotonic() - start) * 1000
    passed = all(r.passed for r in results)

    if not passed:
        failed = [r.check_name for r in results if not r.passed]
        logger.warning("Guardrail checks failed: %s (total=%.1fms)", failed, total_latency_ms)

    return GuardrailReport(
        passed=passed,
        results=results,
        total_latency_ms=total_latency_ms,
    )


def _run_check(results: list[GuardrailResult], fn, *args) -> None:
    """Run a sync check function, appending its result. Catches all exceptions (fail open)."""
    try:
        result = fn(*args)
        results.append(result)
    except Exception as e:
        logger.warning("Guardrail check %s raised: %s", fn.__name__, e)
        # Fail open: treat as passing so we don't block the response
        results.append(
            GuardrailResult(
                passed=True,
                check_name=fn.__name__.replace("check_", ""),
                details=f"check raised exception: {e}",
            )
        )


async def _run_async_check(
    results: list[GuardrailResult],
    check_name: str,
    coro,
    timeout: float = 0.5,
) -> None:
    """Run an async check coroutine with a timeout. Fail open on error or timeout."""
    try:
        result = await asyncio.wait_for(coro, timeout=timeout)
        results.append(result)
    except TimeoutError:
        logger.warning(
            "Async guardrail check '%s' timed out (>%.0fms)",
            check_name,
            timeout * 1000,
        )
        results.append(
            GuardrailResult(
                passed=True,
                check_name=check_name,
                details="check timed out",
            )
        )
    except Exception as e:
        logger.warning("Async guardrail check '%s' raised: %s", check_name, e)
        results.append(
            GuardrailResult(
                passed=True,
                check_name=check_name,
                details=f"check raised exception: {e}",
            )
        )
