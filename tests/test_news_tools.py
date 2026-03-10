from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("ddgs", reason="ddgs not installed in this environment")

from app.skills.models import ToolCall
from app.skills.registry import SkillRegistry
from app.skills.tools.news_tools import register


def _make_registry():
    reg = SkillRegistry(skills_dir="/nonexistent")
    mock_repo = AsyncMock()
    register(reg, mock_repo)
    return reg, mock_repo


async def test_search_news_success():
    reg, _ = _make_registry()
    mock_results = [
        {
            "title": "AI Breakthrough",
            "url": "http://news.example.com/ai",
            "body": "Major advance in AI research.",
            "source": "TechNews",
            "date": "2026-02-15",
        },
        {
            "title": "Weather Update",
            "url": "http://news.example.com/weather",
            "body": "Storms expected.",
            "source": "WeatherCo",
            "date": "2026-02-14",
        },
    ]

    with patch("app.skills.tools.news_tools.DDGS") as MockDDGS:
        MockDDGS.return_value.news.return_value = mock_results
        result = await reg.execute_tool(ToolCall(name="search_news", arguments={"query": "AI"}))

    assert result.success
    assert "AI Breakthrough" in result.content
    assert "TechNews" in result.content
    assert "2026-02-15" in result.content
    assert "Weather Update" in result.content


async def test_search_news_no_results():
    reg, _ = _make_registry()

    with patch("app.skills.tools.news_tools.DDGS") as MockDDGS:
        MockDDGS.return_value.news.return_value = []
        result = await reg.execute_tool(
            ToolCall(name="search_news", arguments={"query": "nothing"})
        )

    assert result.success
    assert "No news found" in result.content


async def test_search_news_with_time_range():
    reg, _ = _make_registry()
    mock_results = [
        {
            "title": "Fresh News",
            "url": "http://news.example.com/fresh",
            "body": "Just happened.",
            "source": "FastNews",
            "date": "2026-02-15",
        },
    ]

    with patch("app.skills.tools.news_tools.DDGS") as MockDDGS:
        MockDDGS.return_value.news.return_value = mock_results
        result = await reg.execute_tool(
            ToolCall(name="search_news", arguments={"query": "breaking", "time_range": "d"})
        )
        MockDDGS.return_value.news.assert_called_once_with("breaking", timelimit="d", max_results=5)

    assert result.success
    assert "Fresh News" in result.content


async def test_search_news_error():
    reg, _ = _make_registry()

    with patch("app.skills.tools.news_tools.DDGS") as MockDDGS:
        MockDDGS.return_value.news.side_effect = Exception("API down")
        result = await reg.execute_tool(ToolCall(name="search_news", arguments={"query": "fail"}))

    assert result.success
    assert "Error searching news" in result.content


async def test_add_news_preference_like():
    reg, mock_repo = _make_registry()
    result = await reg.execute_tool(
        ToolCall(
            name="add_news_preference", arguments={"source": "TechCrunch", "preference": "like"}
        )
    )

    assert result.success
    assert "like" in result.content
    assert "TechCrunch" in result.content
    mock_repo.add_memory.assert_called_once()


async def test_add_news_preference_dislike():
    reg, mock_repo = _make_registry()
    result = await reg.execute_tool(
        ToolCall(
            name="add_news_preference", arguments={"source": "Clarin", "preference": "dislike"}
        )
    )

    assert result.success
    assert "dislike" in result.content
    assert "Clarin" in result.content
    mock_repo.add_memory.assert_called_once()


async def test_add_news_preference_invalid():
    reg, mock_repo = _make_registry()
    result = await reg.execute_tool(
        ToolCall(name="add_news_preference", arguments={"source": "BBC", "preference": "meh"})
    )

    assert result.success
    assert "Error" in result.content
    mock_repo.add_memory.assert_not_called()
