from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from app.formatting.compaction import compact_tool_output
from app.formatting.microcompact import microcompact_messages
from app.llm.client import OllamaClient
from app.models import ChatMessage
from app.security.audit import AuditTrail
from app.security.policy_engine import PolicyEngine
from app.skills.models import ToolCall
from app.skills.registry import SkillRegistry
from app.skills.router import (
    REQUEST_MORE_TOOLS_NAME,
    TOOL_CATEGORIES,
    build_request_more_tools_schema,
    classify_intent,
    select_tools,
)
from app.tracing.context import get_current_trace

if TYPE_CHECKING:
    from app.database.repository import Repository
    from app.mcp.manager import McpManager

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 5

# Truncation limits for Langfuse span inputs (avoid huge payloads)
_SYSTEM_MSG_PREVIEW_LEN = 2000
_MSG_PREVIEW_LEN = 500


def _serialize_messages_for_trace(
    messages: list[ChatMessage],
    tools: list[dict] | None = None,
) -> dict:
    """Serialize messages + tool names for Langfuse span input.

    System messages are truncated to 2000 chars (enough to see prompt + memories),
    other messages to 500 chars. Tool calls in assistant messages are included.
    """
    serialized = []
    for m in messages:
        limit = _SYSTEM_MSG_PREVIEW_LEN if m.role == "system" else _MSG_PREVIEW_LEN
        entry: dict = {"role": m.role, "content": (m.content or "")[:limit]}
        if m.tool_calls:
            entry["tool_calls"] = [tc.get("function", {}).get("name", "") for tc in m.tool_calls]
        serialized.append(entry)
    result: dict = {"messages": serialized}
    if tools:
        result["tool_names"] = [t.get("function", {}).get("name", "") for t in tools]
        result["tool_count"] = len(tools)
    return result


# Module-level cache: tools don't change at runtime after initialization
_cached_tools_map: dict[str, dict] | None = None

_policy_engine: PolicyEngine | None = None
_audit_trail: AuditTrail | None = None


async def get_policy_engine() -> PolicyEngine:
    global _policy_engine
    if _policy_engine is None:
        loop = asyncio.get_running_loop()
        _policy_engine = await loop.run_in_executor(
            None, lambda: PolicyEngine(Path("data/security_policies.yaml"))
        )
    return _policy_engine


async def get_audit_trail() -> AuditTrail:
    import os

    global _audit_trail
    if _audit_trail is None:
        loop = asyncio.get_running_loop()
        hmac_key = os.getenv("AUDIT_HMAC_KEY")
        if not hmac_key:
            logger.warning(
                "AUDIT_HMAC_KEY is not set — audit trail uses plain SHA-256 (no HMAC tamper-evidence). "
                "Set AUDIT_HMAC_KEY in .env to enable cryptographic tamper protection."
            )
        _audit_trail = await loop.run_in_executor(
            None, lambda: AuditTrail(Path("data/audit_trail.jsonl"), hmac_key=hmac_key)
        )
    return _audit_trail


def _build_tools_map(
    skill_registry: SkillRegistry,
    mcp_manager: McpManager | None,
) -> dict[str, dict]:
    """Build a name -> ollama schema dict from all available tools."""
    tools_map: dict[str, dict] = {}
    for tool_schema in skill_registry.get_ollama_tools():
        name = tool_schema["function"]["name"]
        tools_map[name] = tool_schema
    if mcp_manager:
        for tool_schema in mcp_manager.get_ollama_tools():
            name = tool_schema["function"]["name"]
            tools_map[name] = tool_schema
    return tools_map


def _get_cached_tools_map(
    skill_registry: SkillRegistry,
    mcp_manager: McpManager | None,
) -> dict[str, dict]:
    """Return cached tools map, building it on first call."""
    global _cached_tools_map
    if _cached_tools_map is None:
        _cached_tools_map = _build_tools_map(skill_registry, mcp_manager)
    return _cached_tools_map


