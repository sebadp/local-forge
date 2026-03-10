from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("mcp", reason="mcp package not installed in this environment")

from app.mcp.manager import McpManager, _make_handler
from app.skills.models import ToolCall

# --- _make_handler tests (closure bug regression) ---


async def test_make_handler_binds_correct_tool_name():
    """Each handler created by _make_handler calls the correct tool."""
    session = AsyncMock()

    # Simulate MCP response
    text_content = MagicMock()
    text_content.type = "text"
    text_content.text = "result_a"

    call_result = MagicMock()
    call_result.content = [text_content]
    session.call_tool = AsyncMock(return_value=call_result)

    handler_a = _make_handler(session, "tool_a")
    handler_b = _make_handler(session, "tool_b")

    await handler_a(x=1)
    session.call_tool.assert_called_with("tool_a", arguments={"x": 1})

    await handler_b(y=2)
    session.call_tool.assert_called_with("tool_b", arguments={"y": 2})


async def test_make_handler_flattens_text_content():
    """Handler joins multiple text content parts."""
    session = AsyncMock()

    part1 = MagicMock(type="text", text="line 1")
    part2 = MagicMock(type="text", text="line 2")

    call_result = MagicMock()
    call_result.content = [part1, part2]
    session.call_tool = AsyncMock(return_value=call_result)

    handler = _make_handler(session, "multi")
    result = await handler()
    assert result == "line 1\nline 2"


async def test_make_handler_handles_image_and_resource():
    """Handler formats image and resource content types."""
    session = AsyncMock()

    img = MagicMock(type="image", mimeType="image/png")
    res = MagicMock(type="resource", uri="file:///data/test.txt")

    call_result = MagicMock()
    call_result.content = [img, res]
    session.call_tool = AsyncMock(return_value=call_result)

    handler = _make_handler(session, "mixed")
    result = await handler()
    assert "[Image: image/png]" in result
    assert "[Resource: file:///data/test.txt]" in result


async def test_make_handler_returns_error_on_exception():
    """Handler returns error string instead of raising."""
    session = AsyncMock()
    session.call_tool = AsyncMock(side_effect=RuntimeError("connection lost"))

    handler = _make_handler(session, "broken")
    result = await handler()
    assert "Error:" in result
    assert "connection lost" in result


async def test_make_handler_timeout():
    """Handler returns timeout error on slow tools."""
    import asyncio
    from unittest.mock import patch as sync_patch

    session = AsyncMock()

    async def slow_call(*args, **kwargs):
        await asyncio.sleep(100)

    session.call_tool = slow_call

    handler = _make_handler(session, "slow_tool")

    # Patch the timeout to be very short
    with sync_patch("app.mcp.manager.MCP_TOOL_TIMEOUT", 0.01):
        result = await handler()
    assert "timed out" in result


# --- McpManager tests ---


async def test_initialize_no_config_file():
    """Manager initializes gracefully when config file doesn't exist."""
    mgr = McpManager(config_path="/nonexistent/mcp.json")
    await mgr.initialize()
    assert mgr.get_tools() == {}
    assert mgr.get_ollama_tools() == []


async def test_initialize_invalid_json(tmp_path):
    """Manager handles invalid JSON config gracefully."""
    config = tmp_path / "mcp.json"
    config.write_text("not valid json {{{")

    mgr = McpManager(config_path=str(config))
    await mgr.initialize()
    assert mgr.get_tools() == {}


async def test_initialize_empty_servers(tmp_path):
    """Manager handles config with no servers."""
    config = tmp_path / "mcp.json"
    config.write_text(json.dumps({"servers": {}}))

    mgr = McpManager(config_path=str(config))
    await mgr.initialize()
    assert mgr.get_tools() == {}


async def test_initialize_disabled_server(tmp_path):
    """Disabled servers are skipped."""
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "servers": {
                    "test": {
                        "command": "echo",
                        "args": [],
                        "enabled": False,
                    }
                }
            }
        )
    )

    mgr = McpManager(config_path=str(config))
    # _connect_server should NOT be called for disabled servers
    mgr._connect_server = AsyncMock()
    await mgr.initialize()
    mgr._connect_server.assert_not_called()


