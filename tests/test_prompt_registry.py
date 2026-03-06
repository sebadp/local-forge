"""Tests for prompt registry, seeding, and get_active_prompt fallback chain."""

from __future__ import annotations

import pytest

from app.eval.prompt_manager import get_active_prompt, invalidate_prompt_cache
from app.eval.prompt_registry import PROMPT_DEFAULTS, get_default

# ---------------------------------------------------------------------------
# prompt_registry.py — catalog
# ---------------------------------------------------------------------------


def test_prompt_defaults_has_required_keys():
    """All high-priority prompts must be present in PROMPT_DEFAULTS."""
    required = {
        "system_prompt",
        "classifier",
        "summarizer",
        "flush_to_memory",
        "consolidator",
        "compaction_system",
        "planner_create",
        "planner_replan",
        "planner_synthesize",
    }
    assert required.issubset(PROMPT_DEFAULTS.keys()), (
        f"Missing prompts: {required - PROMPT_DEFAULTS.keys()}"
    )


def test_prompt_defaults_all_non_empty():
    """Every entry in PROMPT_DEFAULTS must be a non-empty string."""
    for name, content in PROMPT_DEFAULTS.items():
        assert isinstance(content, str) and content.strip(), f"Prompt '{name}' is empty"


def test_get_default_known_name():
    assert get_default("classifier") == PROMPT_DEFAULTS["classifier"]


def test_get_default_unknown_name():
    assert get_default("nonexistent_prompt_xyz") is None


def test_classifier_prompt_has_few_shot_examples():
    """Classifier default must include the few-shot examples added in Phase 1."""
    classifier = PROMPT_DEFAULTS["classifier"]
    assert "15% of 230" in classifier
    assert "math" in classifier
    assert "tell me a joke" in classifier
    assert "none" in classifier


def test_flush_to_memory_prompt_has_placeholders():
    """flush_to_memory must have {existing_memories} and {conversation} placeholders."""
    prompt = PROMPT_DEFAULTS["flush_to_memory"]
    assert "{existing_memories}" in prompt
    assert "{conversation}" in prompt


def test_consolidator_prompt_has_placeholder():
    prompt = PROMPT_DEFAULTS["consolidator"]
    assert "{memories}" in prompt


def test_planner_create_prompt_has_placeholders():
    prompt = PROMPT_DEFAULTS["planner_create"]
    assert "{objective}" in prompt
    assert "{context_block}" in prompt


# ---------------------------------------------------------------------------
# repository.seed_default_prompts()
# ---------------------------------------------------------------------------


async def test_seed_inserts_v1_for_new_prompts(repository):
    """seed_default_prompts must insert v1 and activate it for each new prompt."""
    defaults = {"test_prompt_alpha": "Hello prompt A", "test_prompt_beta": "Hello prompt B"}
    seeded = await repository.seed_default_prompts(defaults)
    assert seeded == 2

    for name, content in defaults.items():
        row = await repository.get_active_prompt_version(name)
        assert row is not None
        assert row["content"] == content
        assert row["version"] == 1
        assert row["is_active"] is True
        assert row["created_by"] == "system"


async def test_seed_skips_existing_prompts(repository):
    """seed_default_prompts must NOT duplicate prompts that already have an active version."""
    await repository.save_prompt_version(
        "existing_prompt", version=1, content="v1", created_by="human"
    )
    await repository.activate_prompt_version("existing_prompt", version=1)

    seeded = await repository.seed_default_prompts({"existing_prompt": "should not overwrite"})
    assert seeded == 0

    # Original content must be preserved
    row = await repository.get_active_prompt_version("existing_prompt")
    assert row["content"] == "v1"


async def test_seed_is_idempotent(repository):
    """Calling seed_default_prompts twice must not raise and must not double-insert."""
    defaults = {"idempotent_prompt": "idempotent content"}
    first = await repository.seed_default_prompts(defaults)
    second = await repository.seed_default_prompts(defaults)
    assert first == 1
    assert second == 0


# ---------------------------------------------------------------------------
# repository.list_all_active_prompts()
# ---------------------------------------------------------------------------


async def test_list_all_active_prompts_empty(repository):
    result = await repository.list_all_active_prompts()
    assert isinstance(result, list)