def reset_tools_cache() -> None:
    """Invalidate the cached tools map so it rebuilds on the next request.

    Call this after hot-adding or hot-removing MCP servers, or after
    reloading skills, to ensure the executor picks up the new tools.
    """
    global _cached_tools_map
    _cached_tools_map = None
    logger.info("Tools cache invalidated")


async def _run_tool_call(
    tc: dict,
    skill_registry: SkillRegistry,
    mcp_manager: McpManager | None,
    ollama_client: OllamaClient,
    user_message: str,
    hitl_callback: Callable[[str, dict, str], Awaitable[bool]] | None = None,
    parent_span_id: str | None = None,
) -> ChatMessage:
    """Execute a single tool call and return the result as a tool message."""
    func = tc.get("function", {})
    tool_name = func.get("name", "")
    arguments = func.get("arguments", {})

    # Lazy-load skill instructions on first use (only for local skills)
    instructions = skill_registry.get_skill_instructions(tool_name)

    policy = await get_policy_engine()
    audit = await get_audit_trail()

    loop = asyncio.get_running_loop()

    decision = policy.evaluate(tool_name, arguments)

    if decision.is_blocked:
        await loop.run_in_executor(
            None,
            lambda: audit.record(
                tool_name, arguments, "block", decision.reason, "blocked_by_policy"
            ),
        )
        if policy.is_misconfigured:
            logger.error(
                "Tool %s blocked because security policy file is missing or invalid. "
                "Create data/security_policies.yaml (copy from data/security_policies.yaml.example).",
                tool_name,
            )
            error_msg = (
                f"CONFIGURATION ERROR: Tool '{tool_name}' is blocked because the security policy "
                "file is missing or invalid. Tell the user: the file "
                "'data/security_policies.yaml' does not exist or has errors. "
                "They should copy 'data/security_policies.yaml.example' to "
                "'data/security_policies.yaml' and restart the server."
            )
        else:
            error_msg = (
                f"POLICY BLOCK: Tool '{tool_name}' was blocked by security policy. "
                f"Reason: {decision.reason}. "
                "If the user needs this tool, they should add an 'allow' rule for it "
                "in 'data/security_policies.yaml'."
            )
            logger.warning("Blocked tool %s: %s", tool_name, decision.reason)
        return ChatMessage(role="tool", content=error_msg)

    if decision.requires_flag:
        if hitl_callback:
            await loop.run_in_executor(
                None,
                lambda: audit.record(
                    tool_name, arguments, "flag", decision.reason, "pending_hitl_approval"
                ),
            )
            logger.warning(f"Tool {tool_name} flagged for HITL approval: {decision.reason}")
            try:
                approved = await hitl_callback(tool_name, arguments, decision.reason or "")
                # Record HITL escalation as trace score (Plan 39 Fase 3)
                from app.tracing.context import get_current_trace as _get_trace

                _trace = _get_trace()
                if _trace:
                    try:
                        await _trace.add_score(
                            name="hitl_escalation",
                            value=1.0 if approved else 0.0,
                            source="system",
                            comment=f"tool={tool_name} approved={approved}",
                        )
                    except Exception:
                        pass
                if not approved:
                    await loop.run_in_executor(
                        None,
                        lambda: audit.record(
                            tool_name,
                            arguments,
                            "blocked_via_hitl",
                            decision.reason,
                            "denied_by_user",
                        ),
                    )
                    return ChatMessage(
                        role="tool", content="Security Policy BLOCK: Execution denied by user."
                    )
                await loop.run_in_executor(
                    None,
                    lambda: audit.record(
                        tool_name,
                        arguments,
                        "allowed_via_hitl",
                        decision.reason,
                        "approved_by_user",
                    ),
                )
            except Exception as e:
                return ChatMessage(role="tool", content=f"HITL error: {e}")
        else:
            await loop.run_in_executor(
                None,
                lambda: audit.record(
                    tool_name, arguments, "block", decision.reason, "no_hitl_available"
                ),
            )
            return ChatMessage(
                role="tool", content="Security Policy BLOCK: Flagged but no HITL provided."
            )

    tool_call = ToolCall(name=tool_name, arguments=arguments)

    trace = get_current_trace()
    if trace:
        async with trace.span(f"tool:{tool_name}", kind="tool", parent_id=parent_span_id) as span:
            span.set_input({"tool": tool_name, "arguments": arguments})
            if mcp_manager and mcp_manager.has_tool(tool_name):
                result = await mcp_manager.execute_tool(tool_call)
            else:
                result = await skill_registry.execute_tool(tool_call)
            span.set_output({"content": result.content[:1000]})
    else:
        if mcp_manager and mcp_manager.has_tool(tool_name):
            result = await mcp_manager.execute_tool(tool_call)
        else:
            result = await skill_registry.execute_tool(tool_call)

    # Runtime fallback: if a Puppeteer tool fails, retry with mcp-fetch (plain HTTP)
    if not result.success and tool_name.startswith("puppeteer_") and mcp_manager is not None:
        mcp_fetch_tools = {
            name
            for name, tool in mcp_manager.get_tools().items()
            if tool.skill_name == "mcp::mcp-fetch"
        }
        if mcp_fetch_tools:
            url = arguments.get("url") or arguments.get("name") or arguments.get("input", "")
            if url:
                fallback_name = next(
                    (t for t in ("fetch_markdown", "fetch", "fetch_txt") if t in mcp_fetch_tools),
                    None,
                )
                if fallback_name:
                    logger.warning(
                        "Puppeteer tool %s failed, retrying with mcp-fetch fallback (%s)",
                        tool_name,
                        fallback_name,
                    )
                    from app.skills.models import ToolCall as _ToolCall

                    fallback_call = _ToolCall(name=fallback_name, arguments={"url": url})
                    fallback_result = await mcp_manager.execute_tool(fallback_call)
                    prefix = "[⚠️ Fallback a mcp-fetch — Puppeteer no respondió]\n"
                    from app.skills.models import ToolResult as _ToolResult

                    result = _ToolResult(
                        tool_name=fallback_name,
                        content=prefix + fallback_result.content,
                        success=fallback_result.success,
                    )

    # Record allowed execution in audit log
    await loop.run_in_executor(
        None,
        lambda: audit.record(tool_name, arguments, "allow", decision.reason, result.content[:200]),
    )

    logger.debug("Tool Execution RAW PAYLOAD send to %s: %s", tool_name, arguments)
    logger.debug("Tool Execution RAW OUPUT from %s: %r", tool_name, result.content)
    logger.info("Tool %s -> %s", tool_name, result.content[:100])

    final_content = result.content

    # Compress the context if it is massive
    final_content = await compact_tool_output(
        tool_name=tool_name,
        text=final_content,
        user_request=user_message,
        ollama_client=ollama_client,
    )

    if instructions:
        final_content = f"{instructions}\n\nResult:\n{final_content}"

    return ChatMessage(role="tool", content=final_content)


