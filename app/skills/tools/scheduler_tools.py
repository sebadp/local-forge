from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

logger = logging.getLogger(__name__)

# Global references (set via set_scheduler / set_current_user)
_scheduler: AsyncIOScheduler | None = None
_platform_clients: dict[str, Any] = {}  # platform_name → PlatformClient
_current_user_phone: str | None = None
_message_received_at: datetime | None = None
_repository = None  # set via set_repository at boot

DEFAULT_TIMEZONE = ZoneInfo("UTC")


def set_scheduler(scheduler: AsyncIOScheduler, **clients: Any) -> None:
    """Initialize the global scheduler and platform clients.

    Usage: set_scheduler(scheduler, whatsapp=wa_client, telegram=tg_client)
    """
    global _scheduler, _platform_clients
    _scheduler = scheduler
    _platform_clients = dict(clients)


def set_repository(repository) -> None:  # type: ignore[no-untyped-def]
    """Set the repository for cron job CRUD operations."""
    global _repository
    _repository = repository


def set_current_user(phone_number: str, received_at: datetime) -> None:
    """Set the current user context for tool calls."""
    global _current_user_phone, _message_received_at
    _current_user_phone = phone_number
    _message_received_at = received_at


async def _send_reminder(phone_number: str, message: str) -> None:
    """Callback function to send a reminder to the correct platform."""
    platform = "telegram" if phone_number.startswith("tg_") else "whatsapp"
    client = _platform_clients.get(platform)
    if client is None:
        logger.warning(
            "No platform client for '%s' (user: %s), cannot send reminder",
            platform,
            phone_number,
        )
        return
    try:
        await client.send_message(phone_number, f"\u23f0 Reminder: {message}")
        logger.info("Sent reminder to %s via %s: %s", phone_number, platform, message)
    except Exception as e:
        logger.error("Failed to send reminder to %s: %s", phone_number, e)


async def schedule_task(
    description: str,
    delay_minutes: int | None = None,
    when: str | None = None,
    timezone: str = "UTC",
) -> str:
    """
    Schedule a one-time task or reminder.

    Use delay_minutes for relative times ("in 10 minutes") or
    when for absolute times ("tomorrow at 5pm").
    """
    if not _scheduler:
        return "Error: Scheduler is not initialized."

    phone = _current_user_phone
    if not phone:
        return "Error: No user phone number available."

    try:
        tz = ZoneInfo(timezone)
    except (KeyError, Exception):
        logger.warning("Invalid timezone %r, falling back to UTC", timezone)
        tz = DEFAULT_TIMEZONE

    try:
        if delay_minutes is not None:
            # Relative scheduling: base_time + N minutes
            # Use message_received_at so the delay counts from when the user
            # sent the message, not from when the LLM finished processing.
            delay = int(delay_minutes)
            if delay < 1:
                return "Error: delay_minutes must be at least 1."
            base = _message_received_at or datetime.now(tz)
            if base.tzinfo is None:
                base = base.replace(tzinfo=tz)
            run_date = base + timedelta(minutes=delay)
        elif when:
            # Absolute scheduling: parse ISO 8601
            run_date = datetime.fromisoformat(when)
            if run_date.tzinfo is None:
                run_date = run_date.replace(tzinfo=tz)
        else:
            return "Error: Provide either delay_minutes or when."

        job = _scheduler.add_job(
            _send_reminder,
            DateTrigger(run_date=run_date),
            args=[phone, description],
            name=description,
        )

        return f"Scheduled reminder '{description}' for {run_date} (Job ID: {job.id})"

    except ValueError as e:
        return f"Error: Invalid input. {e}"
    except Exception as e:
        logger.exception("Failed to schedule task")
        return f"Error scheduling task: {e}"


async def list_schedules() -> str:
    """List all currently scheduled active jobs."""
    if not _scheduler:
        return "Error: Scheduler is not active."

    jobs = _scheduler.get_jobs()
    if not jobs:
        return "No active schedules found."

    output = ["**Active Schedules:**"]
    for job in jobs:
        next_run = (
            job.next_run_time.strftime("%Y-%m-%d %H:%M:%S %Z") if job.next_run_time else "N/A"
        )
        output.append(f"- ID: `{job.id}` | Time: {next_run} | Task: {job.name}")

    return "\n".join(output)


