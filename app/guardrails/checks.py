"""Individual guardrail check functions (deterministic, no LLM)."""

from __future__ import annotations

import asyncio
import re
import time

from app.guardrails.models import GuardrailResult

# --- PII patterns ---

# DNI argentino: 7-8 dígitos aislados (no dentro de números más largos)
_RE_DNI = re.compile(r"\b\d{7,8}\b")
# Tokens: Bearer, sk-, whsec_, etc.
_RE_TOKEN = re.compile(
    r"\b(Bearer\s+[A-Za-z0-9\-._~+/]+=*|sk-[A-Za-z0-9]{20,}|whsec_[A-Za-z0-9]+)\b"
)
# Emails
_RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
# Phones: secuencias de 10-15 dígitos, opcionalmente con +/- separadores
_RE_PHONE = re.compile(r"\b\+?[\d\s\-]{10,15}\b")

# Regex para detectar raw tool JSON leakage
_RE_RAW_TOOL = re.compile(r'\{"tool_call"', re.IGNORECASE)

# Regex para detectar texto que no es lenguaje natural (URLs, UUIDs, código)
_RE_URL = re.compile(r"^https?://\S+$")
_RE_NON_NATURAL = re.compile(r"^[\w:/\-_.~%?=&#+@]+$")  # sin espacios → no es prosa


def check_not_empty(reply: str) -> GuardrailResult:
    start = time.monotonic()
    passed = len(reply.strip()) > 0
    latency_ms = (time.monotonic() - start) * 1000
    return GuardrailResult(
        passed=passed,
        check_name="not_empty",
        details="" if passed else "Reply is empty",
        latency_ms=latency_ms,
    )


async def check_language_match(
    user_text: str, reply: str, default_language: str | None = None
) -> GuardrailResult:
    """Check that reply is in the same language as user_text.

    When user_text < 30 chars (too short for reliable detection), falls back to
    ``default_language`` if provided — detects reply language and compares against it.
    Skips if user_text has no whitespace (URL, UUID, code) — not natural language.

    langdetect is CPU-bound — runs in a thread to avoid blocking the event loop.
    """
    start = time.monotonic()
    stripped_user = user_text.strip()

    # Skip if reply is too short (nothing meaningful to detect)
    if len(reply.strip()) < 30:
        latency_ms = (time.monotonic() - start) * 1000
        return GuardrailResult(
            passed=True,
            check_name="language_match",
            details="skipped (reply too short for reliable detection)",
            latency_ms=latency_ms,
        )

    # When user_text is too short, fall back to default_language if available
    if len(stripped_user) < 30:
        if not default_language:
            latency_ms = (time.monotonic() - start) * 1000
            return GuardrailResult(
                passed=True,
                check_name="language_match",
                details="skipped (user text too short, no default language)",
                latency_ms=latency_ms,
            )
        try:
            from langdetect import detect

            reply_lang = await asyncio.to_thread(detect, reply)
            passed = reply_lang == default_language
            latency_ms = (time.monotonic() - start) * 1000
            return GuardrailResult(
                passed=passed,
                check_name="language_match",
                details=default_language if not passed else "",
                latency_ms=latency_ms,
            )
        except Exception:
            latency_ms = (time.monotonic() - start) * 1000
            return GuardrailResult(
                passed=True,
                check_name="language_match",
                details="detection failed on short user text fallback",
                latency_ms=latency_ms,
            )

    # Skip if user_text is non-natural (URL, UUID, code with no spaces)
    words = stripped_user.split()
    if len(words) <= 2 and _RE_NON_NATURAL.match(words[0]):
        latency_ms = (time.monotonic() - start) * 1000
        return GuardrailResult(
            passed=True,
            check_name="language_match",
            details="skipped (non-natural-language input: URL/UUID/code)",
            latency_ms=latency_ms,
        )

    try:
        from langdetect import detect

        # langdetect.detect() uses non-thread-safe global state; run both detections
        # sequentially inside a single thread to avoid race conditions.
        def _detect_both() -> tuple[str, str]:
            return detect(user_text), detect(reply)

        user_lang, reply_lang = await asyncio.to_thread(_detect_both)
        passed = user_lang == reply_lang
        latency_ms = (time.monotonic() - start) * 1000
        return GuardrailResult(
            passed=passed,
            check_name="language_match",
            details=user_lang if not passed else "",
            latency_ms=latency_ms,
        )
    except Exception:
        latency_ms = (time.monotonic() - start) * 1000
        # Fail open: if detection fails, don't block the response
        return GuardrailResult(
            passed=True,
            check_name="language_match",
            details="detection failed, skipping",
            latency_ms=latency_ms,
        )