async def execute_tool_loop(
    messages: list[ChatMessage],
    ollama_client: OllamaClient,
    skill_registry: SkillRegistry,
    mcp_manager: McpManager | None = None,
    max_tools: int = 8,
    pre_classified_categories: list[str] | None = None,
    user_facts: dict[str, str] | None = None,
    recent_messages: list[ChatMessage] | None = None,
    sticky_categories: list[str] | None = None,
    hitl_callback: Callable[[str, dict, str], Awaitable[bool]] | None = None,
    parent_span_id: str | None = None,
    repository: Repository | None = None,
    embed_model: str | None = None,
) -> str:
    """Run the tool calling loop: classify intent, select tools, execute.

    Args:
        messages: Conversation history including the latest user message.
        ollama_client: LLM client.
        skill_registry: Registry of available tools.
        mcp_manager: Optional MCP manager for external tools.
        max_tools: Maximum number of tools to offer the LLM per iteration.
        pre_classified_categories: Skip classification if already done.
        user_facts: Structured user facts from memory (e.g. github_username). Injected
            as a system message so the LLM has explicit access during tool calls.
        recent_messages: Recent conversation history for contextual classification.
        sticky_categories: Fallback categories from previous tool-using turn.
    """
    # Always extract last user message — needed for tool output compaction
    # regardless of whether intent classification is pre-computed.
    user_message = ""
    for msg in reversed(messages):
        if msg.role == "user":
            user_message = msg.content
            break

    # Stage 1: classify intent (use pre-computed result if available)
    all_tools_map = _get_cached_tools_map(skill_registry, mcp_manager)
    categories = pre_classified_categories or await classify_intent(
        user_message,
        ollama_client,
        recent_messages=recent_messages,
        sticky_categories=sticky_categories,
    )

    if categories == ["none"]:
        logger.info("Tool router: categories=none, plain chat")
        return await ollama_client.chat(messages)

    # Stage 2: select relevant tools (budget distributed proportionally across categories)
    tools = select_tools(categories, all_tools_map, max_tools=max_tools)
    logger.info(
        "Tool router: categories=%s, selected %d tools: %s",
        categories,
        len(tools),
        [t.get("function", {}).get("name") for t in tools],
    )

    if not tools:
        logger.info("Tool router: no matching tools found, plain chat")
        return await ollama_client.chat(messages)

    # Prepend the meta-tool so the LLM can request additional categories dynamically
    meta_tool_schema = build_request_more_tools_schema(list(TOOL_CATEGORIES.keys()))
    tools = [meta_tool_schema] + tools

    working_messages = list(messages)

    # Inject user facts as a system message so the LLM has explicit access during tool calls.
    # This prevents the LLM from guessing or hallucinating values like owner names in GitHub calls.
    if user_facts:
        from app.context.fact_extractor import format_facts_for_prompt

        facts_text = format_facts_for_prompt(user_facts)
        if facts_text:
            working_messages.insert(
                1 if working_messages and working_messages[0].role == "system" else 0,
                ChatMessage(role="system", content=facts_text),
            )
            logger.debug("Injected user_facts into tool loop: %s", list(user_facts.keys()))

    for iteration in range(MAX_TOOL_ITERATIONS):
        # Budget-based compaction: if context approaches the model's limit,
        # aggressively compact old tool results to prevent context overflow.
        import os as _os

        _ctx_tokens = int(_os.getenv("CONTEXT_WINDOW_TOKENS", "32768"))
        _budget_compact(working_messages, iteration, context_window_tokens=_ctx_tokens)

        trace = get_current_trace()
        iteration_span_id: str | None = None
        if trace:
            async with trace.span(
                f"llm:iteration_{iteration + 1}", kind="generation", parent_id=parent_span_id
            ) as gen_span:
                gen_span.set_input(_serialize_messages_for_trace(working_messages, tools))
                response = await ollama_client.chat_with_tools(working_messages, tools=tools)
                gen_span.set_metadata(
                    {
                        "gen_ai.usage.input_tokens": response.input_tokens,
                        "gen_ai.usage.output_tokens": response.output_tokens,
                        "gen_ai.request.model": response.model,
                    }
                )
                gen_span.set_output(
                    {
                        "content": response.content[:500],
                        "tool_calls_count": len(response.tool_calls or []),
                    }
                )
                iteration_span_id = gen_span.span_id
        else:
            response = await ollama_client.chat_with_tools(working_messages, tools=tools)

        if not response.tool_calls:
            # If the LLM chose to stop but returned empty/whitespace content,
            # nudge it to summarize what it found so far instead of returning nothing.
            if not response.content or not response.content.strip():
                if iteration > 0:
                    logger.warning(
                        "Tool iteration %d: LLM returned empty reply, nudging for summary",
                        iteration + 1,
                    )
                    working_messages.append(
                        ChatMessage(
                            role="system",
                            content=(
                                "Your reply was empty. Based on the tool results above, "
                                "provide a complete text response summarizing what you found. "
                                "If the tools returned no useful data, explain what you searched for "
                                "and what information is still needed."
                            ),
                        )
                    )
                    if trace:
                        async with trace.span(
                            f"llm:iteration_{iteration + 1}:retry",
                            kind="generation",
                            parent_id=iteration_span_id,
                        ) as retry_span:
                            retry = await ollama_client.chat_with_tools(
                                working_messages, tools=tools
                            )
                            retry_span.set_output(
                                {
                                    "content": (retry.content or "")[:300],
                                    "tool_calls_count": len(retry.tool_calls or []),
                                }
                            )
                    else:
                        retry = await ollama_client.chat_with_tools(working_messages, tools=tools)
                    if retry.content and retry.content.strip() and not retry.tool_calls:
                        return retry.content
                    # If still empty or wants to call tools, let the loop continue
                    if retry.tool_calls:
                        # Remove the nudge message — it would confuse the LLM on later iterations
                        working_messages.pop()
                        response = retry
                        # Fall through to tool execution below
                    else:
                        # Last resort: force text-only response (no tools offered)
                        logger.warning(
                            "Tool iteration %d: retry still empty, forcing text-only",
                            iteration + 1,
                        )
                        if trace:
                            async with trace.span(
                                f"llm:iteration_{iteration + 1}:forced",
                                kind="generation",
                                parent_id=iteration_span_id,
                            ) as forced_span:
                                forced = await ollama_client.chat_with_tools(
                                    working_messages, tools=None
                                )
                                forced_span.set_output({"content": (forced.content or "")[:300]})
                        else:
                            forced = await ollama_client.chat_with_tools(
                                working_messages, tools=None
                            )
                        return forced.content
                else:
                    return response.content
            else:
                logger.info(
                    "Tool iteration %d: LLM decided to reply directly: %r",
                    iteration + 1,
                    response.content[:150],
                )
                return response.content

        # If we reach here, response.tool_calls is guaranteed non-None
        # (either from the original response or from a retry that produced tool calls).
        assert response.tool_calls is not None

        tool_names = [tc.get("function", {}).get("name") for tc in response.tool_calls]
        logger.info(
            "Tool iteration %d: LLM generated %d tool call(s): %s",
            iteration + 1,
            len(response.tool_calls),
            tool_names,
        )

        # Append assistant message with tool_calls
        working_messages.append(
            ChatMessage(
                role="assistant",
                content=response.content or "",
                tool_calls=response.tool_calls,
            )
        )

        # Separate meta-tool calls (handled inline) from regular tool calls
        meta_indices = [
            i
            for i, tc in enumerate(response.tool_calls)
            if tc.get("function", {}).get("name") == REQUEST_MORE_TOOLS_NAME
        ]
        regular_indices = [
            i
            for i, tc in enumerate(response.tool_calls)
            if tc.get("function", {}).get("name") != REQUEST_MORE_TOOLS_NAME
        ]

        tool_result_map: dict[int, ChatMessage] = {}

        # Handle meta-tool calls: expand the active tool set (no security layer needed)
        for i in meta_indices:
            tc = response.tool_calls[i]
            args = tc.get("function", {}).get("arguments", {})
            requested_cats = args.get("categories", [])
            query = args.get("query", "")
            reason = args.get("reason", "")

            existing_names = {t.get("function", {}).get("name") for t in tools}
            added: list[str] = []

            # Path 1: category-based (existing behavior)
            if requested_cats:
                new_tools = select_tools(requested_cats, all_tools_map, max_tools=max_tools)
                for tool_schema in new_tools:
                    tool_schema_name = tool_schema.get("function", {}).get("name")
                    if tool_schema_name and tool_schema_name not in existing_names:
                        tools.append(tool_schema)
                        existing_names.add(tool_schema_name)
                        added.append(tool_schema_name)

            # Path 2: semantic search (Tool RAG) — query in natural language
            if query and repository and embed_model:
                try:
                    query_emb = await ollama_client.embed([query], model=embed_model)
                    similar_names = await repository.search_similar_tools(query_emb[0], top_k=5)
                    for name in similar_names:
                        if name in all_tools_map and name not in existing_names:
                            tools.append(all_tools_map[name])
                            existing_names.add(name)
                            added.append(name)
                except Exception:
                    logger.warning(
                        "Semantic tool search failed in request_more_tools", exc_info=True
                    )

            logger.info(
                "request_more_tools: cats=%s, query=%r, added=%d: %s (reason: %r)",
                requested_cats,
                query,
                len(added),
                added,
                reason,
            )
            if added:
                confirmation = (
                    f"Loaded {len(added)} new tools: {', '.join(added)}. "
                    "IMPORTANT: Call these tools NOW to complete the task. "
                    "Do NOT reply with text saying you will use them — actually call them immediately."
                )
            else:
                confirmation = "No new tools added (already available or unknown category/query)"
            tool_result_map[i] = ChatMessage(role="tool", content=confirmation)

        # Execute regular tool calls in parallel, preserving original index for ordering
        if regular_indices:
            regular_results = await asyncio.gather(
                *[
                    _run_tool_call(
                        response.tool_calls[i],
                        skill_registry,
                        mcp_manager,
                        ollama_client,
                        user_message,
                        hitl_callback,
                        parent_span_id=iteration_span_id,
                    )
                    for i in regular_indices
                ]
            )
            for i, msg in zip(regular_indices, regular_results, strict=True):
                tool_result_map[i] = msg

        # Append results in original call order
        working_messages.extend(tool_result_map[i] for i in sorted(tool_result_map))

        # MicroCompact: selectively clear old verbose tool results to free context.
        # Only compacts known-verbose tools (web_search, read_source_file, etc.)
        # from rounds that are >=2 iterations old.  Deterministic, no LLM call.
        working_messages = microcompact_messages(
            working_messages, max_age_rounds=2, current_round=iteration,
        )

    # Safety: exceeded max iterations, force a text response without tools
    logger.warning("Max tool iterations (%d) reached, forcing text response", MAX_TOOL_ITERATIONS)
    # Inject a nudge so the LLM summarizes what it found instead of returning empty
    working_messages.append(
        ChatMessage(
            role="system",
            content=(
                "You have reached the maximum number of tool calls. "
                "Summarize what you have found so far based on the tool results above. "
                "Do NOT call any more tools. Provide a complete text response now."
            ),
        )
    )
    response = await ollama_client.chat_with_tools(working_messages, tools=None)
    return response.content


