from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from app.skills.tools import scheduler_tools
from app.skills.tools.scheduler_tools import (
    list_schedules,
    schedule_task,
    set_current_user,
    set_scheduler,
)

UTC = UTC


def _setup_scheduler(received_at: datetime | None = None):
    """Set up a mock scheduler and WhatsApp client."""
    mock_scheduler = MagicMock()
    mock_job = MagicMock()
    mock_job.id = "test-job-123"
    mock_scheduler.add_job.return_value = mock_job
    mock_scheduler.get_jobs.return_value = []

    mock_wa = AsyncMock()
    set_scheduler(mock_scheduler, whatsapp=mock_wa)
    ts = received_at or datetime.now(UTC)
    set_current_user("5491112345678", received_at=ts)
    return mock_scheduler


async def test_schedule_with_delay_minutes():
    received = datetime.now(UTC)
    mock_sched = _setup_scheduler(received_at=received)
    result = await schedule_task(description="Check logos", delay_minutes=10)

    assert "Scheduled reminder 'Check logos'" in result
    assert "test-job-123" in result
    mock_sched.add_job.assert_called_once()

    # Verify the run_date is based on received_at + 10 minutes
    call_args = mock_sched.add_job.call_args
    trigger = call_args[0][1]
    expected = received + timedelta(minutes=10)
    assert abs((trigger.run_date - expected).total_seconds()) < 2


async def test_delay_uses_message_received_at():
    """delay_minutes counts from message receipt, not tool execution time."""
    received = datetime(2026, 2, 16, 12, 33, 0, tzinfo=UTC)
    _setup_scheduler(received_at=received)
    result = await schedule_task(description="Call", delay_minutes=10)

    assert "Scheduled reminder" in result
    # Should be 12:43, not whenever the tool actually runs
    assert "2026-02-16 12:43:00" in result


async def test_schedule_with_absolute_when():
    mock_sched = _setup_scheduler()
    result = await schedule_task(
        description="Call Mom",
        when="2026-03-01T17:00:00",
        timezone="America/Argentina/Buenos_Aires",
    )

    assert "Scheduled reminder 'Call Mom'" in result
    mock_sched.add_job.assert_called_once()

    call_args = mock_sched.add_job.call_args
    trigger = call_args[0][1]
    assert trigger.run_date.tzinfo is not None


async def test_schedule_no_time_returns_error():
    _setup_scheduler()
    result = await schedule_task(description="Something")
    assert "Error" in result
    assert "delay_minutes" in result or "when" in result


async def test_schedule_negative_delay():
    _setup_scheduler()
    result = await schedule_task(description="Bad", delay_minutes=-5)
    assert "Error" in result
    assert "at least 1" in result


async def test_schedule_no_scheduler():
    scheduler_tools._scheduler = None
    set_current_user("123", received_at=datetime.now(UTC))
    result = await schedule_task(description="Test", delay_minutes=5)
    assert "not initialized" in result


async def test_schedule_no_phone():
    _setup_scheduler()
    scheduler_tools._current_user_phone = None
    result = await schedule_task(description="Test", delay_minutes=5)
    assert "No user phone" in result


async def test_schedule_invalid_timezone_fallback():
    mock_sched = _setup_scheduler()
    result = await schedule_task(
        description="Test",
        delay_minutes=5,
        timezone="Invalid/Timezone",
    )
    # Should succeed with UTC fallback
    assert "Scheduled reminder" in result
    mock_sched.add_job.assert_called_once()


async def test_schedule_passes_phone_to_job():
    mock_sched = _setup_scheduler()
    await schedule_task(description="Reminder", delay_minutes=1)

    call_args = mock_sched.add_job.call_args
    job_args = call_args[1]["args"]
    assert job_args[0] == "5491112345678"
    assert job_args[1] == "Reminder"


async def test_list_schedules_empty():
    _setup_scheduler()
    result = await list_schedules()
    assert "No active schedules" in result


async def test_list_schedules_with_jobs():
    mock_sched = _setup_scheduler()
    mock_job = MagicMock()
    mock_job.id = "abc123"
    mock_job.name = "Call Mom"
    mock_job.next_run_time = datetime(2026, 3, 1, 17, 0, 0, tzinfo=ZoneInfo("UTC"))
    mock_sched.get_jobs.return_value = [mock_job]

    result = await list_schedules()
    assert "Active Schedules" in result
    assert "abc123" in result
    assert "Call Mom" in result
