"""Tests for Fase 3: Eval-Coupled Versioning.

Covers:
- activate_with_eval() returns score without activating
- activate_with_eval() handles missing version, empty dataset, eval errors
- run_quick_eval with prompt_name/prompt_version override
- /approve-prompt shows eval score in response
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.eval.prompt_manager import activate_with_eval

# ---------------------------------------------------------------------------
# activate_with_eval()
# ---------------------------------------------------------------------------


async def test_activate_with_eval_returns_error_for_missing_version(repository):
    """activate_with_eval must return error dict when version doesn't exist."""
    ollama = AsyncMock()
    result = await activate_with_eval(
        "classifier", version=99, repository=repository, ollama_client=ollama
    )
    assert "error" in result
    assert "99" in result["error"]
    ollama.chat.assert_not_called()


async def test_activate_with_eval_returns_no_entries_when_dataset_empty(repository):
    """activate_with_eval must gracefully handle empty dataset."""
    await repository.save_prompt_version(
        "classifier", version=1, content="test prompt", created_by="test"
    )
    await repository.activate_prompt_version("classifier", version=1)

    ollama = AsyncMock()
    result = await activate_with_eval(
        "classifier", version=1, repository=repository, ollama_client=ollama
    )
    assert "error" not in result
    assert result["entries_evaluated"] == 0
    assert result["activated"] is False
    ollama.chat.assert_not_called()


async def test_activate_with_eval_never_activates(repository):
    """activate_with_eval must NEVER activate — always returns activated=False."""
    await repository.save_prompt_version(
        "summarizer", version=1, content="summarize this", created_by="test"
    )
    await repository.activate_prompt_version("summarizer", version=1)

    # Add a dataset entry
    await repository.get_or_create_conversation("test123")
    await repository.save_trace(
        trace_id="t-eval-test-1",
        phone_number="test123",
        input_text="What time is it?",
        message_type="text",
    )
    await repository.add_dataset_entry(
        trace_id="t-eval-test-1",
        entry_type="correction",
        input_text="What time is it?",
        output_text="I don't know",
        expected_output="It is 3pm",
        metadata={},
    )

    ollama = AsyncMock()
    ollama.chat = AsyncMock(side_effect=["It is 3pm.", "yes"])  # inference + judge

    result = await activate_with_eval(
        "summarizer", version=1, repository=repository, ollama_client=ollama
    )
    assert result["activated"] is False
    assert "entries_evaluated" in result
    assert result["entries_evaluated"] > 0


async def test_activate_with_eval_score_below_threshold(repository):
    """activate_with_eval must mark passed=False when score < threshold."""
    await repository.save_prompt_version(
        "consolidator", version=2, content="consolidate", created_by="test"
    )
    await repository.activate_prompt_version("consolidator", version=2)

    await repository.save_trace(
        trace_id="t-eval-test-2",
        phone_number="test456",
        input_text="What is the capital?",
        message_type="text",
    )
    await repository.add_dataset_entry(
        trace_id="t-eval-test-2",
        entry_type="correction",
        input_text="What is the capital?",
        output_text="Buenos Aires",
        expected_output="Paris",
        metadata={},
    )

    ollama = AsyncMock()
    ollama.chat = AsyncMock(side_effect=["Buenos Aires", "no"])  # inference + judge says no

    result = await activate_with_eval(
        "consolidator",
        version=2,
        repository=repository,
        ollama_client=ollama,
        eval_threshold=0.7,
    )
    assert result["activated"] is False
    assert result["passed"] is False
    assert result["score"] == 0.0


async def test_activate_with_eval_score_above_threshold(repository):
    """activate_with_eval must mark passed=True when score >= threshold."""
    await repository.save_prompt_version(
        "flush_to_memory", version=1, content="extract facts", created_by="test"
    )
    await repository.activate_prompt_version("flush_to_memory", version=1)

    await repository.save_trace(
        trace_id="t-eval-test-3",
        phone_number="test789",
        input_text="Who is the president?",
        message_type="text",
    )
    await repository.add_dataset_entry(
        trace_id="t-eval-test-3",
        entry_type="correction",
        input_text="Who is the president?",
        output_text="Milei",
        expected_output="Javier Milei",
        metadata={},
    )

    ollama = AsyncMock()
    ollama.chat = AsyncMock(side_effect=["Javier Milei", "yes"])  # inference + judge says yes

    result = await activate_with_eval(
        "flush_to_memory",
        version=1,
        repository=repository,
        ollama_client=ollama,
        eval_threshold=0.7,
    )
    assert result["activated"] is False
    assert result["passed"] is True
    assert result["score"] == 1.0
    assert result["entries_evaluated"] == 1


# ---------------------------------------------------------------------------
# run_quick_eval with prompt overrides
# ---------------------------------------------------------------------------


async def test_run_quick_eval_with_prompt_override(repository):
    """run_quick_eval with prompt_name+version must use the prompt as system context."""
    from unittest.mock import AsyncMock

    from app.skills.registry import SkillRegistry
    from app.skills.tools.eval_tools import register

    # Seed a candidate prompt
    await repository.save_prompt_version(
        "classifier", version=5, content="You are a test classifier.", created_by="test"
    )

    # Seed a dataset entry
    await repository.save_trace(
        trace_id="t-run-eval-1",
        phone_number="test_phone",
        input_text="hello",
        message_type="text",
    )
    await repository.add_dataset_entry(
        trace_id="t-run-eval-1",
        entry_type="correction",
        input_text="hello",
        output_text="hi",
        expected_output="Hi there",
        metadata={},
    )

    ollama = AsyncMock()
    captured_messages: list = []

    async def fake_chat(messages, **kwargs):
        captured_messages.append(messages[:])
        # First call = inference → return "Hi there"
        # Second call = judge → return "yes"
        return "yes" if len(captured_messages) > 1 else "Hi there"

    ollama.chat = fake_chat

    registry = SkillRegistry(skills_dir="skills")
    register(registry, repository, ollama)

    tool_fn = registry.get_tool("run_quick_eval").handler
    result = await tool_fn(prompt_name="classifier", prompt_version=5)

    # The first call (inference) must include the candidate system prompt
    assert captured_messages, "ollama.chat was never called"
    first_call_msgs = captured_messages[0]
    system_msgs = [m for m in first_call_msgs if m.role == "system"]
    assert system_msgs, "No system message in inference call"
    assert "You are a test classifier." in system_msgs[0].content

    # Result must mention the override
    assert "classifier" in result
    assert "v5" in result


