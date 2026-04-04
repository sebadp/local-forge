"""Tests for app.memory.consolidation_lock."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.memory.consolidation_lock import (
    read_last_consolidated_at,
    release_lock,
    should_dream,
    try_acquire_lock,
    write_last_consolidated_at,
)


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


def test_acquire_lock_first_time(data_dir: Path):
    assert try_acquire_lock(data_dir) is True
    assert (data_dir / ".consolidation_lock").exists()


def test_acquire_lock_already_held(data_dir: Path):
    assert try_acquire_lock(data_dir) is True
    assert try_acquire_lock(data_dir) is False


def test_acquire_lock_stale(data_dir: Path):
    """A lock older than 2 hours should be considered stale."""
    import json

    lock_path = data_dir / ".consolidation_lock"
    stale_time = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    lock_path.write_text(json.dumps({"pid": 99999, "locked_at": stale_time}))

    assert try_acquire_lock(data_dir) is True


def test_release_lock(data_dir: Path):
    try_acquire_lock(data_dir)
    release_lock(data_dir)
    assert not (data_dir / ".consolidation_lock").exists()


def test_release_lock_missing(data_dir: Path):
    """release_lock should not raise if lock doesn't exist."""
    release_lock(data_dir)


def test_read_write_timestamp(data_dir: Path):
    assert read_last_consolidated_at(data_dir) is None

    write_last_consolidated_at(data_dir)
    ts = read_last_consolidated_at(data_dir)
    assert ts is not None
    assert (datetime.now(UTC) - ts).total_seconds() < 5


def test_read_timestamp_corrupt(data_dir: Path):
    (data_dir / ".last_dream").write_text("not-a-date")
    assert read_last_consolidated_at(data_dir) is None


async def test_should_dream_no_previous(data_dir: Path):
    """First ever dream: no timestamp → time gate passes, check activity."""
    repo = AsyncMock()
    repo.count_messages_since = AsyncMock(return_value=100)
    assert await should_dream(data_dir, repo, interval_hours=24, min_messages=50) is True


async def test_should_dream_too_soon(data_dir: Path):
    """Dream ran recently → time gate fails."""
    write_last_consolidated_at(data_dir)
    repo = AsyncMock()
    assert await should_dream(data_dir, repo, interval_hours=24, min_messages=50) is False


async def test_should_dream_not_enough_messages(data_dir: Path):
    """Enough time passed but not enough messages."""
    (data_dir / ".last_dream").write_text(
        (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    )
    repo = AsyncMock()
    repo.count_messages_since = AsyncMock(return_value=10)
    assert await should_dream(data_dir, repo, interval_hours=24, min_messages=50) is False


async def test_should_dream_all_gates_pass(data_dir: Path):
    """Time + activity + lock all pass."""
    (data_dir / ".last_dream").write_text(
        (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    )
    repo = AsyncMock()
    repo.count_messages_since = AsyncMock(return_value=60)
    assert await should_dream(data_dir, repo, interval_hours=24, min_messages=50) is True
