"""Planner agent: creates and revises structured plans for agentic sessions.

The planner is a separate LLM call from the workers. It focuses on:
1. UNDERSTAND — reading context and decomposing the objective into tasks
2. SYNTHESIZE — reviewing worker results and deciding to respond or replan

The planner outputs JSON plans that the orchestrator loop uses to dispatch workers.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app.agent.models import AgentPlan, TaskStep
from app.models import ChatMessage
from app.tracing.context import get_current_trace

if TYPE_CHECKING:
    from app.llm.client import OllamaClient

logger = logging.getLogger(__name__)

_PLANNER_SYSTEM_PROMPT = """\
You are a task planner. Decompose the objective into concrete steps.

OBJECTIVE: {objective}

{context_block}

Reply with ONLY a JSON object in this exact format:
```json
{{
  "context_summary": "Brief summary of the task",
  "tasks": [
    {{"id": 1, "description": "Read the repository structure and key files", "worker_type": "reader", "depends_on": []}},
    {{"id": 2, "description": "Analyze architecture and code quality", "worker_type": "analyzer", "depends_on": [1]}},
    {{"id": 3, "description": "Write final report with findings", "worker_type": "reporter", "depends_on": [2]}}
  ]
}}
```

Worker types and their tools:
- "reader": web search, fetch web pages, GitHub API (get_file_contents, search_repositories), read files, news
- "analyzer": GitHub API, web search, INTERNAL system metrics (get_eval_summary, get_latency_stats, get_agent_stats, get_search_stats, get_dashboard_stats), source code inspection, debugging (review_interactions, diagnose_trace)
- "coder": source code tools, shell commands, GitHub (commits, PRs)
- "reporter": INTERNAL system metrics (get_eval_summary, get_latency_stats, get_dashboard_stats), notes, debugging tools
- "general": all of the above (use when task spans multiple domains)

IMPORTANT: When the user asks for "system statistics", "latencies", "p50/p95", "metrics", "performance data", or "dashboard" — these refer to THIS system's internal metrics stored in the database. Use "analyzer" or "reporter" workers with evaluation/debugging tools. Do NOT search the web for external monitoring tools.

Rules:
- Each task should be substantial (3-5 tool calls). Do NOT make single-tool-call tasks.
- Use depends_on for ordering. Tasks without dependencies can run in sequence.
- Maximum 6 tasks. Prefer 3-4 focused tasks over 6 tiny ones.
- For GitHub repo analysis: first READ the repo structure and key files (README, config, source), then ANALYZE patterns and issues, then REPORT findings.
- Output ONLY the JSON object. No other text.
"""

_REPLAN_SYSTEM_PROMPT = """\
You are a task planner reviewing results from completed steps.

OBJECTIVE: {objective}

COMPLETED STEPS AND RESULTS:
{completed_steps}

REMAINING STEPS:
{remaining_steps}

Based on the results so far, decide:
1. If the objective is achieved, output: {{"action": "done", "summary": "brief summary of findings"}}
2. If remaining steps are still valid, output: {{"action": "continue"}}
3. If the plan needs adjustment, output a NEW plan:
{{
  "action": "replan",
  "context_summary": "updated understanding",
  "tasks": [... new tasks ...]
}}

Output ONLY valid JSON, nothing else.
"""

_SYNTHESIZE_SYSTEM_PROMPT = """\
You are summarizing the results of a completed agent session.

OBJECTIVE: {objective}

CONTEXT: {context_summary}

ALL STEP RESULTS:
{all_results}

Write a concise, actionable summary of what was accomplished and any key findings.
Keep it under 500 words. Use markdown formatting.

