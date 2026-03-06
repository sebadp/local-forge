"""Tests for get_windowed_history() in ConversationManager."""

from unittest.mock import AsyncMock, MagicMock

from app.conversation.manager import ConversationManager
from app.models import ChatMessage


def _make_messages(n: int) -> list[ChatMessage]:
    return [
        ChatMessage(role="user" if i % 2 == 0 else "assistant", content=f"msg {i}")
        for i in range(n)
    ]


async def _make_manager(history: list[ChatMessage], summary: str | None = None):
    """Build a ConversationManager with a mocked repository."""
    repo = MagicMock()
    repo.get_or_create_conversation = AsyncMock(return_value=1)
    repo.get_recent_messages = AsyncMock(return_value=history)
    repo.get_latest_summary = AsyncMock(return_value=summary)
    manager = ConversationManager(repo, max_messages=20)
    return manager


async def test_short_history_no_windowing():
    """History shorter than verbatim_count returns all messages."""
    history = _make_messages(5)
    manager = await _make_manager(history, summary="old summary")
    result_history, result_summary = await manager.get_windowed_history("123", verbatim_count=8)
    assert result_history == history
    assert result_summary is None


async def test_equal_history_no_windowing():
    """History exactly equal to verbatim_count returns all, no summary."""
    history = _make_messages(8)
    manager = await _make_manager(history, summary="some summary")
    result_history, result_summary = await manager.get_windowed_history("123", verbatim_count=8)
    assert result_history == history
    assert result_summary is None


async def test_long_history_windowed():
    """History longer than verbatim_count returns last N + summary."""
    history = _make_messages(20)
    manager = await _make_manager(history, summary="This is a summary of older messages")
    result_history, result_summary = await manager.get_windowed_history("123", verbatim_count=8)
    assert len(result_history) == 8
    assert result_history == history[-8:]
    assert result_summary == "This is a summary of older messages"


async def test_no_summary_available():
    """Long history but no summary in DB → returns last N, None."""
    history = _make_messages(15)
    manager = await _make_manager(history, summary=None)
    result_history, result_summary = await manager.get_windowed_history("123", verbatim_count=8)
    assert len(result_history) == 8
    assert result_summary is None


async def test_verbatim_count_configurable_5():
    """Works with verbatim_count=5."""
    history = _make_messages(12)
    manager = await _make_manager(history, summary="Summary")
    result_history, _ = await manager.get_windowed_history("123", verbatim_count=5)
    assert len(result_history) == 5
    assert result_history == history[-5:]


async def test_verbatim_count_configurable_10():
    """Works with verbatim_count=10."""
    history = _make_messages(25)
    manager = await _make_manager(history, summary="Summary")
    result_history, _ = await manager.get_windowed_history("123", verbatim_count=10)
    assert len(result_history) == 10
    assert result_history == history[-10:]
