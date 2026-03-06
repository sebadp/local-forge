"""Tests for Phase 4: /prompts command (prompt catalog)."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.commands.builtins import cmd_prompts
from app.commands.context import CommandContext


def _make_context(repository) -> CommandContext:
    ctx = MagicMock(spec=CommandContext)
    ctx.repository = repository
    return ctx


# ---------------------------------------------------------------------------
# /prompts  (no args — list all)
# ---------------------------------------------------------------------------


async def test_prompts_no_args_empty_db(repository):
    """/prompts with no prompts in DB must return a graceful message."""
    ctx = _make_context(repository)
    result = await cmd_prompts("", ctx)
    assert "no hay" in result.lower() or "no" in result.lower()


async def test_prompts_no_args_lists_all(repository):
    """/prompts with no args lists all registered prompts."""
    await repository.seed_default_prompts(
        {"alpha_prompt": "content alpha", "beta_prompt": "content beta"}
    )
    ctx = _make_context(repository)
    result = await cmd_prompts("", ctx)
    assert "alpha_prompt" in result
    assert "beta_prompt" in result
    assert "v1" in result


async def test_prompts_no_args_shows_hint(repository):
    """/prompts list must include a usage hint."""
    await repository.seed_default_prompts({"hint_prompt": "hello"})
    ctx = _make_context(repository)
    result = await cmd_prompts("", ctx)
    assert "/prompts" in result or "nombre" in result.lower()


# ---------------------------------------------------------------------------
# /prompts <name>  (show active + history)
# ---------------------------------------------------------------------------


async def test_prompts_name_shows_active_content(repository):
    """/prompts <name> must show the active prompt content."""
    await repository.save_prompt_version(
        "classifier", version=1, content="You classify messages.", created_by="system"
    )
    await repository.activate_prompt_version("classifier", version=1)

    ctx = _make_context(repository)
    result = await cmd_prompts("classifier", ctx)
    assert "You classify messages." in result
    assert "v1" in result


async def test_prompts_name_shows_history(repository):
    """/prompts <name> must show version history."""
    await repository.save_prompt_version(
        "summarizer", version=1, content="v1 content", created_by="system"
    )
    await repository.activate_prompt_version("summarizer", version=1)
    await repository.save_prompt_version(
        "summarizer", version=2, content="v2 content", created_by="agent"
    )

    ctx = _make_context(repository)
    result = await cmd_prompts("summarizer", ctx)
    assert "v1" in result
    assert "v2" in result
    assert "Historial" in result or "historial" in result


async def test_prompts_name_not_found(repository):
    """/prompts <name> for unknown prompt returns a not-found message."""
    ctx = _make_context(repository)
    result = await cmd_prompts("nonexistent_prompt_xyz", ctx)
    assert "no encontré" in result.lower() or "not found" in result.lower()


async def test_prompts_name_truncates_long_content(repository):
    """/prompts <name> must truncate very long prompt content."""
    long_content = "A" * 2000
    await repository.save_prompt_version(
        "long_prompt", version=1, content=long_content, created_by="test"
    )
    await repository.activate_prompt_version("long_prompt", version=1)

    ctx = _make_context(repository)
    result = await cmd_prompts("long_prompt", ctx)
    # Should not include the full 2000 chars, but must mention total length
    assert "chars total" in result or len(result) < 2000


# ---------------------------------------------------------------------------
# /prompts <name> <version>  (show specific version)
# ---------------------------------------------------------------------------


async def test_prompts_name_version_shows_content(repository):
    """/prompts <name> <version> must show that version's content."""
    await repository.save_prompt_version(
        "flush_to_memory", version=1, content="v1 facts", created_by="system"
    )
    await repository.activate_prompt_version("flush_to_memory", version=1)
    await repository.save_prompt_version(
        "flush_to_memory", version=2, content="v2 improved facts", created_by="agent"
    )

    ctx = _make_context(repository)
    result = await cmd_prompts("flush_to_memory 2", ctx)
    assert "v2 improved facts" in result
    assert "v2" in result


async def test_prompts_name_version_shows_active_marker(repository):
    """/prompts <name> <version> marks the active version."""
    await repository.save_prompt_version(
        "consolidator", version=1, content="merge facts", created_by="system"
    )
    await repository.activate_prompt_version("consolidator", version=1)

    ctx = _make_context(repository)
    result = await cmd_prompts("consolidator 1", ctx)
    assert "✅" in result or "activo" in result.lower()


async def test_prompts_name_version_not_found(repository):
    """/prompts <name> <version> for unknown version returns a not-found message."""
    ctx = _make_context(repository)
    result = await cmd_prompts("classifier 999", ctx)
    assert "999" in result
    assert "no encontré" in result.lower() or "not found" in result.lower()


async def test_prompts_name_version_invalid_number(repository):
    """/prompts <name> <version> with non-integer version returns usage hint."""
    ctx = _make_context(repository)
    result = await cmd_prompts("classifier abc", ctx)
    assert "número" in result.lower() or "usage" in result.lower() or "uso" in result.lower()
