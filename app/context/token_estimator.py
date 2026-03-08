"""Token budget estimation for qwen3:8b context window.

Uses chars/4 as a proxy for token count (±20% for BPE tokenizers).
Suitable for logging and alerting — not for precise truncation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models import ChatMessage

logger = logging.getLogger(__name__)

_CONTEXT_LIMIT = 32_000  # qwen3:8b context window


def estimate_tokens(text: str) -> int:
    """Proxy estimator: chars / 4. Acceptable ±20% for qwen3 BPE."""
    return max(1, len(text) // 4)


def estimate_messages_tokens(messages: list[ChatMessage]) -> int:
    """Estimate total tokens for a list of messages."""
    return sum(estimate_tokens(m.content or "") for m in messages)


def estimate_sections(sections: dict[str, str | None]) -> dict[str, int]:
    """Compute token estimate per named section. None/empty sections count as 0."""
    return {name: estimate_tokens(text) if text else 0 for name, text in sections.items()}


def log_context_budget_breakdown(
    sections: dict[str, int],
    context_limit: int = _CONTEXT_LIMIT,
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
) -> int:
    """Log estimated token usage and warn if nearing or exceeding limit.

    Returns the estimated token count.
    """
    estimate = estimate_messages_tokens(messages)
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
