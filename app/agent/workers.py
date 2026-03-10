"""Worker execution for planner-orchestrator agent sessions.

Workers are specialized executors that receive a single TaskStep and run
the inner tool loop with a focused prompt and filtered tool set.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from app.agent.models import AgentPlan, TaskStep
from app.models import ChatMessage
from app.skills.executor import execute_tool_loop
from app.skills.router import WORKER_TOOL_SETS, select_tools

if TYPE_CHECKING:
    from app.llm.client import OllamaClient
    from app.mcp.manager import McpManager
    from app.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

_WORKER_PROMPTS: dict[str, str] = {
    "reader": (
        "You are a thorough information gatherer. Your job is to research and summarize information.\n"
        "RESEARCH METHODOLOGY:\n"
        "1. Search with multiple queries using different keywords to find diverse sources.\n"
        "2. When you find relevant search results, use puppeteer_navigate or fetch tools to READ "
        "the full content of the most promising pages.\n"
        "3. Do NOT stop after a single search — make at least 2-3 searches with varied terms.\n"
        "4. Synthesize findings from multiple sources into a comprehensive summary.\n"
        "5. Include specific names, dates, and facts — do not leave placeholders.\n"
        "Do NOT modify anything — only read and report."
    ),
    "analyzer": (
        "You are a senior technical analyst. Your job is to analyze data and find patterns.\n"
        "METHODOLOGY:\n"
        "1. Use the data from previous steps as your starting point.\n"
        "2. Dive deeper into specific areas — read source files, check configurations, inspect CI/CD.\n"
        "3. Compare against best practices and identify concrete issues.\n"
        "4. Provide specific findings with file paths, line references, and evidence.\n"
        "Do NOT give generic advice — base all findings on actual data you've read."
    ),
    "coder": (
        "You are a software engineer. Your job is to read, understand, and modify source code.\n"
        "Use preview_patch before apply_patch. Always test after changes.\n"
        "Focus on the specific task described below."
    ),
    "reporter": (
        "You are a technical writer. Your job is to synthesize findings into a clear report.\n"
        "Use markdown formatting. Be concise and actionable.\n"
        "Include specific file paths, line numbers, and code snippets when relevant."
    ),
    "general": (
        "You are a capable assistant working on a specific sub-task.\n"
        "METHODOLOGY:\n"
        "1. Use multiple tool calls to gather comprehensive data — do NOT stop after one call.\n"
        "2. For GitHub repos: use get_file_contents to read the root directory listing first, "
        "then read key files (README.md, pyproject.toml, requirements.txt, config files).\n"
        "3. For web research: search with varied queries, then read promising pages.\n"
        "4. Always provide a detailed summary of what you found with specific data points.\n"
        "Do NOT generate sample code or templates — focus on reading and analyzing real data."
    ),
}


def build_worker_prompt(task: TaskStep, objective: str) -> str:
    """Build a focused system prompt for a worker based on its type."""
    base = _WORKER_PROMPTS.get(task.worker_type, _WORKER_PROMPTS["general"])
    return (
        f"{base}\n\n"
        f"OVERALL OBJECTIVE: {objective}\n\n"
        f"YOUR SPECIFIC TASK: {task.description}\n\n"
        "When done, provide a clear summary of what you found or accomplished."
    )


def select_worker_tools(
    task: TaskStep,
    all_tools_map: dict[str, dict],
    max_tools: int = 8,
) -> list[dict]:
    """Select tools appropriate for a worker's type.

    Uses WORKER_TOOL_SETS to map worker_type -> category list, then
    selects tools from those categories. Falls back to all categories
    for 'general' workers.
    """
    categories = WORKER_TOOL_SETS.get(task.worker_type, list(WORKER_TOOL_SETS.get("general", [])))

    # If the task specifies explicit tool names, try to include them
    if task.tools:
        selected: list[dict] = []
        seen: set[str] = set()
        for name in task.tools:
            if name in all_tools_map and name not in seen:
                selected.append(all_tools_map[name])
                seen.add(name)
        # Fill remaining budget from categories
        remaining = max_tools - len(selected)
        if remaining > 0:
            cat_tools = select_tools(categories, all_tools_map, max_tools=remaining)
            for t in cat_tools:
                t_name = t.get("function", {}).get("name")
                if t_name and t_name not in seen:
                    selected.append(t)
                    seen.add(t_name)
        return selected[:max_tools]

    return select_tools(categories, all_tools_map, max_tools=max_tools)


async def execute_worker(
    task: TaskStep,
    objective: str,
    ollama_client: OllamaClient,
    skill_registry: SkillRegistry,
    mcp_manager: McpManager | None = None,
    max_tools: int = 8,
    hitl_callback: Callable[[str, dict, str], Awaitable[bool]] | None = None,
    parent_span_id: str | None = None,
    plan: AgentPlan | None = None,
) -> str:
    """Execute a single TaskStep using the inner tool loop.

    Returns the worker's final text reply.
    """
    worker_prompt = build_worker_prompt(task, objective)

    messages: list[ChatMessage] = [
        ChatMessage(role="system", content=worker_prompt),
        ChatMessage(role="user", content=task.description),
    ]

    # Inject results from dependency tasks so workers can build on prior steps
    if task.depends_on and plan:
        dep_lines = []
        for dep_id in task.depends_on:
            dep_task = next((t for t in plan.tasks if t.id == dep_id), None)
            if dep_task and dep_task.result:
                dep_lines.append(
                    f"--- Result from step #{dep_id} ({dep_task.description[:60]}) ---\n"
                    f"{dep_task.result[:800]}"
                )
        if dep_lines:
            messages.append(
                ChatMessage(
                    role="system",
                    content="CONTEXT FROM PREVIOUS STEPS:\n" + "\n\n".join(dep_lines),
                )
            )

    # Determine categories for this worker type
    categories = WORKER_TOOL_SETS.get(task.worker_type, WORKER_TOOL_SETS.get("general", []))

    result = await execute_tool_loop(
        messages=messages,
        ollama_client=ollama_client,
        skill_registry=skill_registry,
        mcp_manager=mcp_manager,
        max_tools=max_tools,
        pre_classified_categories=list(categories),
        hitl_callback=hitl_callback,
        parent_span_id=parent_span_id,
    )

    # Detect empty results — workers that return nothing useful should signal failure
    if not result or not result.strip():
        logger.warning("Worker [%s] task #%s returned empty result", task.worker_type, task.id)
        return "(no data found — all tool calls returned empty or failed)"

    logger.info(
        "Worker [%s] task #%s completed: %s",
        task.worker_type,
        task.id,
        result[:100],
    )
    return result