async def test_has_tool_and_execute():
    """has_tool and execute_tool work correctly."""
    mgr = McpManager(config_path="/nonexistent")

    assert not mgr.has_tool("test_tool")

    # Manually register a tool
    from app.skills.models import ToolDefinition

    async def fake_handler(**kwargs):
        return "ok"

    mgr._tools["test_tool"] = ToolDefinition(
        name="test_tool",
        description="A test",
        parameters={"type": "object", "properties": {}},
        handler=fake_handler,
        skill_name="mcp::test",
    )

    assert mgr.has_tool("test_tool")

    result = await mgr.execute_tool(ToolCall(name="test_tool", arguments={}))
    assert result.success
    assert result.content == "ok"


async def test_execute_unknown_tool():
    """execute_tool returns error for unknown tools."""
    mgr = McpManager(config_path="/nonexistent")
    result = await mgr.execute_tool(ToolCall(name="nope", arguments={}))
    assert not result.success
    assert "Unknown MCP tool" in result.content


async def test_execute_tool_handler_error():
    """execute_tool catches handler exceptions."""
    mgr = McpManager(config_path="/nonexistent")

    from app.skills.models import ToolDefinition

    async def bad_handler(**kwargs):
        raise ValueError("boom")

    mgr._tools["bad"] = ToolDefinition(
        name="bad",
        description="Fails",
        parameters={"type": "object", "properties": {}},
        handler=bad_handler,
        skill_name="mcp::test",
    )

    result = await mgr.execute_tool(ToolCall(name="bad", arguments={}))
    assert not result.success
    assert "boom" in result.content


async def test_execute_tool_error_string_sets_success_false():
    """Handler returning 'Error: ...' string should set success=False."""
    mgr = McpManager(config_path="/nonexistent")

    from app.skills.models import ToolDefinition

    async def error_handler(**kwargs):
        return "Error: Failed to launch the browser process!"

    mgr._tools["puppeteer_navigate"] = ToolDefinition(
        name="puppeteer_navigate",
        description="Navigate",
        parameters={"type": "object", "properties": {}},
        handler=error_handler,
        skill_name="mcp::puppeteer",
    )

    result = await mgr.execute_tool(ToolCall(name="puppeteer_navigate", arguments={}))
    assert not result.success
    assert "Failed to launch" in result.content


async def test_get_ollama_tools_format():
    """get_ollama_tools returns correct Ollama format."""
    mgr = McpManager(config_path="/nonexistent")

    from app.skills.models import ToolDefinition

    async def h(**kwargs):
        return ""

    mgr._tools["my_tool"] = ToolDefinition(
        name="my_tool",
        description="Does stuff",
        parameters={"type": "object", "properties": {"x": {"type": "string"}}},
        handler=h,
        skill_name="mcp::server1",
    )

    tools = mgr.get_ollama_tools()
    assert len(tools) == 1
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "my_tool"
    assert tools[0]["function"]["description"] == "Does stuff"
    assert "x" in tools[0]["function"]["parameters"]["properties"]


async def test_cleanup():
    """cleanup clears state."""
    mgr = McpManager(config_path="/nonexistent")
    mgr._exit_stack = AsyncMock()
    mgr._sessions["test"] = MagicMock()

    from app.skills.models import ToolDefinition

    async def h(**kwargs):
        return ""

    mgr._tools["t"] = ToolDefinition(
        name="t",
        description="",
        parameters={},
        handler=h,
    )

    await mgr.cleanup()
    assert mgr._sessions == {}
    assert mgr._tools == {}


# --- Tool executor integration with MCP ---


