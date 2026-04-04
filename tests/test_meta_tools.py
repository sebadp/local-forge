"""Tests for app.skills.tools.meta_tools and SkillRegistry.search_tools."""

import pytest

from app.skills.registry import SkillRegistry
from app.skills.tools.meta_tools import discover_tools, set_registry


@pytest.fixture()
def registry() -> SkillRegistry:
    r = SkillRegistry(skills_dir="/nonexistent")

    async def noop(**kwargs):
        return "ok"

    r.register_tool("get_weather", "Get current weather for a city", {"type": "object", "properties": {}}, noop, skill_name="weather")
    r.register_tool("web_search", "Search the web for information", {"type": "object", "properties": {}}, noop, skill_name="search")
    r.register_tool("calculate", "Evaluate a math expression", {"type": "object", "properties": {}}, noop, skill_name="math")
    r.register_tool("save_note", "Save a note to the database", {"type": "object", "properties": {}}, noop, skill_name="notes")
    r.register_tool("search_notes", "Search through saved notes", {"type": "object", "properties": {}}, noop, skill_name="notes")
    return r


def test_search_tools_basic(registry: SkillRegistry):
    results = registry.search_tools("weather")
    assert len(results) >= 1
    assert results[0]["name"] == "get_weather"


def test_search_tools_no_match(registry: SkillRegistry):
    results = registry.search_tools("blockchain")
    assert results == []


def test_search_tools_empty_query(registry: SkillRegistry):
    assert registry.search_tools("") == []


def test_search_tools_multiple_matches(registry: SkillRegistry):
    results = registry.search_tools("search")
    names = [r["name"] for r in results]
    assert "web_search" in names
    assert "search_notes" in names


def test_search_tools_limit(registry: SkillRegistry):
    results = registry.search_tools("search", limit=1)
    assert len(results) == 1


async def test_discover_tools_integration(registry: SkillRegistry):
    set_registry(registry)
    result = await discover_tools("weather")
    assert "get_weather" in result
    assert "weather" in result.lower()


async def test_discover_tools_no_match(registry: SkillRegistry):
    set_registry(registry)
    result = await discover_tools("blockchain")
    assert "No tools found" in result


async def test_discover_tools_no_registry():
    set_registry(None)
    result = await discover_tools("weather")
    assert "Error" in result
