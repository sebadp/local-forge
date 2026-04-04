"""Tests for app.memory.session_extractor — LLM-powered fact extraction."""

import json
from unittest.mock import AsyncMock

from app.memory.session_extractor import (
    _format_messages_for_extraction,
    _parse_extraction_response,
    extract_session_facts,
    reset_counter,
    should_extract,
)
from app.models import ChatMessage

# --- should_extract counter ---


def test_should_extract_interval():
    phone = "test_counter_1"
    reset_counter(phone)
    for _i in range(9):
        assert should_extract(phone, interval=10) is False
    assert should_extract(phone, interval=10) is True
    # Counter resets after trigger
    assert should_extract(phone, interval=10) is False


def test_should_extract_different_phones():
    reset_counter("phone_a")
    reset_counter("phone_b")
    for _ in range(9):
        should_extract("phone_a", interval=10)
    assert should_extract("phone_a", interval=10) is True
    # phone_b still at 0
    assert should_extract("phone_b", interval=10) is False


def test_reset_counter():
    phone = "test_reset"
    for _ in range(5):
        should_extract(phone, interval=10)
    reset_counter(phone)
    # After reset, counter starts from 0 again
    assert should_extract(phone, interval=1) is True


# --- format_messages ---


def test_format_messages_filters_roles():
    msgs = [
        ChatMessage(role="system", content="You are helpful"),
        ChatMessage(role="user", content="Hello"),
        ChatMessage(role="assistant", content="Hi there"),
        ChatMessage(role="tool", content="some result"),
    ]
    result = _format_messages_for_extraction(msgs)
    assert "User: Hello" in result
    assert "Assistant: Hi there" in result
    assert "system" not in result.lower()
    assert "tool" not in result.lower()
    assert "some result" not in result


def test_format_messages_empty():
    assert _format_messages_for_extraction([]) == "(no messages)"


# --- parse_extraction_response ---


def test_parse_valid_json():
    resp = json.dumps({"facts": [{"content": "User likes Python", "category": "preference"}]})
    facts = _parse_extraction_response(resp)
    assert len(facts) == 1
    assert facts[0]["content"] == "User likes Python"


def test_parse_with_fences():
    inner = json.dumps({"facts": [{"content": "A fact", "category": "personal"}]})
    resp = f"```json\n{inner}\n```"
    facts = _parse_extraction_response(resp)
    assert len(facts) == 1


def test_parse_empty_facts():
    resp = json.dumps({"facts": []})
    facts = _parse_extraction_response(resp)
    assert facts == []


def test_parse_garbage():
    facts = _parse_extraction_response("This is not JSON at all")
    assert facts == []


def test_parse_embedded_json():
    inner = json.dumps({"facts": [{"content": "A", "category": "technical"}]})
    resp = f"Here is: {inner} done."
    facts = _parse_extraction_response(resp)
    assert len(facts) == 1


# --- extract_session_facts integration ---


async def test_extract_saves_facts():
    repo = AsyncMock()
    repo.save_memory = AsyncMock()
    repo.list_memories = AsyncMock(return_value=[])

    llm_response = json.dumps({
        "facts": [
            {"content": "User prefers dark mode", "category": "preference"},
            {"content": "User is a Python developer", "category": "technical"},
        ]
    })
    ollama = AsyncMock()
    ollama.chat = AsyncMock(return_value=llm_response)

    daily_log = AsyncMock()
    memory_file = AsyncMock()
    memory_file.sync = AsyncMock()

    msgs = [
        ChatMessage(role="user", content="I always use dark mode"),
        ChatMessage(role="assistant", content="Noted!"),
    ]

    count = await extract_session_facts(
        msgs, ["User's name is Seb"], repo, ollama, daily_log, memory_file
    )

    assert count == 2
    assert repo.save_memory.call_count == 2
    memory_file.sync.assert_called_once()


async def test_extract_no_messages():
    count = await extract_session_facts([], [], AsyncMock(), AsyncMock())
    assert count == 0


async def test_extract_empty_llm_response():
    ollama = AsyncMock()
    ollama.chat = AsyncMock(return_value="")

    msgs = [ChatMessage(role="user", content="hello")]
    count = await extract_session_facts(msgs, [], AsyncMock(), ollama)
    assert count == 0


async def test_extract_no_facts_found():
    ollama = AsyncMock()
    ollama.chat = AsyncMock(return_value=json.dumps({"facts": []}))

    msgs = [ChatMessage(role="user", content="hello")]
    count = await extract_session_facts(msgs, [], AsyncMock(), ollama)
    assert count == 0


async def test_extract_invalid_category_defaults_to_general():
    repo = AsyncMock()
    repo.save_memory = AsyncMock()
    repo.list_memories = AsyncMock(return_value=[])

    llm_response = json.dumps({
        "facts": [{"content": "Something", "category": "invalid_cat"}]
    })
    ollama = AsyncMock()
    ollama.chat = AsyncMock(return_value=llm_response)

    msgs = [ChatMessage(role="user", content="test")]
    count = await extract_session_facts(msgs, [], repo, ollama)
    assert count == 1
    repo.save_memory.assert_called_once_with("Something", category="general")


async def test_extract_skips_empty_content():
    repo = AsyncMock()
    repo.save_memory = AsyncMock()
    repo.list_memories = AsyncMock(return_value=[])

    llm_response = json.dumps({
        "facts": [
            {"content": "", "category": "preference"},
            {"content": "Valid fact", "category": "personal"},
        ]
    })
    ollama = AsyncMock()
    ollama.chat = AsyncMock(return_value=llm_response)

    msgs = [ChatMessage(role="user", content="test")]
    count = await extract_session_facts(msgs, [], repo, ollama)
    assert count == 1


async def test_extract_llm_error():
    """LLM exception should propagate (caller wraps in try/except)."""
    ollama = AsyncMock()
    ollama.chat = AsyncMock(side_effect=Exception("connection refused"))

    msgs = [ChatMessage(role="user", content="test")]
    try:
        await extract_session_facts(msgs, [], AsyncMock(), ollama)
        raise AssertionError("Should have raised")
    except Exception as e:
        assert "connection refused" in str(e)
