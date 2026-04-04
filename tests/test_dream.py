"""Tests for app.memory.dream — Auto-Dream consolidation."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.memory.dream import (
    _format_memories_for_dream,
    _parse_dream_response,
    run_dream,
)
from app.models import Memory


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture()
def sample_memories() -> list[Memory]:
    return [
        Memory(id=1, content="User works as a backend developer", category="personal"),
        Memory(id=2, content="User prefers Python over JavaScript", category="preference"),
        Memory(id=3, content="User works as a backend developer in Python", category="personal"),
        Memory(id=4, content="User's name is Sebastián", category="personal"),
    ]


# --- Unit tests ---


def test_format_memories_empty():
    assert _format_memories_for_dream([]) == "(no memories yet)"


def test_format_memories(sample_memories):
    result = _format_memories_for_dream(sample_memories)
    assert "[1] [personal]" in result
    assert "[2] [preference]" in result
    assert "backend developer" in result


def test_parse_dream_response_valid():
    resp = json.dumps({"actions": [{"type": "remove", "id": 3}], "keep_ids": [1, 2, 4]})
    data = _parse_dream_response(resp)
    assert len(data["actions"]) == 1
    assert data["keep_ids"] == [1, 2, 4]


def test_parse_dream_response_with_fences():
    resp = "```json\n" + json.dumps({"actions": [], "keep_ids": [1]}) + "\n```"
    data = _parse_dream_response(resp)
    assert data["keep_ids"] == [1]


def test_parse_dream_response_garbage():
    data = _parse_dream_response("This is not JSON at all")
    assert data == {"actions": [], "keep_ids": []}


def test_parse_dream_response_embedded():
    resp = "Here is the result: " + json.dumps({"actions": [], "keep_ids": [1, 2]}) + " done."
    data = _parse_dream_response(resp)
    assert data["keep_ids"] == [1, 2]


# --- Integration test with mocked LLM ---


async def test_run_dream_removes_duplicate(data_dir, sample_memories):
    """Dream should remove memory #3 (duplicate of #1)."""
    repo = AsyncMock()
    repo.list_memories = AsyncMock(return_value=sample_memories)
    repo.remove_memory = AsyncMock(return_value=True)
    repo.save_memory = AsyncMock()

    llm_response = json.dumps({
        "actions": [
            {"type": "remove", "id": 3, "reason": "duplicate of #1"},
        ],
        "keep_ids": [1, 2, 4],
    })
    ollama = AsyncMock()
    ollama.chat = AsyncMock(return_value=llm_response)

    memory_file = AsyncMock()
    daily_log = AsyncMock()
    daily_log.load_recent = AsyncMock(return_value="# 2026-04-01\n- 10:00 — User asked about Python")

    result = await run_dream(repo, ollama, memory_file, daily_log, data_dir)

    assert result.removed == 1
    assert result.updated == 0
    assert result.created == 0
    assert result.error is None
    repo.remove_memory.assert_called_once_with("User works as a backend developer in Python")
    memory_file.sync.assert_called_once()


async def test_run_dream_update_and_create(data_dir, sample_memories):
    """Dream updates a memory and creates a new one from logs."""
    repo = AsyncMock()
    repo.list_memories = AsyncMock(return_value=sample_memories)
    repo.remove_memory = AsyncMock(return_value=True)
    repo.save_memory = AsyncMock()

    llm_response = json.dumps({
        "actions": [
            {"type": "update", "id": 1, "new_content": "User works as a senior backend developer"},
            {"type": "create", "content": "User is preparing a demo for Friday 2026-04-04", "category": "temporal"},
        ],
        "keep_ids": [1, 2, 4],
    })
    ollama = AsyncMock()
    ollama.chat = AsyncMock(return_value=llm_response)

    memory_file = AsyncMock()
    daily_log = AsyncMock()
    daily_log.load_recent = AsyncMock(return_value=None)

    result = await run_dream(repo, ollama, memory_file, daily_log, data_dir)

    assert result.updated == 1
    assert result.created == 1
    assert result.error is None


async def test_run_dream_no_memories(data_dir):
    """Dream should skip if there are no memories."""
    repo = AsyncMock()
    repo.list_memories = AsyncMock(return_value=[])

    result = await run_dream(repo, AsyncMock(), AsyncMock(), AsyncMock(), data_dir)

    assert result.removed == 0
    assert result.error is None


async def test_run_dream_llm_error(data_dir, sample_memories):
    """Dream should handle LLM errors gracefully."""
    repo = AsyncMock()
    repo.list_memories = AsyncMock(return_value=sample_memories)

    ollama = AsyncMock()
    ollama.chat = AsyncMock(side_effect=Exception("Ollama connection refused"))

    memory_file = AsyncMock()
    daily_log = AsyncMock()
    daily_log.load_recent = AsyncMock(return_value=None)

    result = await run_dream(repo, ollama, memory_file, daily_log, data_dir)

    assert result.error is not None
    assert "connection refused" in result.error.lower()


async def test_run_dream_invalid_action_ids(data_dir, sample_memories):
    """Dream should skip actions with invalid IDs."""
    repo = AsyncMock()
    repo.list_memories = AsyncMock(return_value=sample_memories)
    repo.remove_memory = AsyncMock(return_value=True)

    llm_response = json.dumps({
        "actions": [
            {"type": "remove", "id": 999, "reason": "nonexistent"},
            {"type": "remove", "id": 1, "reason": "valid"},
        ],
        "keep_ids": [2, 4],
    })
    ollama = AsyncMock()
    ollama.chat = AsyncMock(return_value=llm_response)

    memory_file = AsyncMock()
    daily_log = AsyncMock()
    daily_log.load_recent = AsyncMock(return_value=None)

    result = await run_dream(repo, ollama, memory_file, daily_log, data_dir)

    assert result.removed == 1  # Only the valid one