def check_no_pii(user_text: str, reply: str) -> GuardrailResult:
    """Check that reply doesn't leak PII not present in user_text."""
    start = time.monotonic()

    # Extract PII candidates from reply that were NOT in user_text
    leaked: list[str] = []

    for pattern, name in [
        (_RE_TOKEN, "token"),
        (_RE_EMAIL, "email"),
    ]:
        reply_matches = set(pattern.findall(reply))
        user_matches = set(pattern.findall(user_text))
        new_matches = reply_matches - user_matches
        if new_matches:
            leaked.append(f"{name}:{','.join(list(new_matches)[:2])}")

    # Phones and DNIs: check for patterns that appear in reply but not user_text
    # (common in production: bot generates phone numbers)
    for pattern, name in [(_RE_PHONE, "phone"), (_RE_DNI, "dni")]:
        reply_matches = set(pattern.findall(reply))
        user_matches = set(pattern.findall(user_text))

        reply_seqs = set()
        for m in reply_matches:
            # Ignore ISO dates (YYYY-MM-DD) which match phone regex
            if name == "phone" and re.match(r"^\+?\d{4}-\d{2}-\d{2}$", m.strip()):
                continue
            reply_seqs.add(re.sub(r"[\s\-]", "", m))

        user_seqs = {re.sub(r"[\s\-]", "", m) for m in user_matches}
        new_seqs = reply_seqs - user_seqs
        if new_seqs:
            leaked.append(f"{name}:{','.join(list(new_seqs)[:2])}")

    passed = len(leaked) == 0
    latency_ms = (time.monotonic() - start) * 1000
    return GuardrailResult(
        passed=passed,
        check_name="no_pii",
        details="; ".join(leaked) if leaked else "",
        latency_ms=latency_ms,
    )


def redact_pii(text: str) -> str:
    """Redact PII patterns in-place. Used as remediation for no_pii failures."""
    text = _RE_TOKEN.sub("[REDACTED_TOKEN]", text)
    text = _RE_EMAIL.sub("[REDACTED_EMAIL]", text)
    return text


def check_excessive_length(reply: str) -> GuardrailResult:
    """Check that reply isn't excessively long (>8000 chars = possible runaway generation).
    split_message() handles chunking for normal long messages.
    """
    start = time.monotonic()
    passed = len(reply) <= 8000
    latency_ms = (time.monotonic() - start) * 1000
    return GuardrailResult(
        passed=passed,
        check_name="excessive_length",
        details=f"reply length={len(reply)}" if not passed else "",
        latency_ms=latency_ms,
    )


def check_no_raw_tool_json(reply: str) -> GuardrailResult:
    """Check that reply doesn't contain raw tool call JSON leaking into output."""
    start = time.monotonic()
    passed = not bool(_RE_RAW_TOOL.search(reply))
    latency_ms = (time.monotonic() - start) * 1000
    return GuardrailResult(
        passed=passed,
        check_name="no_raw_tool_json",
        details="raw tool JSON detected in reply" if not passed else "",
        latency_ms=latency_ms,
    )


async def check_tool_coherence(user_text: str, reply: str, ollama_client) -> GuardrailResult:
    """LLM check: verify that a tool-using reply coherently addresses the user's question.

    Only meaningful when tool_calls_used=True. Fail open on error or timeout.
    """
    from app.models import ChatMessage

    start = time.monotonic()
    try:
        prompt = (
            f"User question: {user_text[:300]}\n"
            f"Assistant reply: {reply[:500]}\n\n"
            "Does the assistant reply coherently address the user's question? "
            "Reply ONLY with 'yes' or 'no'."
        )
        response = await ollama_client.chat([ChatMessage(role="user", content=prompt)], think=False)
        answer = response.strip().lower()
        passed = answer.startswith("yes")
        latency_ms = (time.monotonic() - start) * 1000
        return GuardrailResult(
            passed=passed,
            check_name="tool_coherence",
            details="" if passed else "LLM judged reply incoherent with question",
            latency_ms=latency_ms,
        )
    except Exception as e:
        latency_ms = (time.monotonic() - start) * 1000
        return GuardrailResult(
            passed=True,
            check_name="tool_coherence",
            details=f"check error: {e}",
            latency_ms=latency_ms,
        )


async def check_hallucination(user_text: str, reply: str, ollama_client) -> GuardrailResult:
    """LLM check: detect obvious hallucinated facts in the reply.

    Fail open on error or timeout.
    """
    from app.models import ChatMessage

    start = time.monotonic()
    try:
        prompt = (
            f"User question: {user_text[:300]}\n"
            f"Assistant reply: {reply[:500]}\n\n"
            "Does the assistant reply contain specific made-up or hallucinated facts "
            "(e.g., invented numbers, names, or dates not grounded in the question)? "
            "Reply ONLY with 'yes' or 'no'."
        )
        response = await ollama_client.chat([ChatMessage(role="user", content=prompt)], think=False)
        answer = response.strip().lower()
        passed = answer.startswith("no")  # "yes" = hallucination detected = fail
        latency_ms = (time.monotonic() - start) * 1000
        return GuardrailResult(
            passed=passed,
            check_name="hallucination_check",
            details="" if passed else "LLM detected potential hallucination",
            latency_ms=latency_ms,
        )
    except Exception as e:
        latency_ms = (time.monotonic() - start) * 1000
        return GuardrailResult(
            passed=True,
            check_name="hallucination_check",
            details=f"check error: {e}",
            latency_ms=latency_ms,
        )
