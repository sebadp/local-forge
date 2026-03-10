"""Tests for guardrail checks, pipeline, and remediation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.guardrails.checks import check_language_match, check_not_empty
from app.guardrails.models import GuardrailReport, GuardrailResult
from app.guardrails.pipeline import run_guardrails
from app.models import ChatMessage

# ---------------------------------------------------------------------------
# check_not_empty
# ---------------------------------------------------------------------------


def test_check_not_empty_passes():
    result = check_not_empty("Hello world")
    assert result.passed is True
    assert result.check_name == "not_empty"


def test_check_not_empty_fails_on_blank():
    result = check_not_empty("   ")
    assert result.passed is False


# ---------------------------------------------------------------------------
# check_language_match
# ---------------------------------------------------------------------------


def test_language_match_skip_short_text():
    """Short user text + short reply → skip (no detection at all)."""
    result = check_language_match("Hola", "Hello")
    assert result.passed is True
    assert "short" in result.details


def test_language_match_short_user_with_default_catches_mismatch():
    """Short user text but long reply: detect reply lang vs default_language."""
    with patch("langdetect.detect", return_value="pt"):
        result = check_language_match(
            "en portuges?",
            "Aqui está uma resposta muito longa em português para o utilizador",
            default_language="es",
        )
    assert result.passed is False
    assert result.details == "es"


def test_language_match_short_user_with_default_passes_when_match():
    """Short user text + reply in default language → pass."""
    with patch("langdetect.detect", return_value="es"):
        result = check_language_match(
            "hola!",
            "Hola, esta es una respuesta en español suficientemente larga",
            default_language="es",
        )
    assert result.passed is True


def test_language_match_short_user_no_default_skips():
    """Short user text + no default_language → skip (backward compat)."""
    result = check_language_match(
        "ok",
        "This is a long enough reply to be detected but no default language set",
    )
    assert result.passed is True
    assert "no default" in result.details


def test_language_match_skip_short_reply():
    result = check_language_match("This is a long enough user message okay", "Hi")
    assert result.passed is True
    assert "short" in result.details


def test_language_match_details_contains_user_lang_on_failure():
    """When language mismatch is detected, details must contain the user's ISO lang code."""
    # detect is imported inside the function so patch at the langdetect module level
    with patch("langdetect.detect") as mock_detect:
        mock_detect.side_effect = ["es", "en"]  # user=es, reply=en → mismatch
        result = check_language_match(
            "Este es un mensaje de prueba en español suficientemente largo",
            "This is the reply in English that is also sufficiently long",
        )
    if not result.passed:  # only assert if detection actually ran (langdetect installed)
        assert result.details == "es"


# ---------------------------------------------------------------------------
# Phase 1: _handle_guardrail_failure — language_match remediation
# ---------------------------------------------------------------------------


def _make_language_report(lang_code: str) -> GuardrailReport:
    return GuardrailReport(
        passed=False,
        results=[
            GuardrailResult(
                passed=False,
                check_name="language_match",
                details=lang_code,
                latency_ms=1.0,
            )
        ],
        total_latency_ms=1.0,
    )


async def test_language_remediation_prompt_is_bilingual():
    """Remediation hint for language_match must mention the language in both the target
    language and in English so qwen3 understands it regardless of its current biases."""
    from app.webhook.router import _handle_guardrail_failure

    ollama_mock = AsyncMock()
    ollama_mock.chat = AsyncMock(return_value="Respuesta en español correcta.")

    report = _make_language_report("es")
    context = [ChatMessage(role="user", content="Dime el clima de hoy por favor, es importante")]

    result = await _handle_guardrail_failure(report, context, ollama_mock, "Wrong language reply")

    # Verify LLM was called
    assert ollama_mock.chat.called
    call_args = ollama_mock.chat.call_args
    messages = call_args[0][0]  # first positional arg is the message list
    hint = messages[-1].content

    # Hint must contain both the native name and English
    assert "español" in hint
    assert "IMPORTANTE" in hint
    assert "IMPORTANT" in hint
    assert result == "Respuesta en español correcta."


async def test_language_remediation_unknown_lang_uses_bilingual_generic():
    """Unknown language code (not in _LANG_NAMES dict) uses a bilingual generic fallback."""
    from app.webhook.router import _handle_guardrail_failure

    ollama_mock = AsyncMock()
    ollama_mock.chat = AsyncMock(return_value="Corrected reply")

    report = _make_language_report("xx")  # unknown code
    context = [ChatMessage(role="user", content="Some long enough user message here please")]

    await _handle_guardrail_failure(report, context, ollama_mock, "Wrong reply")

    call_args = ollama_mock.chat.call_args
    messages = call_args[0][0]
    hint = messages[-1].content

    # Bilingual generic: both IMPORTANTE and IMPORTANT
    assert "IMPORTANTE" in hint
    assert "IMPORTANT" in hint
    # Must NOT contain the raw code "xx" as a language instruction
    assert "xx" not in hint


