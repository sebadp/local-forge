"""Tests for compact_tool_output (formatting/compaction.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.formatting.compaction import compact_tool_output


async def test_compact_returns_as_is_when_small():
    """Text under max_length is returned unchanged without any LLM call."""
    ollama = AsyncMock()
    result = await compact_tool_output(
        "my_tool", "short text", "user request", ollama, max_length=1000
    )
    assert result == "short text"
    ollama.chat.assert_not_called()


async def test_compact_json_extraction_before_llm():
    """JSON-extractable payloads should NOT call LLM."""
    import json

    ollama = AsyncMock()
    items = [
        {"name": f"repo{i}", "id": i, "html_url": f"https://github.com/{i}"} for i in range(50)
    ]
    big_json = json.dumps(items)

    result = await compact_tool_output(
        "list_repos", big_json, "list my repos", ollama, max_length=200
    )

    # JSON extraction should have worked — LLM should NOT be called
    ollama.chat.assert_not_called()
    # Result should be valid JSON with fewer items
    assert len(result) <= 200 or result.endswith("…[truncated]")


async def test_compact_llm_uses_think_false():
    """When LLM compaction is needed, chat() must be called with think=False."""
    ollama = AsyncMock()
    ollama.chat = AsyncMock(return_value="Compacted output")

    # Use non-JSON text so JSON extraction fails and LLM is used
    long_text = "This is plain text " * 200  # >1000 chars

    with patch("app.formatting.compaction.get_current_trace", return_value=None):
        await compact_tool_output("my_tool", long_text, "summarize it", ollama, max_length=100)

    ollama.chat.assert_called_once()
    _, kwargs = ollama.chat.call_args
    assert kwargs.get("think") is False


async def test_compact_prompt_has_no_full_result_note():
    """The LLM prompt must NOT contain the 'full result is available' filler instruction."""
    ollama = AsyncMock()
    ollama.chat = AsyncMock(return_value="Compacted output")

    long_text = "plain text data " * 200

    captured_messages: list = []

    async def capture_chat(messages, **kwargs):
        captured_messages.extend(messages)
        return "Compacted output"

    ollama.chat = capture_chat

    with patch("app.formatting.compaction.get_current_trace", return_value=None):
        await compact_tool_output("my_tool", long_text, "request", ollama, max_length=100)

    prompt_text = " ".join(m.content for m in captured_messages)
    assert "full result is available" not in prompt_text.lower()
    assert "available on request" not in prompt_text.lower()


async def test_compact_hard_truncate_on_llm_failure():
    """When LLM raises, result must be hard-truncated with '…[truncated]' suffix."""
    ollama = AsyncMock()
    ollama.chat = AsyncMock(side_effect=Exception("LLM down"))

    long_text = "x" * 500

    with patch("app.formatting.compaction.get_current_trace", return_value=None):
        result = await compact_tool_output("my_tool", long_text, "request", ollama, max_length=100)

    assert result.endswith("…[truncated]")
    assert len(result) <= 120  # 100 chars + suffix
