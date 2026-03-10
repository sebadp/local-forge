from unittest.mock import AsyncMock

from app.llm.client import ChatResponse
from app.skills.router import (
    DEFAULT_CATEGORIES,
    REQUEST_MORE_TOOLS_NAME,
    TOOL_CATEGORIES,
    build_request_more_tools_schema,
    classify_intent,
    select_tools,
)


def _make_tool_schema(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Tool {name}",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _make_tools_map(names: list[str]) -> dict[str, dict]:
    return {name: _make_tool_schema(name) for name in names}


def _mock_ollama(response_text: str) -> AsyncMock:
    client = AsyncMock()
    client.chat_with_tools = AsyncMock(return_value=ChatResponse(content=response_text))
    client.chat = AsyncMock(return_value="plain reply")
    return client


# --- classify_intent tests ---


async def test_classify_single_category():
    client = _mock_ollama("weather")
    result = await classify_intent("What's the weather?", client)
    assert result == ["weather"]
    # Should call with think=False
    client.chat_with_tools.assert_called_once()
    call_kwargs = client.chat_with_tools.call_args
    assert call_kwargs.kwargs.get("think") is False or call_kwargs[1].get("think") is False


async def test_classify_multiple_categories():
    client = _mock_ollama("time, weather")
    result = await classify_intent("What time is it and how's the weather?", client)
    assert result == ["time", "weather"]


async def test_classify_none():
    client = _mock_ollama("none")
    result = await classify_intent("Hello, how are you?", client)
    assert result == ["none"]


async def test_classify_invalid_response_returns_defaults():
    client = _mock_ollama("I don't understand the question")
    result = await classify_intent("Tell me a joke", client)
    assert result == DEFAULT_CATEGORIES


async def test_classify_mixed_valid_invalid():
    client = _mock_ollama("weather, foobar, math")
    result = await classify_intent("Calculate and check weather", client)
    assert result == ["weather", "math"]


async def test_classify_exception_returns_defaults():
    client = AsyncMock()
    client.chat_with_tools = AsyncMock(side_effect=Exception("LLM error"))
    result = await classify_intent("anything", client)
    assert result == DEFAULT_CATEGORIES


async def test_classify_empty_response_returns_defaults():
    client = _mock_ollama("")
    result = await classify_intent("test", client)
    assert result == DEFAULT_CATEGORIES


async def test_classify_strips_whitespace():
    client = _mock_ollama("  search , news  ")
    result = await classify_intent("Find latest news", client)
    assert result == ["search", "news"]


# --- URL detection tests ---


async def test_classify_url_adds_fetch():
    client = _mock_ollama("none")
    # Ensure "fetch" is a known category so the URL override guard passes
    TOOL_CATEGORIES.setdefault("fetch", ["fetch_markdown"])
    result = await classify_intent("Check out this link https://example.com", client)
    assert "fetch" in result


async def test_classify_url_overrides_none():
    client = _mock_ollama("none")
    TOOL_CATEGORIES.setdefault("fetch", ["fetch_markdown"])
    result = await classify_intent("https://github.com", client)
    assert result == ["fetch"]


async def test_classify_url_appends_to_others():
    client = _mock_ollama("search, news")
    TOOL_CATEGORIES.setdefault("fetch", ["fetch_markdown"])
    result = await classify_intent("Search for this https://news.com", client)
    assert "fetch" in result
    assert "search" in result
    assert "news" in result


# --- select_tools tests ---


def test_select_single_category():
    tools_map = _make_tools_map(["get_weather", "calculate", "web_search"])
    result = select_tools(["weather"], tools_map)
    assert len(result) == 1
    assert result[0]["function"]["name"] == "get_weather"


def test_select_multiple_categories():
    all_names = ["get_current_datetime", "convert_timezone", "calculate", "get_weather"]
    tools_map = _make_tools_map(all_names)
    result = select_tools(["time", "math"], tools_map)
    names = [t["function"]["name"] for t in result]
    assert "get_current_datetime" in names
    assert "convert_timezone" in names
    assert "calculate" in names


def test_select_respects_max_tools():
    # Create tools for many categories
    all_names = TOOL_CATEGORIES["time"] + TOOL_CATEGORIES["notes"] + TOOL_CATEGORIES["weather"]
    tools_map = _make_tools_map(all_names)
    result = select_tools(["time", "notes", "weather"], tools_map, max_tools=3)
    assert len(result) == 3


def test_select_ignores_missing_tools():
    # Only some tools exist in the map
    tools_map = _make_tools_map(["get_weather"])
    result = select_tools(["weather", "math"], tools_map)
    assert len(result) == 1
    assert result[0]["function"]["name"] == "get_weather"


def test_select_unknown_category():
    tools_map = _make_tools_map(["get_weather"])
    result = select_tools(["nonexistent"], tools_map)
    assert result == []


def test_select_no_duplicates():
    tools_map = _make_tools_map(["get_weather"])
    # Same category twice shouldn't duplicate
    result = select_tools(["weather", "weather"], tools_map)
    assert len(result) == 1


def test_select_empty_categories():
    tools_map = _make_tools_map(["get_weather"])
    result = select_tools([], tools_map)
    assert result == []


def test_select_tools_distributes_budget_with_two_categories():
    """Both categories must be represented when budget is tight (bug fix regression test)."""
    projects_tools = list(TOOL_CATEGORIES["projects"])
    github_tools = list(TOOL_CATEGORIES["github"])
    tools_map = _make_tools_map(projects_tools + github_tools)

    result = select_tools(["projects", "github"], tools_map, max_tools=8)
    names = [t["function"]["name"] for t in result]

    assert any(n in projects_tools for n in names), "projects category must be represented"
    assert any(n in github_tools for n in names), "github category must be represented"
    assert len(result) <= 8


def test_select_tools_single_category_unchanged():
    """Single category should still receive up to max_tools tools (retrocompatibility)."""
    all_names = list(TOOL_CATEGORIES["projects"])
    tools_map = _make_tools_map(all_names)
    result = select_tools(["projects"], tools_map, max_tools=8)
    # With 1 category: per_cat = max(2, 8//1) = 8 → same as before
    assert len(result) == min(8, len(all_names))


# --- request_more_tools schema tests ---


def test_build_request_more_tools_schema_lists_categories():
    """Schema description must mention all provided categories."""
    schema = build_request_more_tools_schema(["github", "notes", "weather"])
    desc = schema["function"]["description"]
    assert "github" in desc
    assert "notes" in desc
    assert "weather" in desc


async def test_classify_prompt_includes_few_shot_examples():
    """The classifier prompt must include few-shot examples for common categories."""
    from app.skills.router import _CLASSIFIER_PROMPT_TEMPLATE, TOOL_CATEGORIES

    # Render template with minimal context
    categories_str = ", ".join(TOOL_CATEGORIES.keys())
    prompt = _CLASSIFIER_PROMPT_TEMPLATE.format(
        categories=categories_str,
        user_message="test",
        recent_context="",
    )

    # Verify the few-shot examples are in the rendered prompt
    assert "15% of 230" in prompt
    assert "math" in prompt
    assert "time" in prompt
    assert "tell me a joke" in prompt
    assert "none" in prompt


def test_build_request_more_tools_schema_has_required_categories_param():
    """Schema must have 'categories' as a required array parameter and correct tool name."""
    schema = build_request_more_tools_schema(["github"])
    assert schema["function"]["name"] == REQUEST_MORE_TOOLS_NAME
    params = schema["function"]["parameters"]
    assert "categories" in params["properties"]
    assert params["properties"]["categories"]["type"] == "array"
    assert "categories" in params["required"]


async def test_classify_scheduling_as_time():
    """Scheduling messages should classify as 'time' category."""
    client = _mock_ollama("time")
    result = await classify_intent("recuerdame en 5 minutos", client)
    assert result == ["time"]


def test_cron_tools_in_time_category():
    """Cron tools must be included in the 'time' category."""
    time_tools = TOOL_CATEGORIES["time"]
    assert "create_cron" in time_tools
    assert "list_crons" in time_tools
    assert "delete_cron" in time_tools


def test_workers_have_search_tools():
    """Reader and general workers should have search/fetch categories."""
    from app.skills.router import WORKER_TOOL_SETS

    assert "search" in WORKER_TOOL_SETS["reader"]
    assert "fetch" in WORKER_TOOL_SETS["reader"]
    assert "search" in WORKER_TOOL_SETS["general"]
    assert "fetch" in WORKER_TOOL_SETS["general"]