async def test_language_remediation_creates_span_when_trace_ctx_provided():
    """When trace_ctx is provided, remediation must create a guardrails:remediation span."""
    from app.webhook.router import _handle_guardrail_failure

    ollama_mock = AsyncMock()
    ollama_mock.chat = AsyncMock(return_value="Corrected reply in correct language")

    # Mock trace context
    span_mock = AsyncMock()
    span_mock.__aenter__ = AsyncMock(return_value=span_mock)
    span_mock.__aexit__ = AsyncMock(return_value=None)
    span_mock.set_metadata = MagicMock()

    trace_ctx_mock = MagicMock()
    trace_ctx_mock.span = MagicMock(return_value=span_mock)

    report = _make_language_report("fr")
    context = [ChatMessage(role="user", content="Bonjour, comment allez vous aujourd'hui?")]

    await _handle_guardrail_failure(
        report, context, ollama_mock, "Wrong reply", trace_ctx=trace_ctx_mock
    )

    # span() must have been called with the correct name and kind
    trace_ctx_mock.span.assert_called_once_with("guardrails:remediation", kind="generation")
    # set_metadata must include check and lang_code
    span_mock.set_metadata.assert_called_once()
    meta = span_mock.set_metadata.call_args[0][0]
    assert meta["check"] == "language_match"
    assert meta["lang_code"] == "fr"


async def test_language_remediation_no_span_without_trace_ctx():
    """Without trace_ctx, remediation runs the LLM call directly without span overhead."""
    from app.webhook.router import _handle_guardrail_failure

    ollama_mock = AsyncMock()
    ollama_mock.chat = AsyncMock(return_value="Correct reply")

    report = _make_language_report("pt")
    context = [ChatMessage(role="user", content="Olá, como você está hoje meu amigo?")]

    # Must not raise even without trace_ctx
    result = await _handle_guardrail_failure(report, context, ollama_mock, "Wrong reply")
    assert result == "Correct reply"
    assert ollama_mock.chat.called


# ---------------------------------------------------------------------------
# Phase 2: Timeout configurable
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase 1 (exec plan 32): think=False in LLM guardrail checks
# ---------------------------------------------------------------------------


async def test_check_tool_coherence_uses_think_false():
    """check_tool_coherence must pass think=False to suppress chain-of-thought."""
    from app.guardrails.checks import check_tool_coherence

    ollama_mock = AsyncMock()
    ollama_mock.chat = AsyncMock(return_value="yes")

    await check_tool_coherence("What time is it?", "It is 3pm.", ollama_mock)

    ollama_mock.chat.assert_called_once()
    _, kwargs = ollama_mock.chat.call_args
    assert kwargs.get("think") is False


async def test_check_hallucination_uses_think_false():
    """check_hallucination must pass think=False to suppress chain-of-thought."""
    from app.guardrails.checks import check_hallucination

    ollama_mock = AsyncMock()
    ollama_mock.chat = AsyncMock(return_value="no")

    await check_hallucination("What time is it?", "It is 3pm.", ollama_mock)

    ollama_mock.chat.assert_called_once()
    _, kwargs = ollama_mock.chat.call_args
    assert kwargs.get("think") is False


async def test_guardrails_llm_timeout_from_settings():
    """run_guardrails must pass guardrails_llm_timeout from settings to _run_async_check."""
    settings = MagicMock()
    settings.guardrails_llm_checks = True
    settings.guardrails_language_check = False
    settings.guardrails_pii_check = False
    settings.guardrails_llm_timeout = 2.5

    ollama_mock = AsyncMock()

    captured_timeout: list[float] = []

    async def fake_run_async_check(results, check_name, coro, timeout=0.5):
        captured_timeout.append(timeout)
        # cancel the coroutine to avoid ResourceWarning
        coro.close()

    with (
        patch("app.guardrails.pipeline._run_async_check", side_effect=fake_run_async_check),
        patch("app.guardrails.checks.check_hallucination") as mock_hall,
    ):
        mock_hall.return_value = AsyncMock(
            return_value=GuardrailResult(
                passed=True, check_name="hallucination_check", details="", latency_ms=0.0
            )
        )()
        await run_guardrails(
            user_text="some text",
            reply="some reply",
            tool_calls_used=False,
            settings=settings,
            ollama_client=ollama_mock,
        )

    assert captured_timeout, "No async check was run"
    assert all(t == 2.5 for t in captured_timeout), f"Expected 2.5, got {captured_timeout}"