async def create_cron(schedule: str, message: str, timezone: str = "UTC") -> str:
    """Create a persistent recurring cron job.

    `schedule` must be a standard 5-field cron expression, e.g.:
      '0 9 * * 1-5'  -> weekdays at 9am
      '0 */6 * * *'  -> every 6 hours
      '30 8 * * 1'   -> every Monday at 8:30am
    """
    if not _scheduler:
        return "Error: Scheduler is not initialized."
    if not _repository:
        return "Error: Repository not available."

    phone = _current_user_phone
    if not phone:
        return "Error: No user phone number available."

    try:
        tz_obj = ZoneInfo(timezone)
    except Exception:
        return f"Error: Invalid timezone '{timezone}'. Use an IANA timezone like 'America/Argentina/Buenos_Aires'."

    try:
        trigger = CronTrigger.from_crontab(schedule, timezone=tz_obj)
    except Exception as e:
        return (
            f"Error: Invalid cron expression '{schedule}'. "
            f"Use 5-field syntax (min hour day month weekday). Details: {e}"
        )

    try:
        job_id = await _repository.create_cron_job(phone, schedule, message, timezone)
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Failed to persist cron job")
        return f"Error saving cron job: {e}"

    try:
        _scheduler.add_job(
            _send_reminder,
            trigger,
            args=[phone, message],
            name=message,
            id=f"cron_{job_id}",
            replace_existing=True,
        )
    except Exception as e:
        logger.exception("Failed to register cron job in scheduler")
        # Rollback: remove the DB entry to avoid orphaned records that won't fire
        try:
            await _repository.delete_cron_job(job_id, phone)
        except Exception:
            pass
        return f"Error: Failed to schedule cron job. {e}"

    logger.info(
        "Created cron job %s for %s: %s @ %s (%s)", job_id, phone, schedule, timezone, message
    )
    return f"\u2705 Cron job creado (ID: {job_id})\nSchedule: `{schedule}` ({timezone})\nMensaje: {message}"


async def list_crons() -> str:
    """List all active recurring cron jobs for the current user."""
    if not _repository:
        return "Error: Repository not available."

    phone = _current_user_phone
    if not phone:
        return "Error: No user phone number available."

    try:
        jobs = await _repository.list_cron_jobs(phone)
    except Exception as e:
        return f"Error listing cron jobs: {e}"

    if not jobs:
        return "No hay cron jobs activos. Usa `create_cron` para crear uno."

    lines = ["**Cron Jobs Activos:**\n"]
    for j in jobs:
        lines.append(
            f"\u2022 ID `{j['id']}` | `{j['cron_expr']}` ({j['timezone']}) \u2192 {j['message']}\n"
            f"  _Creado: {j['created_at']}_"
        )
    return "\n".join(lines)


async def delete_cron(job_id: int) -> str:
    """Delete a recurring cron job by its ID."""
    if not _repository:
        return "Error: Repository not available."

    phone = _current_user_phone
    if not phone:
        return "Error: No user phone number available."

    try:
        deleted = await _repository.delete_cron_job(job_id, phone)
    except Exception as e:
        return f"Error deleting cron job: {e}"

    if not deleted:
        return f"Error: Cron job {job_id} not found or already inactive."

    # Also remove from the running scheduler
    if _scheduler:
        try:
            _scheduler.remove_job(f"cron_{job_id}")
        except Exception:
            pass  # Job may not be registered (e.g. already fired)

    logger.info("Deleted cron job %s for %s", job_id, phone)
    return f"\u2705 Cron job {job_id} eliminado."


if TYPE_CHECKING:
    from app.skills.registry import SkillRegistry


def register(registry: SkillRegistry) -> None:
    registry.register_tool(
        name="schedule_task",
        description="Schedule a future task or reminder. Use delay_minutes for relative times (e.g. 'in 10 minutes') or when for absolute times.",
        parameters={
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "What to remind about.",
                },
                "delay_minutes": {
                    "type": "integer",
                    "description": "Minutes from now to trigger the reminder. Use this for relative times like 'in 10 minutes', 'in an hour' (60).",
                },
                "when": {
                    "type": "string",
                    "description": "ISO 8601 datetime (YYYY-MM-DDTHH:MM:SS). Use this for absolute times like 'tomorrow at 5pm'. Only if delay_minutes is not suitable.",
                },
                "timezone": {
                    "type": "string",
                    "description": "IANA timezone for the schedule (e.g. America/Argentina/Buenos_Aires). Default: UTC.",
                },
            },
            "required": ["description"],
        },
        handler=schedule_task,
        skill_name="scheduler",
    )
    registry.register_tool(
        name="list_schedules",
        description="List all active scheduled tasks.",
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=list_schedules,
        skill_name="scheduler",
    )
    registry.register_tool(
        name="create_cron",
        description=(
            "Create a persistent recurring cron job that survives bot restarts. "
            "Use standard 5-field cron syntax (min hour day month weekday). "
            "Examples: '0 9 * * 1-5' (weekdays 9am), '0 */6 * * *' (every 6h)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "schedule": {
                    "type": "string",
                    "description": "5-field cron expression, e.g. '0 9 * * 1-5'",
                },
                "message": {
                    "type": "string",
                    "description": "Message to send when the cron fires.",
                },
                "timezone": {
                    "type": "string",
                    "description": "IANA timezone, e.g. 'America/Argentina/Buenos_Aires'. Default: UTC.",
                },
            },
            "required": ["schedule", "message"],
        },
        handler=create_cron,
        skill_name="scheduler",
    )
    registry.register_tool(
        name="list_crons",
        description="List all active recurring cron jobs for the current user.",
        parameters={"type": "object", "properties": {}},
        handler=list_crons,
        skill_name="scheduler",
    )
    registry.register_tool(
        name="delete_cron",
        description="Delete a recurring cron job by its ID. Get the ID from list_crons.",
        parameters={
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "integer",
                    "description": "ID of the cron job to delete.",
                },
            },
            "required": ["job_id"],
        },
        handler=delete_cron,
        skill_name="scheduler",
    )
