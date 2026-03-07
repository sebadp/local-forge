from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from app.skills.models import ToolCall, ToolDefinition, ToolResult

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Timeout for individual MCP tool calls (seconds)
MCP_TOOL_TIMEOUT = 30.0
# Timeout for connecting to an MCP server (seconds)
MCP_CONNECT_TIMEOUT = 30.0


def _make_handler(session: ClientSession, tool_name: str):
    """Create a handler bound to a specific session and tool name.

    This avoids the classic closure-over-loop-variable bug:
    without this factory, all handlers would reference the last
    tool/session from the loop.
    """

    async def handler(**kwargs: object) -> str:
        try:
            res = await asyncio.wait_for(
                session.call_tool(tool_name, arguments=kwargs),
                timeout=MCP_TOOL_TIMEOUT,
            )
            text_parts: list[str] = []
            for content in res.content:
                if content.type == "text":
                    text_parts.append(content.text)
                elif content.type == "image":
                    text_parts.append(f"[Image: {content.mimeType}]")
                elif content.type == "resource":
                    text_parts.append(f"[Resource: {content.uri}]")  # type: ignore[attr-defined]
            return "\n".join(text_parts)
        except TimeoutError:
            logger.error("MCP tool %s timed out after %.0fs", tool_name, MCP_TOOL_TIMEOUT)
            return f"Error: tool {tool_name} timed out"
        except Exception as e:
            logger.error("Error calling MCP tool %s: %s", tool_name, e)
            return f"Error: {e}"

    return handler


