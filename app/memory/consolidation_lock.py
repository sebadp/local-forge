"""Consolidation lock and timestamp management for Auto-Dream.

Prevents concurrent consolidation runs and tracks when the last
dream consolidation occurred.

Lock: file-based (.consolidation_lock) with PID + timestamp.
Timestamp: separate file (.last_dream) for last successful consolidation.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_LOCK_FILENAME = ".consolidation_lock"
_TIMESTAMP_FILENAME = ".last_dream"
_STALE_THRESHOLD_HOURS = 2


def _lock_path(data_dir: Path) -> Path:
    return data_dir / _LOCK_FILENAME


def _timestamp_path(data_dir: Path) -> Path:
    return data_dir / _TIMESTAMP_FILENAME


def try_acquire_lock(data_dir: Path) -> bool:
    """Try to acquire the consolidation lock.

    Creates a lock file with PID + timestamp. Returns False if lock
    already exists and is not stale (< 2 hours old).
    """
    lock = _lock_path(data_dir)
    if lock.exists():
        try:
            content = json.loads(lock.read_text(encoding="utf-8"))
            locked_at = datetime.fromisoformat(content["locked_at"])
            if datetime.now(UTC) - locked_at < timedelta(hours=_STALE_THRESHOLD_HOURS):
                logger.debug("Consolidation lock held (pid=%s, age=%s)", content.get("pid"), datetime.now(UTC) - locked_at)
                return False
            logger.warning("Stale consolidation lock detected (age=%s), overwriting", datetime.now(UTC) - locked_at)
        except (json.JSONDecodeError, KeyError, ValueError):
            logger.warning("Corrupt consolidation lock, overwriting")

    data_dir.mkdir(parents=True, exist_ok=True)
    lock.write_text(
        json.dumps({"pid": os.getpid(), "locked_at": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )
    return True


def release_lock(data_dir: Path) -> None:
    """Release the consolidation lock."""
    lock = _lock_path(data_dir)
    try:
        lock.unlink(missing_ok=True)
    except OSError:
        logger.warning("Failed to remove consolidation lock", exc_info=True)


def read_last_consolidated_at(data_dir: Path) -> datetime | None:
    """Read the timestamp of the last successful dream consolidation."""
    ts_file = _timestamp_path(data_dir)
    if not ts_file.exists():
        return None
    try:
        text = ts_file.read_text(encoding="utf-8").strip()
        return datetime.fromisoformat(text)
    except (ValueError, OSError):
        return None


def write_last_consolidated_at(data_dir: Path) -> None:
    """Persist the current time as the last consolidation timestamp."""
    ts_file = _timestamp_path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    ts_file.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")


async def should_dream(
    data_dir: Path,
    repository: object,
    interval_hours: int = 24,
    min_messages: int = 50,
) -> bool:
    """Gate function: determine if a dream consolidation should run.

    Checks (cheapest first):
    1. Time since last consolidation >= interval_hours
    2. Message count since last consolidation >= min_messages
    3. Lock can be acquired
    """
    last = read_last_consolidated_at(data_dir)

    # Check 1: Time gate
    if last is not None:
        hours_since = (datetime.now(UTC) - last).total_seconds() / 3600
        if hours_since < interval_hours:
            return False

    # Check 2: Activity gate — count messages since last consolidation
    since_iso = last.isoformat() if last else "2000-01-01T00:00:00+00:00"
    try:
        count = await repository.count_messages_since(since_iso)  # type: ignore[attr-defined]
    except AttributeError:
        # Fallback if repository doesn't have the method yet
        logger.warning("Repository lacks count_messages_since; skipping activity gate")
        count = min_messages  # pass the gate

    if count < min_messages:
        return False

    # Check 3: Lock gate
    return try_acquire_lock(data_dir)
