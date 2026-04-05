"""Tests for memory retrieval benchmark mode (Plan 62 Phase 2)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


async def test_run_memory_with_matching_keywords():
    """Memory mode scores based on keyword matching in retrieved memories."""
    from scripts.run_eval import _run_memory

    entries = [
        {
            "id": 1,
            "input_text": "Que sabe sobre Python?",
            "metadata": '{"expected_memory_keywords": ["python", "programming"], "section": "memory"}',
        }
    ]

    mock_client = AsyncMock()

    mock_client.embed = AsyncMock(return_value=[[0.1] * 768])

    with patch("scripts.run_eval.init_db") as mock_init, \
         patch("scripts.run_eval.Repository") as MockRepo:
        mock_conn = AsyncMock()
        mock_init.return_value = (mock_conn, None)
        mock_repo = AsyncMock()
        mock_repo.search_similar_memories.return_value = [
            "User likes Python and FastAPI",
            "User is a programming expert",
            "User lives in Buenos Aires",
            "User prefers dark mode",
            "Random unrelated memory",
        ]
        MockRepo.return_value = mock_repo

        results = await _run_memory(entries, mock_client)

    assert len(results) == 1
    r = results[0]
    assert r["passed"] is True
    assert r["score"] > 0  # Should have some precision and recall


async def test_run_memory_skips_entries_without_keywords():
    """Entries without expected_memory_keywords are skipped."""
    from scripts.run_eval import _run_memory

    entries = [
        {
            "id": 1,
            "input_text": "Hola",
            "metadata": '{"section": "chat"}',
        }
    ]

    mock_client = AsyncMock()

    mock_client.embed = AsyncMock(return_value=[[0.1] * 768])

    with patch("scripts.run_eval.init_db") as mock_init, \
         patch("scripts.run_eval.Repository") as MockRepo:
        mock_init.return_value = (AsyncMock(), None)
        MockRepo.return_value = AsyncMock()

        results = await _run_memory(entries, mock_client)

    assert len(results) == 0


async def test_run_memory_handles_embedding_failure():
    """If embedding fails, entry is scored 0."""
    from scripts.run_eval import _run_memory

    entries = [
        {
            "id": 1,
            "input_text": "test query",
            "metadata": '{"expected_memory_keywords": ["test"], "section": "memory"}',
        }
    ]

    mock_client = AsyncMock()

    mock_client.embed = AsyncMock(return_value=[])

    with patch("scripts.run_eval.init_db") as mock_init, \
         patch("scripts.run_eval.Repository") as MockRepo:
        mock_init.return_value = (AsyncMock(), None)
        MockRepo.return_value = AsyncMock()

        results = await _run_memory(entries, mock_client)

    assert len(results) == 1
    assert results[0]["passed"] is False
    assert results[0]["score"] == 0.0
