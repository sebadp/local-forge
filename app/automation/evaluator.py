from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.automation.actions import execute_action
from app.automation.conditions import check_condition, in_cooldown
from app.automation.models import ActionContext, AutomationRule

if TYPE_CHECKING:
    from app.database.repository import Repository

logger = logging.getLogger(__name__)


async def evaluate_rules(
    repository: Repository,
    platform_client: object | None = None,
    admin_phone: str = "",
    user_phone: str = "",
    ollama_client: object | None = None,
    embed_model: str = "",
    vec_available: bool = False,
) -> int:
    """Evaluate all active automation rules. Returns count of triggered rules."""
    try:
        rows = await repository.get_active_automation_rules()
    except Exception:
        logger.exception("Failed to fetch automation rules")
        return 0

    from app.tracing.context import get_current_trace

    trace = get_current_trace()

    triggered = 0
    for row in rows:
        try:
            rule = AutomationRule.from_row(tuple(row))
        except Exception:
            logger.exception("Failed to parse automation rule row")
            continue

        if in_cooldown(rule):
            logger.debug("Rule %s in cooldown, skipping", rule.name)
            continue

        try:
            met, value = await check_condition(rule, repository)
        except Exception:
            logger.exception("Condition check failed for rule %s", rule.name)
            await _safe_log(repository, rule.id, "error", "failed", "condition_check_error")
            continue

        if not met:
            continue

        context = ActionContext(
            platform_client=platform_client,
            admin_phone=admin_phone,
            user_phone=user_phone,
            repository=repository,
            ollama_client=ollama_client,
            embed_model=embed_model,
            vec_available=vec_available,
        )

        result = await execute_action(rule, value, context)
        action_result = "success" if "error" not in result.lower() else "failed"

        await _safe_log(repository, rule.id, value, action_result, result)
        if action_result == "success":
            await _safe_update_triggered(repository, rule.id)
            triggered += 1
        logger.info(
            "Automation rule triggered: %s (condition: %s, result: %s)",
            rule.name,
            value,
            result,
        )

    if trace:
        try:
            async with trace.span("automation:evaluate", kind="span") as span:
                span.set_input({"rule_count": len(rows)})
                span.set_output({"triggered": triggered})
        except Exception:
            logger.debug("Failed to record automation span", exc_info=True)

    return triggered


async def _safe_log(
    repository: Repository,
    rule_id: int,
    condition_value: str,
    result: str,
    details: str | None,
) -> None:
    try:
        await repository.log_automation(rule_id, condition_value, result, details)
    except Exception:
        logger.exception("Failed to log automation result for rule_id=%d", rule_id)


async def _safe_update_triggered(repository: Repository, rule_id: int) -> None:
    try:
        await repository.update_rule_last_triggered(rule_id)
    except Exception:
        logger.exception("Failed to update last_triggered_at for rule_id=%d", rule_id)