async def test_executor_routes_to_mcp_manager():
    """execute_tool_loop routes MCP tools to mcp_manager."""
    from unittest.mock import patch as sync_patch

    from app.llm.client import ChatResponse, OllamaClient
    from app.models import ChatMessage
    from app.skills.executor import execute_tool_loop
    from app.skills.models import ToolResult
    from app.skills.registry import SkillRegistry

    skill_registry = SkillRegistry(skills_dir="/nonexistent")

    # Register a local tool
    async def local_handler(**kwargs):
        return "local result"

    skill_registry.register_tool(
        name="local_tool",
        description="Local",
        parameters={"type": "object", "properties": {}},
        handler=local_handler,
    )

    # Mock MCP manager
    mcp_manager = MagicMock()
    mcp_manager.get_ollama_tools.return_value = [
        {
            "type": "function",
            "function": {
                "name": "mcp_tool",
                "description": "MCP",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    mcp_manager.has_tool.side_effect = lambda name: name == "mcp_tool"
    mcp_manager.execute_tool = AsyncMock(
        return_value=ToolResult(tool_name="mcp_tool", content="mcp result")
    )

    # Mock Ollama: first call returns MCP tool call, second returns text
    mock_http = AsyncMock()
    ollama = OllamaClient(http_client=mock_http, base_url="http://localhost:11434", model="test")
    ollama.chat_with_tools = AsyncMock(
        side_effect=[
            ChatResponse(
                content="",
                tool_calls=[{"function": {"name": "mcp_tool", "arguments": {}}}],
            ),
            ChatResponse(content="Final answer using mcp result"),
        ]
    )

    messages = [ChatMessage(role="user", content="Use MCP")]

    # Bypass router and security layer for unit test isolation
    from app.security.audit import AuditTrail
    from app.security.models import PolicyAction, PolicyDecision
    from app.security.policy_engine import PolicyEngine

    allow_engine = MagicMock(spec=PolicyEngine)
    allow_engine.evaluate.return_value = PolicyDecision(action=PolicyAction.ALLOW)
    allow_audit = MagicMock(spec=AuditTrail)
    allow_audit.record.return_value = MagicMock()

    with (
        sync_patch(
            "app.skills.executor.classify_intent", new_callable=AsyncMock, return_value=["time"]
        ),
        sync_patch(
            "app.skills.executor.select_tools",
            side_effect=lambda cats, all_tools, max_tools=8: list(all_tools.values()),
        ),
        sync_patch(
            "app.skills.executor.get_policy_engine",
            new_callable=AsyncMock,
            return_value=allow_engine,
        ),
        sync_patch(
            "app.skills.executor.get_audit_trail", new_callable=AsyncMock, return_value=allow_audit
        ),
    ):
        result = await execute_tool_loop(messages, ollama, skill_registry, mcp_manager=mcp_manager)

    assert result == "Final answer using mcp result"
    mcp_manager.execute_tool.assert_called_once()


# --- get_tools_summary tests ---


async def test_get_tools_summary_no_tools():
    """Returns None when no tools are registered."""
    mgr = McpManager(config_path="/nonexistent")
    assert mgr.get_tools_summary() is None


async def test_get_tools_summary_grouped_by_server():
    """Returns summary grouped by server with descriptions."""
    from app.skills.models import ToolDefinition

    mgr = McpManager(config_path="/nonexistent")
    mgr._server_descriptions["filesystem"] = "Read and write files"
    mgr._server_descriptions["fetch"] = "Retrieve web content"

    async def h(**kwargs):
        return ""

    mgr._tools["read_file"] = ToolDefinition(
        name="read_file",
        description="Read a file",
        parameters={},
        handler=h,
        skill_name="mcp::filesystem",
    )
    mgr._tools["write_file"] = ToolDefinition(
        name="write_file",
        description="Write a file",
        parameters={},
        handler=h,
        skill_name="mcp::filesystem",
    )
    mgr._tools["fetch_url"] = ToolDefinition(
        name="fetch_url",
        description="Fetch a URL",
        parameters={},
        handler=h,
        skill_name="mcp::fetch",
    )

    summary = mgr.get_tools_summary()
    assert summary is not None
    assert "Available MCP capabilities:" in summary
    assert "filesystem (Read and write files):" in summary
    assert "fetch (Retrieve web content):" in summary
    assert "- read_file: Read a file" in summary
    assert "- write_file: Write a file" in summary
    assert "- fetch_url: Fetch a URL" in summary


async def test_get_tools_summary_no_description_fallback():
    """Falls back to server name when no description is configured."""
    from app.skills.models import ToolDefinition

    mgr = McpManager(config_path="/nonexistent")

    async def h(**kwargs):
        return ""

    mgr._tools["some_tool"] = ToolDefinition(
        name="some_tool",
        description="Does something",
        parameters={},
        handler=h,
        skill_name="mcp::myserver",
    )

    summary = mgr.get_tools_summary()
    assert "myserver (myserver):" in summary
    assert "- some_tool: Does something" in summary
