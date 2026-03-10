from unittest.mock import patch

import pytest

pytest.importorskip("ddgs", reason="ddgs not installed in this environment")

from app.skills.models import ToolCall
from app.skills.registry import SkillRegistry
from app.skills.tools.search_tools import register


def _make_registry():
    reg = SkillRegistry(skills_dir="/nonexistent")
    register(reg)
    return reg


async def test_web_search_success():
    reg = _make_registry()
    mock_results = [
        {"title": "Result 1", "href": "http://example.com/1", "body": "Snippet 1"},
        {"title": "Result 2", "href": "http://example.com/2", "body": "Snippet 2"},
    ]

    with patch("app.skills.tools.search_tools.DDGS") as MockDDGS:
        MockDDGS.return_value.text.return_value = mock_results
        result = await reg.execute_tool(ToolCall(name="web_search", arguments={"query": "test"}))

    assert result.success
    assert "Result 1" in result.content
    assert "http://example.com/1" in result.content
    assert "Snippet 1" in result.content
    assert "Result 2" in result.content


async def test_web_search_no_results():
    reg = _make_registry()

    with patch("app.skills.tools.search_tools.DDGS") as MockDDGS:
        MockDDGS.return_value.text.return_value = []
        result = await reg.execute_tool(ToolCall(name="web_search", arguments={"query": "nothing"}))

    assert result.success
    assert "No results found" in result.content


async def test_web_search_error():
    reg = _make_registry()

    with patch("app.skills.tools.search_tools.DDGS") as MockDDGS:
        MockDDGS.return_value.text.side_effect = Exception("Network error")
        result = await reg.execute_tool(ToolCall(name="web_search", arguments={"query": "fail"}))

    assert result.success
    assert "Error performing search" in result.content


async def test_web_search_with_time_range():
    reg = _make_registry()
    mock_results = [
        {"title": "Recent", "href": "http://example.com/recent", "body": "Fresh news"},
    ]

    with patch("app.skills.tools.search_tools.DDGS") as MockDDGS:
        MockDDGS.return_value.text.return_value = mock_results
        result = await reg.execute_tool(
            ToolCall(name="web_search", arguments={"query": "test", "time_range": "d"})
        )
        MockDDGS.return_value.text.assert_called_once_with("test", timelimit="d", max_results=5)

    assert result.success
    assert "Recent" in result.content
