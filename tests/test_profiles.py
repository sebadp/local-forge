"""Tests for user profile onboarding, discovery, and prompt building."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.llm.client import ChatResponse
from app.profiles.discovery import _parse_json_safe, maybe_discover_profile_updates
from app.profiles.onboarding import handle_onboarding_message
from app.profiles.prompt_builder import build_system_prompt

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_ollama(reply: str = "LLM response") -> MagicMock:
    """Return a mock OllamaClient whose chat_with_tools always returns reply."""
    client = MagicMock()
    client.chat_with_tools = AsyncMock(return_value=ChatResponse(content=reply))
    return client


# ---------------------------------------------------------------------------
# Onboarding state machine
# ---------------------------------------------------------------------------


async def test_onboarding_pending_returns_step_1():
    ollama = make_ollama("Hi! I'm your assistant. What's your name?")
    next_state, reply, data = await handle_onboarding_message(
        user_reply="Hello there!",
        state="pending",
        profile_data={},
        ollama_client=ollama,
    )
    assert next_state == "step_1"
    assert reply  # non-empty
    assert data == {}  # no data extracted at this step


async def test_onboarding_step_1_extracts_name():
    # LLM extract returns "Alice", then asks occupation
    ollama = MagicMock()
    responses = [
        ChatResponse(content="Alice"),  # _extract_field call
        ChatResponse(content="What do you do?"),  # _ask_occupation call
    ]
    ollama.chat_with_tools = AsyncMock(side_effect=responses)

    next_state, reply, data = await handle_onboarding_message(
        user_reply="My name is Alice",
        state="step_1",
        profile_data={},
        ollama_client=ollama,
    )
    assert next_state == "step_2"
    assert data.get("name") == "Alice"
    assert reply == "What do you do?"


async def test_onboarding_step_2_extracts_occupation():
    ollama = MagicMock()
    responses = [
        ChatResponse(content="software engineer"),  # _extract_field
        ChatResponse(content="What do you use me for?"),  # _ask_use_cases
    ]
    ollama.chat_with_tools = AsyncMock(side_effect=responses)

    next_state, reply, data = await handle_onboarding_message(
        user_reply="I'm a software engineer",
        state="step_2",
        profile_data={"name": "Alice"},
        ollama_client=ollama,
    )
    assert next_state == "step_3"
    assert data.get("occupation") == "software engineer"
    assert data.get("name") == "Alice"  # preserved


async def test_onboarding_step_3_extracts_use_cases():
    ollama = MagicMock()
    responses = [
        ChatResponse(content="coding help and research"),  # _extract_field
        ChatResponse(content="How about Aria or Nova?"),  # _propose_names
    ]
    ollama.chat_with_tools = AsyncMock(side_effect=responses)

    next_state, reply, data = await handle_onboarding_message(
        user_reply="I want help with coding and research",
        state="step_3",
        profile_data={"name": "Alice", "occupation": "software engineer"},
        ollama_client=ollama,
    )
    assert next_state == "naming"
    assert data.get("use_cases") == "coding help and research"


async def test_onboarding_naming_extracts_assistant_name():
    ollama = MagicMock()
    responses = [
        ChatResponse(content="Aria"),  # _extract_field
        ChatResponse(content="Welcome! I'm Aria, ready to help!"),  # _generate_welcome
    ]
    ollama.chat_with_tools = AsyncMock(side_effect=responses)

    next_state, reply, data = await handle_onboarding_message(
        user_reply="I'll go with Aria",
        state="naming",
        profile_data={"name": "Alice", "occupation": "software engineer", "use_cases": "coding"},
        ollama_client=ollama,
    )
    assert next_state == "complete"
    assert data.get("assistant_name") == "Aria"
    assert "Aria" in reply or reply  # welcome message present


async def test_onboarding_naming_fallback_name():
    """If extraction returns empty string, default to 'Wasi'."""
    ollama = MagicMock()
    responses = [
        ChatResponse(content=""),  # extraction fails → empty
        ChatResponse(content="Welcome! I'm Wasi!"),
    ]
    ollama.chat_with_tools = AsyncMock(side_effect=responses)

    next_state, reply, data = await handle_onboarding_message(
        user_reply="Whatever you prefer",
        state="naming",
        profile_data={},
        ollama_client=ollama,
    )
    assert next_state == "complete"
    assert data.get("assistant_name") == "Wasi"


async def test_onboarding_preserves_existing_data():
    """Data from previous steps should be preserved at each transition."""
    ollama = MagicMock()
    responses = [
        ChatResponse(content=""),  # extraction returns nothing
        ChatResponse(content="What do you do?"),
    ]
    ollama.chat_with_tools = AsyncMock(side_effect=responses)

    initial_data = {"name": "Bob", "extra_field": "preserved"}
    _, _, data = await handle_onboarding_message(
        user_reply="something",
        state="step_1",
        profile_data=initial_data,
        ollama_client=ollama,
    )
    assert data.get("extra_field") == "preserved"


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def test_build_system_prompt_no_profile():
    result = build_system_prompt("Base prompt.", {}, "2026-02-19")
    assert "Base prompt." in result
    assert "Today is 2026-02-19" in result
    # No profile lines added
    assert "The user's name" not in result


def test_build_system_prompt_full_profile():
    profile = {
        "name": "Alice",
        "assistant_name": "Aria",
        "occupation": "software engineer",
        "use_cases": "coding and research",
        "tech_context": "Python developer",
        "interests": "music",
        "location": "Buenos Aires",
        "preferences": "brief answers",
    }
    result = build_system_prompt("Base.", profile, "2026-02-19")
    assert "The user's name is Alice." in result
    assert "Your name is Aria." in result
    assert "works as: software engineer." in result
    assert "mainly use you for: coding and research." in result
    assert "Technical context: Python developer." in result
    assert "Interests: music." in result
    assert "Location: Buenos Aires." in result
    assert "Preferences: brief answers." in result
    assert "Today is 2026-02-19" in result


def test_build_system_prompt_partial_profile():
    profile = {"name": "Bob"}
    result = build_system_prompt("Base.", profile, "2026-02-19")
    assert "The user's name is Bob." in result
    assert "Your name is" not in result


# ---------------------------------------------------------------------------
# Repository: user_profiles table
# ---------------------------------------------------------------------------


async def test_get_user_profile_creates_row(repository):
    profile = await repository.get_user_profile("5491100000001")
    assert profile["onboarding_state"] == "complete"
    assert profile["data"] == {}
    assert profile["message_count"] == 0


async def test_get_user_profile_idempotent(repository):
    await repository.get_user_profile("5491100000002")
    profile2 = await repository.get_user_profile("5491100000002")
    assert profile2["onboarding_state"] == "complete"


async def test_save_user_profile(repository):
    await repository.save_user_profile(
        "5491100000003",
        "step_1",
        {"name": "Carlos"},
    )
    profile = await repository.get_user_profile("5491100000003")
    assert profile["onboarding_state"] == "step_1"
    assert profile["data"]["name"] == "Carlos"


async def test_save_user_profile_upsert(repository):
    await repository.save_user_profile("5491100000004", "step_1", {"name": "D"})
    await repository.save_user_profile(
        "5491100000004", "complete", {"name": "D", "assistant_name": "Nova"}
    )
    profile = await repository.get_user_profile("5491100000004")
    assert profile["onboarding_state"] == "complete"
    assert profile["data"]["assistant_name"] == "Nova"


async def test_increment_profile_message_count(repository):
    count1 = await repository.increment_profile_message_count("5491100000005")
    count2 = await repository.increment_profile_message_count("5491100000005")
    count3 = await repository.increment_profile_message_count("5491100000005")
    assert count1 == 1
    assert count2 == 2
    assert count3 == 3


async def test_reset_user_profile(repository):
    await repository.save_user_profile(
        "5491100000006", "complete", {"name": "E", "assistant_name": "Max"}
    )
    await repository.increment_profile_message_count("5491100000006")
    await repository.reset_user_profile("5491100000006")
    profile = await repository.get_user_profile("5491100000006")
    assert profile["onboarding_state"] == "pending"
    assert profile["data"] == {}
    assert profile["message_count"] == 0


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------


def test_parse_json_safe_valid():
    assert _parse_json_safe('{"interests": "music"}') == {"interests": "music"}


def test_parse_json_safe_with_fences():
    text = '```json\n{"tech_context": "Python"}\n```'
    assert _parse_json_safe(text) == {"tech_context": "Python"}


def test_parse_json_safe_invalid():
    assert _parse_json_safe("not json at all") == {}
    assert _parse_json_safe("") == {}


def test_parse_json_safe_non_dict():
    assert _parse_json_safe('["a", "b"]') == {}


async def test_maybe_discover_skipped_when_not_multiple(repository):
    """Discovery should not run when message_count is not a multiple of interval."""
    ollama = make_ollama()
    await maybe_discover_profile_updates("123", 7, 10, repository, ollama, MagicMock())
    # If nothing ran, no LLM call was made
    ollama.chat_with_tools.assert_not_called()


async def test_maybe_discover_skipped_zero_interval(repository):
    """interval=0 should skip to avoid ZeroDivisionError."""
    ollama = make_ollama()
    await maybe_discover_profile_updates("123", 10, 0, repository, ollama, MagicMock())
    ollama.chat_with_tools.assert_not_called()


async def test_maybe_discover_skipped_during_onboarding(repository):
    """Discovery should not run if user is still in onboarding."""
    await repository.save_user_profile("555", "step_2", {"name": "F"})
    ollama = make_ollama('{"interests": "hiking"}')
    await maybe_discover_profile_updates("555", 10, 10, repository, ollama, MagicMock())
    ollama.chat_with_tools.assert_not_called()


async def test_maybe_discover_runs_and_merges(repository):
    """Discovery runs when count % interval == 0 and state is complete."""
    phone = "777"
    await repository.save_user_profile(phone, "complete", {"name": "G"})
    conv_id = await repository.get_or_create_conversation(phone)
    await repository.save_message(conv_id, "user", "I love hiking on weekends")
    await repository.save_message(conv_id, "assistant", "That sounds great!")

    ollama = make_ollama('{"interests": "hiking"}')
    settings = MagicMock()

    await maybe_discover_profile_updates(phone, 10, 10, repository, ollama, settings)

    profile = await repository.get_user_profile(phone)
    assert profile["data"].get("interests") == "hiking"
    assert profile["data"].get("name") == "G"  # existing field preserved


async def test_maybe_discover_does_not_overwrite_existing(repository):
    """Discovery should not overwrite fields already in the profile."""
    phone = "888"
    await repository.save_user_profile(phone, "complete", {"name": "H", "interests": "reading"})
    conv_id = await repository.get_or_create_conversation(phone)
    await repository.save_message(conv_id, "user", "I love hiking")

    # LLM tries to set interests, but it already exists
    ollama = make_ollama('{"interests": "hiking"}')
    settings = MagicMock()

    await maybe_discover_profile_updates(phone, 10, 10, repository, ollama, settings)

    profile = await repository.get_user_profile(phone)
    # Original value should be preserved
    assert profile["data"]["interests"] == "reading"
