"""Subagent: mini agent loop for complex tasks.

A subagent is a focused agent with a specific objective, a subset of tools,
and its own tool calling loop. It runs within the parent session's async
context but has independent message history.

Used by the planner-orchestrator when a task is too complex for a single
worker turn (multiple action verbs, >150 chars description).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.models import ChatMessage

if TYPE_CHECKING:
    from app.llm.client import OllamaClient
    from app.mcp.manager import McpManager
    from app.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

_MAX_CONCURRENT_SUBAGENTS = 3

_SUBAGENT_SYSTEM_PROMPT = """\
You are a focused agent executing a specific task autonomously.

TASK: {objective}

{context}

RULES:
- Focus ONLY on the task above. Do not do extra work.
- Use tools to accomplish the task. Read before writing.
- When done, provide a concise summary of what you did and the results.
- If you cannot complete the task after several attempts, explain what failed.
"""


@dataclass
class SubagentConfig:
    """Configuration for a subagent fork."""

    objective: str
    tool_names: list[str] = field(default_factory=list)
    max_iterations: int = 5
    timeout_seconds: float = 120.0
    parent_session_id: str | None = None
    context: str = ""  # Additional context from parent (e.g. prior task results)


def should_use_subagent(description: str, worker_type: str = "general") -> bool:
    """Heuristic: decide whether a task should be elevated to a subagent.

    A subagent is warranted when the task description implies multiple
    sequential actions (read → analyze → write).
    """
    if worker_type not in ("general", "coder"):
        return False

    action_words = [
        "create", "build", "implement", "write", "modify", "add",
        "fix", "test", "analyze", "read", "search", "generate",
    ]
    count = sum(1 for w in action_words if w in description.lower())
    return count >= 3 or len(description) > 150


async def run_subagent(
    config: SubagentConfig,
    ollama_client: OllamaClient,
    skill_registry: SkillRegistry,
    mcp_manager: McpManager | None = None,
    hitl_callback=None,
) -> str:
    """Run a mini agent loop with focused tools and objective.

    Returns the final text result from the subagent.
    """
    from app.skills.executor import execute_tool_loop
    from app.skills.router import TOOL_CATEGORIES

    # Build system prompt
    system_content = _SUBAGENT_SYSTEM_PROMPT.format(
        objective=config.objective,
        context=config.context or "",
    )

    messages = [
        ChatMessage(role="system", content=system_content),
        ChatMessage(role="user", content=config.objective),
    ]

    # Determine categories from tool names
    tool_to_cat: dict[str, str] = {}
    for cat, names in TOOL_CATEGORIES.items():
        for name in names:
            tool_to_cat[name] = cat

    if config.tool_names:
        categories = list({tool_to_cat.get(t, "general") for t in config.tool_names})
    else:
        categories = ["general"]

    logger.info(
        "subagent.start: objective=%s, tools=%d, max_iter=%d, timeout=%.0fs",
        config.objective[:80],
        len(config.tool_names),
        config.max_iterations,
        config.timeout_seconds,
    )

    try:
        result = await asyncio.wait_for(
            execute_tool_loop(
                messages=messages,
                ollama_client=ollama_client,
                skill_registry=skill_registry,
                mcp_manager=mcp_manager,
                max_tools=8,
                pre_classified_categories=categories,
                hitl_callback=hitl_callback,
            ),
            timeout=config.timeout_seconds,
        )
        logger.info("subagent.done: %s", (result or "")[:100])
        return result or "(subagent returned empty result)"
    except TimeoutError:
        logger.warning("subagent.timeout: %s (%.0fs)", config.objective[:60], config.timeout_seconds)
        return f"(subagent timed out after {config.timeout_seconds}s)"
    except Exception as e:
        logger.exception("subagent.error: %s", config.objective[:60])
        return f"(subagent error: {e})"
