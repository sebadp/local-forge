from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.automation.models import ActionContext, AutomationRule

logger = logging.getLogger(__name__)

# Registry of run_task handlers: task_name -> async callable(context)
_TASK_HANDLERS: dict[str, object] = {}


def register_task(name: str, handler: object) -> None:
    _TASK_HANDLERS[name] = handler


async def execute_action(
    rule: AutomationRule,
    condition_value: str,
    context: ActionContext,
) -> str:
    """Execute a rule's action. Returns result description. Never raises."""
    try:
        if rule.action_type == "notify_user":
            return await _action_notify(rule, condition_value, context, admin=False)
        elif rule.action_type == "notify_admin":
            return await _action_notify(rule, condition_value, context, admin=True)
        elif rule.action_type == "run_task":
            return await _action_run_task(rule, condition_value, context)
        elif rule.action_type == "log":
            return _action_log(rule, condition_value)
        else:
            logger.warning("Unknown action type: %s", rule.action_type)
            return f"unknown_action_type:{rule.action_type}"
    except Exception as e:
        logger.exception("Action execution failed for rule %s", rule.name)
        return f"error: {e}"


async def _action_notify(
    rule: AutomationRule,
    condition_value: str,
    context: ActionContext,
    admin: bool,
) -> str:
    cfg = rule.action_config
    template = cfg.get("template", "Automation alert: {rule_name}")
    phone = context.admin_phone if admin else context.user_phone
    if not phone:
        return "no_phone_configured"
    if context.platform_client is None:
        return "no_platform_client"

    try:
        message = template.format(
            rule_name=rule.name,
            value=condition_value,
            description=rule.description or "",
        )
    except (KeyError, IndexError):
        message = f"[Automation] {rule.name}: {condition_value}"

    try:
        from app.platforms.base import PlatformClient

        client: PlatformClient = context.platform_client  # type: ignore[assignment]
        await client.send_message(phone, message)
        label = "admin" if admin else "user"
        logger.info("Automation notification sent to %s (%s): %s", label, phone, rule.name)
        return f"notified_{label}"
    except Exception as e:
        logger.exception("Failed to send automation notification for %s", rule.name)
        return f"notify_error: {e}"


async def _action_run_task(
    rule: AutomationRule,
    condition_value: str,
    context: ActionContext,
) -> str:
    cfg = rule.action_config
    task_name = cfg.get("task", "")
    if not task_name:
        return "no_task_name"

    if task_name == "backfill_embeddings":
        return await _task_backfill_embeddings(context)
    elif task_name == "consolidate_memories":
        return await _task_consolidate_memories(context)
    elif task_name == "vacuum_db":
        return await _task_vacuum_db(context)
    elif task_name in _TASK_HANDLERS:
        handler = _TASK_HANDLERS[task_name]
        await handler(context)  # type: ignore[misc,operator]
        return f"custom_task:{task_name}"
    else:
        logger.warning("Unknown task: %s in rule %s", task_name, rule.name)
        return f"unknown_task:{task_name}"


async def _task_backfill_embeddings(context: ActionContext) -> str:
    try:
        from app.embeddings.indexer import backfill_embeddings

        if context.repository is None or context.ollama_client is None:
            return "missing_dependencies"
        await backfill_embeddings(
            context.repository,  # type: ignore[arg-type]
            context.ollama_client,  # type: ignore[arg-type]
            context.embed_model,
        )
        return "backfill_complete"
    except Exception as e:
        logger.exception("Backfill embeddings task failed")
        return f"backfill_error: {e}"


async def _task_consolidate_memories(context: ActionContext) -> str:
    try:
        from app.memory.consolidator import consolidate_memories
        from app.memory.markdown import MemoryFile

        if context.repository is None or context.ollama_client is None:
            return "missing_dependencies"
        # MemoryFile needed for consolidator to sync changes to MEMORY.md
        memory_file = MemoryFile(path="data/MEMORY.md")
        await consolidate_memories(
            context.repository,  # type: ignore[arg-type]
            context.ollama_client,  # type: ignore[arg-type]
            memory_file,
        )
        return "consolidation_complete"
    except Exception as e:
        logger.exception("Consolidate memories task failed")
        return f"consolidation_error: {e}"


async def _task_vacuum_db(context: ActionContext) -> str:
    try:
        if context.repository is None:
            return "missing_repository"
        from app.database.repository import Repository

        repo: Repository = context.repository  # type: ignore[assignment]
        await repo.conn.execute("VACUUM")
        logger.info("Automation: DB VACUUM completed")
        return "vacuum_complete"
    except Exception as e:
        logger.exception("VACUUM task failed")
        return f"vacuum_error: {e}"


def _action_log(rule: AutomationRule, condition_value: str) -> str:
    cfg = rule.action_config
    message = cfg.get("message", f"Rule {rule.name} triggered: {condition_value}")
    level = cfg.get("level", "warning")
    log_fn = getattr(logger, level, logger.warning)
    log_fn("Automation [%s]: %s", rule.name, message)
    return f"logged:{level}"