class McpManager:
    """Manages connections to multiple MCP servers.

    Uses per-server AsyncExitStack instances to support hot-add and
    hot-remove of individual servers without restarting the process.
    """

    def __init__(self, config_path: str = "data/mcp_servers.json"):
        self.config_path = config_path
        # Per-server stacks allow individual connect/disconnect
        self._server_stacks: dict[str, AsyncExitStack] = {}
        self._sessions: dict[str, ClientSession] = {}
        self._tools: dict[str, ToolDefinition] = {}
        self._server_descriptions: dict[str, str] = {}
        # Keep raw server configs for save/reload
        self._server_configs: dict[str, dict] = {}
        # Tracks which web-fetching backend is active: "puppeteer" | "mcp-fetch" | "unavailable"
        self._fetch_mode: str = "unavailable"

    async def initialize(self) -> None:
        """Load config and connect to all enabled servers."""
        if not os.path.exists(self.config_path):
            logger.warning("MCP config not found at %s", self.config_path)
            return

        try:
            with open(self.config_path) as f:
                data = json.load(f)
        except Exception as e:
            logger.error("Failed to load MCP config: %s", e)
            return

        servers_config = data.get("servers", {})

        for name, cfg in servers_config.items():
            # Always track the config so disabled servers are preserved on save
            self._server_configs[name] = cfg
            if "description" in cfg:
                self._server_descriptions[name] = cfg["description"]

            if not cfg.get("enabled", True):
                logger.info("MCP server %s is disabled, skipping", name)
                continue

            await self._connect_server(name, cfg)
            self._update_dynamic_categories(name)

        # Invalidate cache once after all servers are loaded
        self._invalidate_tools_cache()

        # Register the "fetch" dynamic category so URL fast-path routes to real tools
        self._register_fetch_category()

        if self._tools:
            logger.info(
                "MCP initialized: %d server(s), %d tool(s), fetch_mode=%s",
                len(self._sessions),
                len(self._tools),
                self._fetch_mode,
            )
        else:
            logger.info("MCP initialized: no tools loaded")

    async def _connect_server(self, name: str, cfg: dict) -> None:
        """Connect to a single MCP server using a dedicated per-server exit stack.

        Supports two transport types via the ``type`` config field:
        - ``"stdio"`` (default): spawns a local process via command/args.
        - ``"http"``: connects to a remote MCP server via Streamable HTTP
          (used by Smithery-hosted servers).
        """
        server_stack = AsyncExitStack()
        try:
            server_type = cfg.get("type", "stdio")

            if server_type == "http":
                url = cfg["url"]
                transport = await asyncio.wait_for(
                    server_stack.enter_async_context(streamable_http_client(url)),
                    timeout=MCP_CONNECT_TIMEOUT,
                )
                # streamable_http_client yields (read, write, get_session_id)
                read, write, _ = transport
            else:
                server_params = StdioServerParameters(
                    command=cfg["command"],
                    args=cfg.get("args", []),
                    env={**os.environ, **(cfg.get("env") or {})},
                )
                transport = await asyncio.wait_for(
                    server_stack.enter_async_context(stdio_client(server_params)),
                    timeout=MCP_CONNECT_TIMEOUT,
                )
                read, write = transport  # type: ignore[misc]

            session = await server_stack.enter_async_context(ClientSession(read, write))

            await asyncio.wait_for(session.initialize(), timeout=MCP_CONNECT_TIMEOUT)

            self._server_stacks[name] = server_stack
            self._sessions[name] = session
            logger.info("Connected to MCP server: %s (type=%s)", name, server_type)

            await self._load_tools(name, session)

        except TimeoutError:
            logger.error(
                "MCP server %s timed out during connection (%.0fs)",
                name,
                MCP_CONNECT_TIMEOUT,
            )
            with anyio.CancelScope(shield=True):
                await server_stack.aclose()
        except Exception as e:
            logger.error("Failed to connect to MCP server %s: %s", name, e)
            with anyio.CancelScope(shield=True):
                await server_stack.aclose()

    async def _load_tools(self, server_name: str, session: ClientSession) -> None:
        """Fetch tools from the server and register them as ToolDefinitions."""
        try:
            result = await asyncio.wait_for(session.list_tools(), timeout=MCP_CONNECT_TIMEOUT)
        except Exception as e:
            logger.error("Failed to list tools for server %s: %s", server_name, e)
            return

        for tool in result.tools:
            # Detect name collisions
            if tool.name in self._tools:
                existing = self._tools[tool.name].skill_name
                logger.warning(
                    "MCP tool name collision: '%s' from mcp::%s overwrites %s",
                    tool.name,
                    server_name,
                    existing,
                )

            tool_def = ToolDefinition(
                name=tool.name,
                description=tool.description or "",
                parameters=tool.inputSchema,
                handler=_make_handler(session, tool.name),
                skill_name=f"mcp::{server_name}",
            )
            self._tools[tool.name] = tool_def
            logger.info("Registered MCP tool: %s (server: %s)", tool.name, server_name)

    # ------------------------------------------------------------------
    # Hot-reload public API
    # ------------------------------------------------------------------

    async def hot_add_server(self, name: str, cfg: dict) -> str:
        """Connect a new MCP server at runtime without restarting.

        Persists the config to disk if connection succeeds.
        Returns a human-readable status message.
        """
        if name in self._sessions:
            return f"Server '{name}' is already connected."

        if "description" in cfg:
            self._server_descriptions[name] = cfg["description"]

        self._server_configs[name] = cfg
        tools_before = len(self._tools)
        await self._connect_server(name, cfg)

        if name not in self._sessions:
            self._server_configs.pop(name, None)
            self._server_descriptions.pop(name, None)
            return f"Failed to connect to server '{name}'. Check logs for details."

        new_tools = len(self._tools) - tools_before
        self._invalidate_tools_cache()
        self._update_dynamic_categories(name)
        self._register_fetch_category()
        self._persist_config()

        return f"Connected '{name}': {new_tools} new tool(s) available."

    async def hot_remove_server(self, name: str) -> str:
        """Disconnect an MCP server and remove its tools at runtime.

        Updates the persisted config (marks as disabled).
        Returns a human-readable status message.
        """
        if name not in self._sessions:
            return f"Server '{name}' is not connected."

        # Remove tools registered by this server
        to_remove = [k for k, v in self._tools.items() if v.skill_name == f"mcp::{name}"]
        for k in to_remove:
            del self._tools[k]

        # Close per-server stack
        if name in self._server_stacks:
            try:
                await self._server_stacks[name].aclose()
            except Exception as e:
                logger.warning("Error closing MCP server %s: %s", name, e)
            del self._server_stacks[name]

        del self._sessions[name]
        self._server_descriptions.pop(name, None)

        # Mark as disabled in persisted config (don't delete — allows re-enable)
        if name in self._server_configs:
            self._server_configs[name]["enabled"] = False
        self._persist_config()
        self._invalidate_tools_cache()

        return f"Disconnected '{name}', removed {len(to_remove)} tool(s)."

    def list_servers(self) -> list[dict]:
        """Return status of all known servers (connected + disabled)."""
        result = []
        seen = set()

        # Connected servers
        for name in self._sessions:
            seen.add(name)
            tool_count = sum(1 for t in self._tools.values() if t.skill_name == f"mcp::{name}")
            result.append(
                {
                    "name": name,
                    "status": "connected",
                    "tools": tool_count,
                    "description": self._server_descriptions.get(name, ""),
                }
            )

        # Configured but not connected (disabled or failed)
        for name, cfg in self._server_configs.items():
            if name in seen:
                continue
            result.append(
                {
                    "name": name,
                    "status": "disabled" if not cfg.get("enabled", True) else "disconnected",
                    "tools": 0,
                    "description": self._server_descriptions.get(name, ""),
                }
            )

        return result

    def get_fetch_mode(self) -> str:
        """Return the active web-fetch backend: 'puppeteer', 'mcp-fetch', or 'unavailable'."""
        return self._fetch_mode

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _register_fetch_category(self) -> None:
        """Detect available fetch tools and register them under the 'fetch' router category.

        Priority: Puppeteer (full JS rendering) > mcp-fetch (plain HTTP) > unavailable.
        This ensures the URL fast-path in classify_intent() routes to real tools.
        """
        from app.skills.router import register_dynamic_category

        # Puppeteer tools that are useful for URL fetching (primary)
        _PUPPETEER_FETCH_TOOLS = {
            "puppeteer_navigate",
            "puppeteer_screenshot",
            "puppeteer_evaluate",
            "puppeteer_click",
        }
        puppeteer_tools = [
            name
            for name, tool in self._tools.items()
            if tool.skill_name == "mcp::puppeteer" and name in _PUPPETEER_FETCH_TOOLS
        ]

        # mcp-fetch tools (fallback, plain HTTP)
        mcp_fetch_tools = [
            name for name, tool in self._tools.items() if tool.skill_name == "mcp::mcp-fetch"
        ]

        if puppeteer_tools:
            register_dynamic_category("fetch", puppeteer_tools)
            self._fetch_mode = "puppeteer"
            logger.info("Fetch mode: puppeteer (tools: %s)", puppeteer_tools)
        elif mcp_fetch_tools:
            register_dynamic_category("fetch", mcp_fetch_tools)
            self._fetch_mode = "mcp-fetch"
            logger.warning(
                "Fetch mode: mcp-fetch (Puppeteer unavailable — using plain HTTP fallback for URL requests)"
            )
        else:
            self._fetch_mode = "unavailable"
            logger.error("Fetch mode: unavailable — no web browsing tools connected")

    def _persist_config(self) -> None:
        """Write current server configs back to disk."""
        try:
            os.makedirs(os.path.dirname(self.config_path) or ".", exist_ok=True)
            data = {"servers": self._server_configs}
            with open(self.config_path, "w") as f:
                json.dump(data, f, indent=2)
            logger.info("MCP config saved to %s", self.config_path)
        except Exception as e:
            logger.error("Failed to persist MCP config: %s", e)

    @staticmethod
    def _invalidate_tools_cache() -> None:
        """Invalidate the executor-level tools map cache."""
        from app.skills.executor import reset_tools_cache

        reset_tools_cache()

    def _update_dynamic_categories(self, server_name: str) -> None:
        """Register newly added MCP tools into the router's TOOL_CATEGORIES."""
        from app.skills.router import register_dynamic_category

        tool_names = [k for k, v in self._tools.items() if v.skill_name == f"mcp::{server_name}"]
        if tool_names:
            register_dynamic_category(server_name, tool_names)

    # ------------------------------------------------------------------
    # Existing read-only API (unchanged)
    # ------------------------------------------------------------------

    def get_ollama_tools(self) -> list[dict]:
        """Return tool schemas in Ollama's expected format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]

    async def cleanup(self) -> None:
        """Close all connections."""
        for name, stack in list(self._server_stacks.items()):
            try:
                # Shield each close from any active anyio cancellation.
                # stdio_client / streamable_http_client use anyio cancel scopes
                # internally; exiting them while the outer scope is being cancelled
                # raises "Attempted to exit cancel scope in a different task".
                with anyio.CancelScope(shield=True):
                    await stack.aclose()
            except Exception as e:
                logger.error("Error closing MCP server %s: %s", name, e)
        self._server_stacks.clear()
        self._sessions.clear()
        self._tools.clear()
        logger.info("MCP Manager cleanup complete")

    def has_tool(self, tool_name: str) -> bool:
        return tool_name in self._tools

    async def execute_tool(self, tool_call: ToolCall) -> ToolResult:
        """Execute a tool call and return the result."""
        tool = self._tools.get(tool_call.name)
        if not tool:
            return ToolResult(
                tool_name=tool_call.name,
                content=f"Unknown MCP tool: {tool_call.name}",
                success=False,
            )
        try:
            result = await tool.handler(**tool_call.arguments)
            return ToolResult(tool_name=tool_call.name, content=result)
        except Exception as e:
            logger.exception("MCP tool %s execution failed", tool_call.name)
            return ToolResult(
                tool_name=tool_call.name,
                content=f"MCP tool error: {e}",
                success=False,
            )

    def get_tools_summary(self) -> str | None:
        """Return a summary of MCP tools grouped by server, for the system prompt."""
        if not self._tools:
            return None

        by_server: dict[str, list[ToolDefinition]] = {}
        for tool in self._tools.values():
            server = tool.skill_name.removeprefix("mcp::")  # type: ignore[union-attr]
            by_server.setdefault(server, []).append(tool)

        lines = ["Available MCP capabilities:"]
        for server, tools in by_server.items():
            desc = self._server_descriptions.get(server, server)
            lines.append(f"\n{server} ({desc}):")
            for tool in tools:
                lines.append(f"- {tool.name}: {tool.description}")

        return "\n".join(lines)

    def get_tools(self) -> dict[str, ToolDefinition]:
        return dict(self._tools)