async def test_run_quick_eval_with_nonexistent_prompt_version_returns_error(repository):
    """run_quick_eval must return an error message if prompt version not found."""
    from app.skills.registry import SkillRegistry
    from app.skills.tools.eval_tools import register

    ollama = AsyncMock()
    registry = SkillRegistry(skills_dir="skills")
    register(registry, repository, ollama)

    tool_fn = registry.get_tool("run_quick_eval").handler
    result = await tool_fn(prompt_name="nonexistent_prompt", prompt_version=999)

    assert "No encontré" in result or "not found" in result.lower()
    ollama.chat.assert_not_called()


# ---------------------------------------------------------------------------
# /approve-prompt with eval
# ---------------------------------------------------------------------------


async def test_approve_prompt_shows_eval_score(repository):
    """cmd_approve_prompt must show eval score when ollama_client is available."""
    from unittest.mock import AsyncMock, MagicMock

    from app.commands.builtins import cmd_approve_prompt
    from app.commands.context import CommandContext

    # Seed a prompt version
    await repository.save_prompt_version(
        "classifier", version=3, content="classify: {msg}", created_by="agent"
    )

    ollama = AsyncMock()

    context = MagicMock(spec=CommandContext)
    context.repository = repository
    context.ollama_client = ollama

    with patch("app.commands.builtins.activate_with_eval", new_callable=AsyncMock) as mock_eval:
        mock_eval.return_value = {
            "passed": True,
            "score": 0.85,
            "details": "5/5 entries passed (85%, threshold=70%)",
            "activated": False,
            "entries_evaluated": 5,
        }
        result = await cmd_approve_prompt("classifier 3", context)

    assert "85%" in result or "0.85" in result or "✅" in result
    assert "activado" in result.lower() or "activ" in result.lower()
    # Prompt must actually be activated in DB
    row = await repository.get_active_prompt_version("classifier")
    assert row is not None
    assert row["version"] == 3


async def test_approve_prompt_shows_warning_on_low_score(repository):
    """cmd_approve_prompt must show warning (⚠️) when eval score is below threshold."""
    from unittest.mock import MagicMock

    from app.commands.builtins import cmd_approve_prompt
    from app.commands.context import CommandContext

    await repository.save_prompt_version(
        "summarizer", version=2, content="bad prompt", created_by="agent"
    )

    ollama = AsyncMock()
    context = MagicMock(spec=CommandContext)
    context.repository = repository
    context.ollama_client = ollama

    with patch("app.commands.builtins.activate_with_eval", new_callable=AsyncMock) as mock_eval:
        mock_eval.return_value = {
            "passed": False,
            "score": 0.2,
            "details": "1/5 entries passed (20%, threshold=70%)",
            "activated": False,
            "entries_evaluated": 5,
        }
        result = await cmd_approve_prompt("summarizer 2", context)

    assert "⚠️" in result or "advisory" in result.lower() or "threshold" in result.lower()
    # Must still activate despite low score (advisory, not blocking)
    row = await repository.get_active_prompt_version("summarizer")
    assert row is not None
    assert row["version"] == 2


async def test_approve_prompt_activates_even_if_eval_fails(repository):
    """If activate_with_eval raises, /approve-prompt must still activate the prompt."""
    from unittest.mock import MagicMock

    from app.commands.builtins import cmd_approve_prompt
    from app.commands.context import CommandContext

    await repository.save_prompt_version(
        "consolidator", version=3, content="some prompt", created_by="agent"
    )

    ollama = AsyncMock()
    context = MagicMock(spec=CommandContext)
    context.repository = repository
    context.ollama_client = ollama

    with patch("app.commands.builtins.activate_with_eval", new_callable=AsyncMock) as mock_eval:
        mock_eval.side_effect = Exception("LLM down")
        result = await cmd_approve_prompt("consolidator 3", context)

    # Must activate despite eval failure
    row = await repository.get_active_prompt_version("consolidator")
    assert row is not None
    assert row["version"] == 3
    assert "activado" in result.lower() or "activ" in result.lower()


async def test_approve_prompt_no_eval_without_ollama(repository):
    """When ollama_client is None, /approve-prompt must skip eval and activate directly."""
    from unittest.mock import MagicMock

    from app.commands.builtins import cmd_approve_prompt
    from app.commands.context import CommandContext

    await repository.save_prompt_version(
        "system_prompt", version=2, content="a new prompt", created_by="agent"
    )

    context = MagicMock(spec=CommandContext)
    context.repository = repository
    context.ollama_client = None

    with patch("app.commands.builtins.activate_with_eval", new_callable=AsyncMock) as mock_eval:
        await cmd_approve_prompt("system_prompt 2", context)

    mock_eval.assert_not_called()
    row = await repository.get_active_prompt_version("system_prompt")
    assert row is not None
    assert row["version"] == 2