async def test_list_all_active_prompts_after_seed(repository):
    defaults = {"prompt_list_a": "content a", "prompt_list_b": "content b"}
    await repository.seed_default_prompts(defaults)

    result = await repository.list_all_active_prompts()
    names = {r["prompt_name"] for r in result}
    assert "prompt_list_a" in names
    assert "prompt_list_b" in names
    for r in result:
        if r["prompt_name"] in defaults:
            assert r["version"] == 1
            assert r["created_by"] == "system"


# ---------------------------------------------------------------------------
# get_active_prompt() — fallback chain
# ---------------------------------------------------------------------------


async def test_get_active_prompt_uses_db_version(repository):
    """When DB has an active version, it must be returned (not the default)."""
    invalidate_prompt_cache("db_prompt_test")
    await repository.save_prompt_version(
        "db_prompt_test", version=1, content="DB version", created_by="human"
    )
    await repository.activate_prompt_version("db_prompt_test", version=1)

    result = await get_active_prompt("db_prompt_test", repository, default="fallback")
    assert result == "DB version"
    invalidate_prompt_cache("db_prompt_test")


async def test_get_active_prompt_falls_back_to_registry(repository):
    """When DB has no version, registry default must be returned."""
    invalidate_prompt_cache("summarizer")
    # Don't seed — rely purely on registry fallback
    result = await get_active_prompt("summarizer", repository)
    assert result == PROMPT_DEFAULTS["summarizer"]
    invalidate_prompt_cache("summarizer")


async def test_get_active_prompt_explicit_default_overrides_none(repository):
    """When prompt is unknown to DB and registry, explicit default param is used."""
    invalidate_prompt_cache("unknown_prompt_xyz")
    result = await get_active_prompt(
        "unknown_prompt_xyz", repository, default="my explicit default"
    )
    assert result == "my explicit default"
    invalidate_prompt_cache("unknown_prompt_xyz")


async def test_get_active_prompt_raises_when_nothing_found(repository):
    """get_active_prompt must raise ValueError when no default exists anywhere."""
    invalidate_prompt_cache("totally_unknown_prompt_abc")
    with pytest.raises(ValueError, match="totally_unknown_prompt_abc"):
        await get_active_prompt("totally_unknown_prompt_abc", repository)
    invalidate_prompt_cache("totally_unknown_prompt_abc")


async def test_get_active_prompt_uses_cache_on_second_call(repository):
    """Second call for same prompt must use cache (no DB hit needed)."""
    invalidate_prompt_cache("cached_prompt_test")
    await repository.save_prompt_version(
        "cached_prompt_test", version=1, content="cached", created_by="human"
    )
    await repository.activate_prompt_version("cached_prompt_test", version=1)

    # First call — hits DB
    first = await get_active_prompt("cached_prompt_test", repository)
    # Modify DB value — should NOT affect second call (still cached)
    await repository.save_prompt_version(
        "cached_prompt_test", version=2, content="updated", created_by="human"
    )
    await repository.activate_prompt_version("cached_prompt_test", version=2)

    second = await get_active_prompt("cached_prompt_test", repository)
    assert first == second == "cached"
    invalidate_prompt_cache("cached_prompt_test")


# ---------------------------------------------------------------------------
# classify_intent with repository — uses versioned classifier prompt
# ---------------------------------------------------------------------------


async def test_classify_intent_uses_versioned_classifier(repository):
    """When repository is provided, classify_intent fetches the active classifier prompt."""
    from unittest.mock import AsyncMock

    from app.llm.client import ChatResponse
    from app.skills.router import classify_intent

    invalidate_prompt_cache("classifier")

    # Save a modified classifier prompt in DB
    custom_template = (
        'Classify: Reply with category or "none".\n'
        "Categories: {categories}, none\n\n"
        "{recent_context}"
        "Message: {user_message}"
    )
    await repository.save_prompt_version(
        "classifier", version=1, content=custom_template, created_by="test"
    )
    await repository.activate_prompt_version("classifier", version=1)

    client = AsyncMock()
    captured_prompts: list[str] = []

    async def capture(*args, **kwargs):
        msgs = args[0]
        captured_prompts.append(msgs[0].content)
        return ChatResponse(content="math")

    client.chat_with_tools = capture

    result = await classify_intent("what is 2+2", client, repository=repository)
    assert result == ["math"]
    # The custom template (shorter, no few-shots) must have been used
    assert captured_prompts
    assert "Classify:" in captured_prompts[0]

    invalidate_prompt_cache("classifier")