def _budget_compact(
    working_messages: list[ChatMessage],
    iteration: int,
    context_window_tokens: int = 32768,
) -> None:
    """Trigger aggressive compaction when context approaches the model's limit."""
    from app.context.token_estimator import estimate_tokens

    total_text = "".join(m.content or "" for m in working_messages)
    estimated = estimate_tokens(total_text)

    budget = context_window_tokens

    if estimated > int(budget * 0.8):
        logger.warning(
            "Budget compaction triggered: %d tokens estimated (%.0f%% of %d budget)",
            estimated,
            (estimated / budget) * 100,
            budget,
        )
        working_messages[:] = microcompact_messages(
            working_messages, max_age_rounds=1, current_round=iteration,
        )
        _clear_old_tool_results(working_messages, keep_last_n=1)


def _clear_old_tool_results(messages: list[ChatMessage], keep_last_n: int = 2) -> None:
    """Replace old tool results with compact summaries to free context window space.

    Keeps the last `keep_last_n` tool messages intact (most recent are most useful).
    This implements the Anthropic-recommended 'tool result clearing' pattern:
    once a raw API response is processed, there's no reason to keep it verbatim.
    """
    tool_indices = [i for i, m in enumerate(messages) if m.role == "tool"]

    if len(tool_indices) <= keep_last_n:
        return  # Not enough tool results to warrant clearing

    for idx in tool_indices[:-keep_last_n]:
        old_content = messages[idx].content
        first_line = old_content.split("\n")[0][:120].strip()
        messages[idx] = ChatMessage(
            role="tool",
            content=f"[Previous result processed — summary: {first_line}]",
        )