IMPORTANT: If step results are empty, "(no output)", or contain only error messages,
report HONESTLY that the task could not be completed. Explain what was attempted and
what failed. Do NOT fabricate, invent, or hallucinate results. Never claim success
if the data was not actually gathered.
"""


def _parse_plan_json(raw: str, objective: str) -> AgentPlan:
    """Parse LLM output into an AgentPlan, with tolerant fallback."""
    # Try to extract JSON from the response (handle markdown fences)
    text = raw.strip()
    if text.startswith("```"):
        # Remove markdown code fences
        lines = text.split("\n")
        text = "\n".join(line for line in lines if not line.strip().startswith("```")).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end])
            except json.JSONDecodeError:
                logger.warning("Planner JSON parse failed, using fallback plan")
                return _fallback_plan(objective)
        else:
            logger.warning("No JSON found in planner output, using fallback plan")
            return _fallback_plan(objective)

    context_summary = data.get("context_summary", "")
    raw_tasks = data.get("tasks", [])

    if not raw_tasks:
        return _fallback_plan(objective)

    import re as _re

    tasks: list[TaskStep] = []
    for t in raw_tasks[:6]:  # Cap at 6 tasks
        # Coerce ID to int — LLMs sometimes generate strings like "#3-reader"
        raw_id = t.get("id", len(tasks) + 1)
        if isinstance(raw_id, int):
            task_id = raw_id
        else:
            match = _re.search(r"\d+", str(raw_id))
            task_id = int(match.group()) if match else len(tasks) + 1

        # Coerce depends_on entries to int as well
        raw_deps = t.get("depends_on", [])
        deps: list[int] = []
        for d in raw_deps:
            if isinstance(d, int):
                deps.append(d)
            else:
                dep_match = _re.search(r"\d+", str(d))
                if dep_match:
                    deps.append(int(dep_match.group()))

        tasks.append(
            TaskStep(
                id=task_id,
                description=t.get("description", "Execute objective"),
                worker_type=t.get("worker_type", "general"),
                tools=t.get("tools", []),
                depends_on=deps,
            )
        )

    return AgentPlan(
        objective=objective,
        context_summary=context_summary,
        tasks=tasks,
    )


def _fallback_plan(objective: str) -> AgentPlan:
    """Create a single-task fallback plan when JSON parsing fails."""
    return AgentPlan(
        objective=objective,
        context_summary="(fallback: planner could not generate structured plan)",
        tasks=[
            TaskStep(
                id=1,
                description=objective,
                worker_type="general",
            )
        ],
    )


async def create_plan(
    objective: str,
    ollama_client: OllamaClient,
    context_info: str = "",
    repository: object | None = None,
) -> AgentPlan:
    """Phase 1 — UNDERSTAND: create a structured plan for the objective.

    Args:
        objective: The user's goal or task description.
        ollama_client: LLM client for the planning call.
        context_info: Optional pre-fetched context (e.g. recent messages, file listing).
    """
    context_block = ""
    if context_info:
        context_block = f"AVAILABLE CONTEXT:\n{context_info}\n"

    try:
        from app.eval.prompt_manager import get_active_prompt

        planner_template = (
            await get_active_prompt("planner_create", repository, _PLANNER_SYSTEM_PROMPT)
            if repository
            else _PLANNER_SYSTEM_PROMPT
        )
    except Exception:
        planner_template = _PLANNER_SYSTEM_PROMPT

    system_content = planner_template.format(
        objective=objective,
        context_block=context_block,
    )
    messages = [
        ChatMessage(role="system", content=system_content),
        ChatMessage(role="user", content=f"Create a plan for: {objective}"),
    ]

    try:
        trace = get_current_trace()
        if trace:
            async with trace.span("llm:planner_create", kind="generation") as _span:
                _span.set_input({"objective": objective[:200]})
                response = await ollama_client.chat_with_tools(messages, tools=None, think=True)
                _span.set_metadata(
                    {
                        "gen_ai.usage.input_tokens": response.input_tokens,
                        "gen_ai.usage.output_tokens": response.output_tokens,
                        "gen_ai.request.model": response.model,
                    }
                )
                plan = _parse_plan_json(response.content, objective)
                _span.set_output(
                    {"tasks": len(plan.tasks), "context_summary": plan.context_summary[:200]}
                )
        else:
            response = await ollama_client.chat_with_tools(messages, tools=None, think=True)
            plan = _parse_plan_json(response.content, objective)
        logger.info(
            "Planner created plan: %d tasks, context=%s",
            len(plan.tasks),
            plan.context_summary[:80],
        )
        return plan
    except Exception:
        logger.exception("Planner failed, using fallback")
        return _fallback_plan(objective)


async def replan(
    plan: AgentPlan,
    ollama_client: OllamaClient,
    repository: object | None = None,
) -> AgentPlan | None:
    """Phase 3 — SYNTHESIZE/REPLAN: review results and decide next steps.

    Returns:
        - None if the objective is complete (action=done) or we should continue as-is
        - A new AgentPlan if replanning was needed
    """
    if plan.replans >= plan.max_replans:
        logger.warning("Max replans (%d) reached, continuing with current plan", plan.max_replans)
        return None

    completed_lines = []
    remaining_lines = []
    for t in plan.tasks:
        if t.status == "done":
            result_preview = (t.result or "")[:200]
            completed_lines.append(
                f"#{t.id} [{t.worker_type}] {t.description}\n  Result: {result_preview}"
            )
        elif t.status == "failed":
            completed_lines.append(
                f"#{t.id} [{t.worker_type}] {t.description}\n  Result: FAILED - {t.result or 'unknown error'}"
            )
        else:
            remaining_lines.append(f"#{t.id} [{t.worker_type}] {t.description}")

    try:
        from app.eval.prompt_manager import get_active_prompt

        replan_template = (
            await get_active_prompt("planner_replan", repository, _REPLAN_SYSTEM_PROMPT)
            if repository
            else _REPLAN_SYSTEM_PROMPT
        )
    except Exception:
        replan_template = _REPLAN_SYSTEM_PROMPT

    system_content = replan_template.format(
        objective=plan.objective,
        completed_steps="\n".join(completed_lines) or "(none yet)",
        remaining_steps="\n".join(remaining_lines) or "(all done)",
    )
    messages = [
        ChatMessage(role="system", content=system_content),
        ChatMessage(role="user", content="Review progress and decide next action."),
    ]

    try:
        trace = get_current_trace()
        if trace:
            async with trace.span("llm:planner_replan", kind="generation") as _span:
                _span.set_input(
                    {
                        "replans": plan.replans,
                        "tasks_done": sum(1 for t in plan.tasks if t.status == "done"),
                    }
                )
                response = await ollama_client.chat_with_tools(messages, tools=None, think=True)
                _span.set_metadata(
                    {
                        "gen_ai.usage.input_tokens": response.input_tokens,
                        "gen_ai.usage.output_tokens": response.output_tokens,
                        "gen_ai.request.model": response.model,
                    }
                )
                _span.set_output({"raw_preview": response.content[:200]})
        else:
            response = await ollama_client.chat_with_tools(messages, tools=None, think=True)
        text = response.content.strip()

        # Parse the JSON response
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(ln for ln in lines if not ln.strip().startswith("```")).strip()

        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(text[start:end])
        else:
            logger.warning("Replan: no JSON found, continuing")
            return None

        action = data.get("action", "continue")

        if action == "done":
            # Mark all remaining tasks as done
            for t in plan.tasks:
                if t.status == "pending":
                    t.status = "done"
                    t.result = data.get("summary", "Objective achieved")
            return None

        if action == "replan":
            new_plan = _parse_plan_json(json.dumps(data), plan.objective)
            new_plan.replans = plan.replans + 1
            logger.info("Replanned (attempt %d): %d tasks", new_plan.replans, len(new_plan.tasks))
            return new_plan

        # action == "continue" or unknown
        return None

    except Exception:
        logger.exception("Replan failed, continuing with current plan")
        return None


async def synthesize(
    plan: AgentPlan,
    ollama_client: OllamaClient,
    repository: object | None = None,
) -> str:
    """Generate a final summary from all step results."""
    result_lines = []
    for t in plan.tasks:
        status_icon = "done" if t.status == "done" else "failed"
        result_preview = (t.result or "(no output)")[:2000]
        result_lines.append(f"#{t.id} [{status_icon}] {t.description}\n{result_preview}")

    try:
        from app.eval.prompt_manager import get_active_prompt

        synth_template = (
            await get_active_prompt("planner_synthesize", repository, _SYNTHESIZE_SYSTEM_PROMPT)
            if repository
            else _SYNTHESIZE_SYSTEM_PROMPT
        )
    except Exception:
        synth_template = _SYNTHESIZE_SYSTEM_PROMPT

    system_content = synth_template.format(
        objective=plan.objective,
        context_summary=plan.context_summary,
        all_results="\n\n".join(result_lines),
    )
    messages = [
        ChatMessage(role="system", content=system_content),
        ChatMessage(role="user", content="Summarize the results."),
    ]

    try:
        trace = get_current_trace()
        if trace:
            async with trace.span("llm:planner_synthesize", kind="generation") as _span:
                tasks_done = sum(1 for t in plan.tasks if t.status == "done")
                _span.set_input({"tasks_done": tasks_done, "tasks_total": len(plan.tasks)})
                response = await ollama_client.chat_with_tools(messages, tools=None, think=True)
                _span.set_metadata(
                    {
                        "gen_ai.usage.input_tokens": response.input_tokens,
                        "gen_ai.usage.output_tokens": response.output_tokens,
                        "gen_ai.request.model": response.model,
                    }
                )
                _span.set_output({"reply_preview": response.content[:300]})
        else:
            response = await ollama_client.chat_with_tools(messages, tools=None, think=True)
        return response.content
    except Exception:
        logger.exception("Synthesis failed, returning raw results")
        return "\n\n".join(f"Step {t.id}: {t.result or '(no result)'}" for t in plan.tasks)
