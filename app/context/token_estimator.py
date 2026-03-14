"""Token budget estimation for qwen3:8b context window.

Uses chars/4 as a default proxy, auto-calibrated at runtime using
actual token counts from Ollama's prompt_eval_count response field.
After ~10 requests the EMA converges to <5% error vs the real tokenizer.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models import ChatMessage

logger = logging.getLogger(__name__)

_CONTEXT_LIMIT = 32_000  # qwen3:8b context window

# --- Runtime calibration state ---
_token_ratios: dict[str, float] = {}  # model_name → chars_per_token ratio
_EMA_ALPHA = 0.3  # weight of new observation vs history
_DEFAULT_RATIO = 4.0  # fallback: chars / 4


def calibrate(model: str, char_count: int, actual_tokens: int) -> None:
    """Update the chars-per-token ratio for *model* using an exponential moving average.

    Called after each Ollama response that includes prompt_eval_count.
    """
    if actual_tokens <= 0 or char_count <= 0:
        return
    observed_ratio = char_count / actual_tokens
    # Clamp to plausible BPE range (2–6 chars/token) to prevent corruption
    if observed_ratio < 2.0 or observed_ratio > 6.0:
        logger.debug("token.calibration: skipping outlier ratio=%.3f for %s", observed_ratio, model)
        return
    old_ratio = _token_ratios.get(model)
    if old_ratio is not None:
        _token_ratios[model] = _EMA_ALPHA * observed_ratio + (1 - _EMA_ALPHA) * old_ratio
    else:
        _token_ratios[model] = observed_ratio
    logger.debug(
        "token.calibration: model=%s ratio=%.3f (was %s)",
        model,
        _token_ratios[model],
        f"{old_ratio:.3f}" if old_ratio is not None else "uncalibrated",
    )


def get_calibration_info(model: str = "default") -> dict:
    """Return current calibration state for observability."""
    ratio = _token_ratios.get(model)
    return {
        "model": model,
        "calibrated": ratio is not None,
        "chars_per_token": round(ratio, 3) if ratio is not None else _DEFAULT_RATIO,
        "known_models": list(_token_ratios.keys()),
    }


def estimate_tokens(text: str, model: str = "default") -> int:
    """Estimate token count using the calibrated ratio (fallback: chars/4)."""
    ratio = _token_ratios.get(model, _DEFAULT_RATIO)
    return max(1, int(len(text) / ratio))


def estimate_messages_tokens(messages: list[ChatMessage], model: str = "default") -> int:
    """Estimate total tokens for a list of messages."""
    return sum(estimate_tokens(m.content or "", model) for m in messages)


def estimate_sections(sections: dict[str, str | None], model: str = "default") -> dict[str, int]:
    """Compute token estimate per named section. None/empty sections count as 0."""
    return {name: estimate_tokens(text, model) if text else 0 for name, text in sections.items()}


def log_context_budget_breakdown(
    sections: dict[str, int],
) -> None:
    """Log a structured token breakdown per context section.

    Emits a single INFO log with the breakdown dict, largest section, and total.
    Does NOT emit WARNING/ERROR — that is handled by log_context_budget().
    Best-effort: callers should wrap in try/except.
    """
    total = sum(sections.values())
    if not sections:
        return
    largest_section = max(sections, key=lambda k: sections[k])
    logger.info(
        "context.budget.breakdown: total=%d largest=%s",
        total,
        largest_section,
        extra={
            "token_breakdown": sections,
            "largest_section": largest_section,
            "total": total,
        },
    )


def log_context_budget(
    messages: list[ChatMessage],
    context_limit: int = _CONTEXT_LIMIT,
    extra: dict | None = None,
    model: str = "default",
) -> int:
    """Log estimated token usage and warn if nearing or exceeding limit.

    Returns the estimated token count.
    """
    estimate = estimate_messages_tokens(messages, model)
    system_count = sum(1 for m in messages if m.role == "system")

    log_extra = {
        "estimated_tokens": estimate,
        "message_count": len(messages),
        "system_message_count": system_count,
        **(extra or {}),
    }

    if estimate > context_limit:
        logger.error(
            "context.budget.exceeded: %d estimated tokens (limit=%d)",
            estimate,
            context_limit,
            extra=log_extra,
        )
    elif estimate > context_limit * 0.8:
        logger.warning(
            "context.budget.near_limit: %d estimated tokens (%.0f%% of %d)",
            estimate,
            estimate / context_limit * 100,
            context_limit,
            extra=log_extra,
        )
    else:
        logger.info(
            "context.budget: %d estimated tokens (%d msgs, %d system)",
            estimate,
            len(messages),
            system_count,
            extra=log_extra,
        )

    return estimate
