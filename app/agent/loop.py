"""Agentic session loop: planner-orchestrator architecture.

3-phase execution model:
  Phase 1 — UNDERSTAND: Planner reads context and creates a structured plan
  Phase 2 — EXECUTE: Workers execute each task step with focused tools
  Phase 3 — SYNTHESIZE: Planner reviews results, decides to respond or replan

The planner creates an AgentPlan (JSON list of TaskSteps) which is dispatched
to type-specific workers. Each worker runs execute_tool_loop with a focused
prompt and filtered tool set. The legacy reactive loop is preserved as the
fallback when the planner fails to produce valid JSON.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from app.agent.models import AgentSession, AgentStatus
from app.agent.persistence import append_to_session
from app.agent.planner import create_plan, replan, synthesize
from app.agent.workers import execute_worker
from app.models import ChatMessage
from app.skills.executor import _clear_old_tool_results, execute_tool_loop
from app.tracing.context import TraceContext, get_current_trace

if TYPE_CHECKING:
    from app.llm.client import OllamaClient
    from app.mcp.manager import McpManager
    from app.skills.registry import SkillRegistry
    from app.whatsapp.client import WhatsAppClient

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Active sessions indexed by phone number - one concurrent session per user
_active_sessions: dict[str, AgentSession] = {}
# asyncio Tasks for each active session, so we can actually cancel them
_active_tasks: dict[str, asyncio.Task] = {}

# Tools per round: conservative cap so each round can do 1-2 meaningful actions
_TOOLS_PER_ROUND = 8

# Loop detection thresholds
_LOOP_WARNING_THRESHOLD = 3
_LOOP_CIRCUIT_BREAKER = 5
_LOOP_HISTORY_SIZE = 20

_AGENT_SYSTEM_PROMPT = """\
You are a senior software engineer working autonomously on this codebase.

OBJECTIVE: {objective}

WORKFLOW:
1. UNDERSTAND: list_source_files, read_source_file, search_source_code to learn the codebase.
2. PLAN: create_task_plan with concrete, small steps.
3. EXECUTE: Use preview_patch FIRST to verify diffs visualmente, then apply_patch for actual edits. Use write_source_file only for NEW files.
4. TEST: run_command("pytest ...") after EVERY code change.
5. FIX: if tests fail, read errors, fix, re-test (max 3 attempts per step).
6. DELIVER: git_commit, git_push when all tests pass.

RULES:
- Always test after edits. Never commit untested code.
- Prefer preview_patch before apply_patch to catch formatting/indentation mistakes.
- Use apply_patch for edits to existing files. Only use write_source_file for NEW files.
- Use conventional commit messages: "fix: ...", "feat: ...", "refactor: ..."
- If a step fails 3 times, skip it and move to the next. Note the failure in the plan.
- Ask for approval (request_user_approval) before destructive operations.
- After completing each step, call update_task_status to mark it done.
- For large files (>200 lines): use get_file_outline first, then read_lines for specific sections.
  Do NOT use read_source_file on files >200 lines — use the outline+read_lines pattern.
- When ALL steps are done, write a concise summary of what was accomplished.

PROJECT GENERATION: When the user asks to create a project, app, or website:
1. Use list_project_templates to see available templates (html-static, python-fastapi, react-vite, nextjs)
2. Use scaffold_project to create the project from a template
3. Customize the generated files with write_source_file or apply_patch
4. Use deliver_project to deliver the result (github, zip, or preview)

{workspace_info}

SCRATCHPAD: You can use <scratchpad>...</scratchpad> tags in your replies to persist notes between \
rounds. Content inside scratchpad tags will be saved and re-injected in the next round. Use this \
for: key findings, file paths discovered, decisions made, test results, partial progress. \
The scratchpad content is NOT shown to the user — it is only visible to you in the next round.
"""

_PLAN_REMINDER = """\

--- CURRENT TASK PLAN ---
{task_plan}
--- END TASK PLAN ---

Continue executing the next pending [ ] step. Do not repeat steps already marked [x].
"""


def _get_workspace_info_block() -> str:
    """Build workspace info for agent system prompt."""
    try:
        from app.skills.tools.workspace_tools import _engine

        if _engine and _engine.projects_root:
            root = _engine.get_active_root()
            workspaces = _engine.list_workspaces()
            ws_names = ", ".join(w["name"] for w in workspaces[:10]) if workspaces else "(none)"
            return (
                f"WORKSPACE: Active workspace at `{root}`\n"
                f"Available workspaces: {ws_names}\n"
                f"Projects root: `{_engine.projects_root}`"
            )
    except Exception:
        pass
    return ""


def _register_session_tools(
    session: AgentSession,
    skill_registry: SkillRegistry,
    wa_client: WhatsAppClient,
) -> SkillRegistry:
    """Create a session-scoped copy of the registry and register HITL + task-memory tools.

    Returns a new SkillRegistry derived from skill_registry so that concurrent
    agent sessions do not overwrite each other's handler closures.
    """
    from app.agent.hitl import request_user_approval as _hitl_request
    from app.agent.task_memory import register_task_memory_tools
    from app.skills.registry import SkillRegistry as _Reg

    # Shallow copy: inherits all existing tools, skills metadata, and adds session-specific ones on top
    session_registry = _Reg(skills_dir=skill_registry._skills_dir)  # type: ignore[attr-defined]
    session_registry._tools = dict(skill_registry._tools)  # type: ignore[attr-defined]  # copy tool map
    session_registry._skills = dict(skill_registry._skills)  # type: ignore[attr-defined]  # copy skill metadata for get_skill_instructions()
    session_registry._loaded_instructions = set(skill_registry._loaded_instructions)  # type: ignore[attr-defined]

    # Register the three task-memory tools
    register_task_memory_tools(session_registry, lambda: session)

    # Register the HITL approval tool
    async def request_user_approval(question: str) -> str:
        """Pause the agent session and send a question to the user via WhatsApp.

        The session will resume as soon as the user replies.
        Use this before irreversible actions (commits, pushes, file overwrites).
        """
        session.status = AgentStatus.WAITING_USER
        try:
            result = await _hitl_request(
                phone_number=session.phone_number,
                question=question,
                wa_client=wa_client,
            )
        finally:
            session.status = AgentStatus.RUNNING
        return result

    session_registry.register_tool(
        name="request_user_approval",
        description=(
            "Pause the agent session and ask the user a question via WhatsApp. "
            "The session resumes when the user replies. "
            "Use this before irreversible actions like commits or file deletions."
        ),
        parameters={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the user. Be specific about what you need approval for.",
                },
            },
            "required": ["question"],
        },
        handler=request_user_approval,
        skill_name="agent",
    )

    return session_registry


def _inject_scratchpad(messages: list[ChatMessage], scratchpad: str) -> None:
    """Insert scratchpad as a system message after the main system prompt.

    Replaces an existing scratchpad injection if present (avoids accumulation).
    """
    scratchpad_msg = ChatMessage(
        role="system",
        content=f"<scratchpad_context>\n{scratchpad}\n</scratchpad_context>",
    )
    for i, msg in enumerate(messages):
        if msg.role == "system" and "<scratchpad_context>" in msg.content:
            messages[i] = scratchpad_msg
            return
    # Insert after the main system prompt
    insert_pos = 1 if messages and messages[0].role == "system" else 0
    messages.insert(insert_pos, scratchpad_msg)


def _extract_scratchpad(reply: str) -> tuple[str, str]:
    """Extract <scratchpad>...</scratchpad> content from the agent reply.

    Returns:
        (scratchpad_content, clean_reply) — scratchpad tags removed from the reply.
    """
    import re

    pattern = re.compile(r"<scratchpad>(.*?)</scratchpad>", re.DOTALL)
    match = pattern.search(reply)
    if not match:
        return "", reply
    scratchpad_content = match.group(1).strip()
    clean_reply = pattern.sub("", reply).strip()
    return scratchpad_content, clean_reply


def _inject_task_plan(messages: list[ChatMessage], task_plan: str) -> None:
    """Insert or update the task plan reminder as the second system message.

    Replaces the previous plan reminder if one exists, to avoid duplication.
    Always keeps it right after the main system prompt (index 1).
    """
    plan_content = _PLAN_REMINDER.format(task_plan=task_plan)

    # Find and replace an existing plan reminder
    for i, msg in enumerate(messages):
        if msg.role == "system" and "CURRENT TASK PLAN" in msg.content:
            messages[i] = ChatMessage(role="system", content=plan_content)
            return

    # First time: insert right after the main system prompt
    insert_pos = 1 if messages and messages[0].role == "system" else 0
    messages.insert(insert_pos, ChatMessage(role="system", content=plan_content))


def _check_loop_detection(tool_history: list[tuple[str, str]]) -> str | None:
    """Detect if the agent is stuck in a loop.

    Returns a warning message if a loop is detected, or None if OK.
    Raises RuntimeError if circuit breaker threshold is reached.

    Args:
        tool_history: List of (tool_name, params_hash) tuples from recent calls.
    """
    if len(tool_history) < _LOOP_WARNING_THRESHOLD:
        return None

    # --- genericRepeat: same (name, hash) repeated N times ---
    from collections import Counter

    counts = Counter(tool_history[-_LOOP_HISTORY_SIZE:])
    for (tool_name, _), count in counts.most_common(3):
        if count >= _LOOP_CIRCUIT_BREAKER:
            logger.warning(
                "agent.loop.detected",
                extra={
                    "detector": "genericRepeat",
                    "repeated_tool": tool_name,
                    "count": count,
                    "action": "circuit_breaker",
                },
            )
            raise RuntimeError(
                f"Loop detected: {tool_name} called {count} times with same params. "
                "Aborting round to prevent infinite loop."
            )
        if count >= _LOOP_WARNING_THRESHOLD:
            logger.warning(
                "agent.loop.detected",
                extra={
                    "detector": "genericRepeat",
                    "repeated_tool": tool_name,
                    "count": count,
                    "action": "warning",
                },
            )
            return (
                f"⚠️ You have called `{tool_name}` {count} times with identical parameters. "
                "This looks like a loop. Try a different approach or skip this step."
            )

    # --- pingPong: A→B→A→B pattern ---
    recent = tool_history[-6:]
    if len(recent) >= 4:
        names = [t[0] for t in recent]
        # Check for alternating pattern: a,b,a,b
        if len(set(names[-4:])) == 2 and names[-4] == names[-2] and names[-3] == names[-1]:
            logger.warning(
                "agent.loop.detected",
                extra={
                    "detector": "pingPong",
                    "tools": f"{names[-2]}<->{names[-1]}",
                    "action": "warning",
                },
            )
            return (
                f"⚠️ Ping-pong detected: alternating between `{names[-2]}` and `{names[-1]}` "
                "without progress. Try a different approach."
            )

    return None


def _extract_tool_history(messages: list[ChatMessage]) -> list[tuple[str, str]]:
    """Extract (tool_name, params_hash) from recent assistant messages containing tool calls."""
    history: list[tuple[str, str]] = []
    for msg in messages:
        if msg.role != "assistant":
            continue
        # Tool calls are embedded in the message content as JSON by Ollama
        # We look for patterns like tool_name + params in the content
        content = msg.content
        if not content or len(content) < 5:
            continue
        # Create a rough hash of the content to detect repetition
        content_hash = hashlib.md5(content.encode()).hexdigest()[:8]
        # Use a simplified name — the first word or tool indicator
        name = content.split("(")[0].split(":")[0].strip()[:40] if content else "unknown"
        history.append((name, content_hash))
    return history[-_LOOP_HISTORY_SIZE:]


def _is_session_complete(session: AgentSession, last_reply: str) -> bool:
    """Heuristic: determine if the agent considers the session complete.

    Returns True when:
    - The structured plan has no remaining pending tasks, OR
    - The task plan has no remaining [ ] steps (all done), OR
    - The reply contains a completion signal and there's no task plan

    This is a soft check — max_iterations is always the hard safety net.
    """
    # Primary: structured plan exhausted
    if session.plan is not None:
        return session.plan.all_done()

    # Secondary: markdown task plan exhausted
    if session.task_plan is not None:
        pending = session.task_plan.count("[ ]")
        if pending == 0:
            logger.info(
                "Agent session %s: task plan complete (no pending steps)",
                session.session_id,
            )
            return True
        return False  # Still has work to do — don't check text signals

    # Fallback: no task plan yet, look for completion signals in the text
    completion_signals = [
        "completad",
        "terminad",
        "finaliz",
        "listo",
        "done",
        "finished",
        "accomplished",
        "all done",
        "todo completo",
    ]
    lower_reply = last_reply.lower()
    return any(sig in lower_reply for sig in completion_signals)


def _build_security_hitl_callback(
    session: AgentSession,
    wa_client: WhatsAppClient,
):
    """Build the HITL callback for security policy enforcement."""

    async def _security_hitl_callback(tool_name: str, arguments: dict, reason: str) -> bool:
        from app.agent.hitl import request_user_approval

        session.status = AgentStatus.WAITING_USER
        try:
            question = (
                f"⚠️ *Alerta de Seguridad*\n"
                f"El agente intenta ejecutar `{tool_name}`.\n"
                f"Argumentos: `{arguments}`\n\n"
                f"Motivo: *{reason}*\n\n"
                f"¿Autorizás la ejecución? (Aprobar/Rechazar)"
            )
            user_reply = await request_user_approval(session.phone_number, question, wa_client)
            return user_reply.lower().strip() in [
                "aprobar",
                "sí",
                "si",
                "yes",
                "y",
                "ok",
                "dale",
                "mandale",
                "autorizo",
            ]
        finally:
            session.status = AgentStatus.RUNNING

    return _security_hitl_callback


async def _run_planner_session(
    session: AgentSession,
    ollama_client: OllamaClient,
    session_registry: SkillRegistry,
    wa_client: WhatsAppClient,
    mcp_manager: McpManager | None,
    hitl_callback,
) -> str:
    """Run the 3-phase planner-orchestrator loop.

    Phase 1 — UNDERSTAND: Planner creates structured plan
    Phase 2 — EXECUTE: Workers run each task step sequentially
    Phase 3 — SYNTHESIZE: Planner reviews results, may replan

    Returns the final reply text.
    """
    # --- Phase 1: UNDERSTAND — Create plan ---
    logger.info("Agent session %s: Phase 1 — UNDERSTAND (creating plan)", session.session_id)
    trace = get_current_trace()
    if trace:
        async with trace.span("planner:create_plan", kind="span") as span:
            span.set_input({"objective": session.objective})
            plan = await create_plan(session.objective, ollama_client)
            span.set_output({"tasks": len(plan.tasks), "plan_preview": plan.to_markdown()[:500]})
    else:
        plan = await create_plan(session.objective, ollama_client)
    session.plan = plan
    session.task_plan = plan.to_markdown()

    # --- Phase 1.5: REVIEW — Let user review plan before execution ---
    from app.agent.hitl import request_user_approval
    from app.agent.planner import replan_with_feedback

    approval = await request_user_approval(
        session.phone_number,
        f"📋 *Plan de ejecución ({len(plan.tasks)} pasos):*\n{plan.to_markdown()}\n\n"
        "¿Procedo? (sí / modificar / cancelar)",
        wa_client,
        timeout=300,  # 5 min for plan review
    )

    lower = approval.lower().strip()
    if lower in ("cancelar", "no", "cancel"):
        session.status = AgentStatus.COMPLETED
        return "❌ Sesión cancelada por el usuario."
    elif lower.startswith("timeout"):
        session.status = AgentStatus.COMPLETED
        return "⏰ Timeout esperando aprobación del plan. Sesión cancelada."
    elif lower not in ("sí", "si", "dale", "ok", "yes", "go", "procede", "adelante"):
        # Treat as modification request
        plan = await replan_with_feedback(
            session.objective, plan, approval, ollama_client,
        )
        session.plan = plan
        session.task_plan = plan.to_markdown()
        try:
            await wa_client.send_message(
                session.phone_number,
                f"📋 *Plan actualizado:*\n{plan.to_markdown()}\n\n▶️ Ejecutando...",
            )
        except Exception:
            pass
    else:
        try:
            await wa_client.send_message(session.phone_number, "▶️ Ejecutando plan...")
        except Exception:
            pass

    # --- Phase 2 + 3: EXECUTE + SYNTHESIZE loop ---
    max_cycles = session.max_iterations
    for cycle in range(max_cycles):
        session.iteration = cycle

        # Execute ready tasks — run in parallel when multiple tasks have deps met
        ready = plan.ready_tasks()
        while ready:
            for t in ready:
                t.status = "in_progress"
            session.task_plan = plan.to_markdown()

            async def _execute_one(task, _plan=plan):
                """Execute a single worker task with tracing."""
                logger.info(
                    "Agent session %s: executing task #%s [%s]: %s",
                    session.session_id,
                    task.id,
                    task.worker_type,
                    task.description[:80],
                )
                trace = get_current_trace()
                if trace:
                    async with trace.span(f"worker:task_{task.id}", kind="span") as worker_span:
                        worker_span.set_input(
                            {
                                "description": task.description,
                                "worker_type": task.worker_type,
                            }
                        )
                        try:
                            result = await execute_worker(
                                task=task,
                                objective=_plan.objective,
                                ollama_client=ollama_client,
                                skill_registry=session_registry,
                                mcp_manager=mcp_manager,
                                max_tools=_TOOLS_PER_ROUND,
                                hitl_callback=hitl_callback,
                                parent_span_id=worker_span.span_id,
                                plan=_plan,
                            )
                            task.result = result
                            task.status = "failed" if "(no data found" in result else "done"
                            worker_span.set_output({"result": result[:500], "status": task.status})
                        except Exception as e:
                            logger.exception("Worker task #%s failed", task.id)
                            task.status = "failed"
                            task.result = f"Error: {e}"
                            worker_span._status = "failed"
                            worker_span.set_output({"error": str(e), "status": task.status})
                else:
                    try:
                        result = await execute_worker(
                            task=task,
                            objective=_plan.objective,
                            ollama_client=ollama_client,
                            skill_registry=session_registry,
                            mcp_manager=mcp_manager,
                            max_tools=_TOOLS_PER_ROUND,
                            hitl_callback=hitl_callback,
                            plan=_plan,
                        )
                        task.result = result
                        task.status = "failed" if "(no data found" in result else "done"
                    except Exception as e:
                        logger.exception("Worker task #%s failed", task.id)
                        task.status = "failed"
                        task.result = f"Error: {e}"

            if len(ready) > 1:
                logger.info(
                    "Agent session %s: running %d tasks in parallel: %s",
                    session.session_id,
                    len(ready),
                    [t.id for t in ready],
                )
                await asyncio.gather(*[_execute_one(t) for t in ready])
            else:
                await _execute_one(ready[0])

            session.task_plan = plan.to_markdown()

            # Persist after each batch
            for task in ready:
                try:
                    round_data = {
                        "iteration": cycle + 1,
                        "task_id": task.id,
                        "task_status": task.status,
                        "task_plan": session.task_plan,
                        "reply": task.result or "",
                    }
                    append_to_session(session.phone_number, session.session_id, round_data)
                except Exception as e:
                    logger.error("Error saving session round: %s", e)

            # Progress update
            done_count = plan.success_count()
            completed_ids = [t.id for t in ready if t.status == "done"]
            failed_ids = [t.id for t in ready if t.status == "failed"]
            status_parts = []
            if completed_ids:
                status_parts.append(f"✅ #{','.join(str(i) for i in completed_ids)} done")
            if failed_ids:
                status_parts.append(f"❌ #{','.join(str(i) for i in failed_ids)} failed")
            try:
                await wa_client.send_message(
                    session.phone_number,
                    f"{' | '.join(status_parts)} ({done_count}/{len(plan.tasks)})",
                )
            except Exception:
                pass

            ready = plan.ready_tasks()

        # All tasks executed (or failed) — check if we should replan
        if plan.all_done() and not plan.has_failures():
            break  # All tasks succeeded, no need to replan

        if not plan.all_done():
            # Some tasks still pending (shouldn't normally happen), continue
            continue

        # Tasks finished but some failed — fall through to replan
        logger.info(
            "Agent session %s: %d/%d tasks failed, attempting replan",
            session.session_id,
            sum(1 for t in plan.tasks if t.status == "failed"),
            len(plan.tasks),
        )

        # --- Phase 3: SYNTHESIZE / REPLAN ---
        logger.info(
            "Agent session %s: Phase 3 — SYNTHESIZE (reviewing results)", session.session_id
        )
        trace = get_current_trace()
        if trace:
            async with trace.span("planner:replan", kind="span") as span:
                span.set_input(
                    {
                        "replans": plan.replans,
                        "tasks_done": sum(1 for t in plan.tasks if t.status == "done"),
                    }
                )
                new_plan = await replan(plan, ollama_client)
                span.set_output(
                    {
                        "action": "replan" if new_plan else "done_or_continue",
                        "new_tasks": len(new_plan.tasks) if new_plan else 0,
                    }
                )
        else:
            new_plan = await replan(plan, ollama_client)
        if new_plan is None:
            # Planner says done or continue (but no more pending tasks)
            break
        # Apply the new plan
        plan = new_plan
        session.plan = plan
        session.task_plan = plan.to_markdown()
        try:
            await wa_client.send_message(
                session.phone_number,
                f"🔄 Re-planned: {len(plan.tasks)} new steps\n{plan.to_markdown()}",
            )
        except Exception:
            pass

    # --- Final synthesis ---
    trace = get_current_trace()
    if trace:
        async with trace.span("planner:synthesize", kind="span") as span:
            span.set_input(
                {
                    "tasks_done": sum(1 for t in plan.tasks if t.status == "done"),
                    "tasks_failed": sum(1 for t in plan.tasks if t.status == "failed"),
                }
            )
            reply = await synthesize(plan, ollama_client)
            span.set_output({"reply_preview": reply[:300]})
    else:
        reply = await synthesize(plan, ollama_client)
    return reply


async def _run_reactive_session(
    session: AgentSession,
    ollama_client: OllamaClient,
    session_registry: SkillRegistry,
    wa_client: WhatsAppClient,
    mcp_manager: McpManager | None,
    hitl_callback,
    messages: list[ChatMessage],
    pre_classified_categories: list[str] | None = None,
) -> str:
    """Run the legacy reactive agent loop (fallback).

    Used when the planner-orchestrator is not applicable or as a fallback.
    """
    reply = ""
    for iteration in range(session.max_iterations):
        session.iteration = iteration
        logger.info(
            "Agent session %s — round %d/%d",
            session.session_id,
            iteration + 1,
            session.max_iterations,
        )

        # Re-inject task plan before each round so the agent stays oriented.
        if session.task_plan:
            _inject_task_plan(messages, session.task_plan)

        # Inject scratchpad as system message (if non-empty from a previous round)
        if session.scratchpad:
            _inject_scratchpad(messages, session.scratchpad)

        # Token budget tracking (best-effort, no latency impact)
        try:
            from app.context.token_estimator import log_context_budget

            log_context_budget(messages, extra={"agent_round": iteration + 1})
        except Exception:
            pass

        # Run one round of tool execution
        trace = get_current_trace()
        if trace:
            async with trace.span(f"reactive:round_{iteration + 1}", kind="span") as round_span:
                from app.skills.executor import _serialize_messages_for_trace

                round_span.set_input(
                    {
                        "iteration": iteration + 1,
                        "message_count": len(messages),
                        **_serialize_messages_for_trace(messages),
                    }
                )
                reply = await execute_tool_loop(
                    messages=messages,
                    ollama_client=ollama_client,
                    skill_registry=session_registry,
                    mcp_manager=mcp_manager,
                    max_tools=_TOOLS_PER_ROUND,
                    hitl_callback=hitl_callback,
                    parent_span_id=round_span.span_id,
                    pre_classified_categories=pre_classified_categories,
                )
                round_span.set_output({"reply_preview": reply[:200]})
        else:
            reply = await execute_tool_loop(
                messages=messages,
                ollama_client=ollama_client,
                skill_registry=session_registry,
                mcp_manager=mcp_manager,
                max_tools=_TOOLS_PER_ROUND,
                hitl_callback=hitl_callback,
                pre_classified_categories=pre_classified_categories,
            )

        # Extract and persist scratchpad before appending the clean reply
        new_scratchpad, clean_reply = _extract_scratchpad(reply)
        if new_scratchpad:
            session.scratchpad = new_scratchpad
            logger.debug(
                "Agent session %s: scratchpad updated (%d chars)",
                session.session_id,
                len(new_scratchpad),
            )
        reply = clean_reply

        messages.append(ChatMessage(role="assistant", content=reply))

        if _is_session_complete(session, reply):
            logger.info(
                "Agent session %s: detected completion at round %d",
                session.session_id,
                iteration + 1,
            )
            break

        # Loop detection
        tool_history = _extract_tool_history(messages)
        try:
            loop_warning = _check_loop_detection(tool_history)
            if loop_warning:
                messages.append(ChatMessage(role="system", content=loop_warning))
        except RuntimeError as e:
            logger.error("Agent session %s: circuit breaker — %s", session.session_id, e)
            messages.append(ChatMessage(role="system", content=str(e)))
            break

        # Progress update via WhatsApp
        if session.task_plan:
            done = session.task_plan.count("[x]")
            total = done + session.task_plan.count("[ ]")
            try:
                await wa_client.send_message(
                    session.phone_number,
                    f"🔧 Round {iteration + 1}: {done}/{total} steps done",
                )
            except Exception:
                pass

        # Session Persistence
        try:
            round_data = {
                "iteration": iteration + 1,
                "task_plan": session.task_plan,
                "reply": reply,
                "messages": [
                    m.model_dump() if hasattr(m, "model_dump") else m.dict() for m in messages[-4:]
                ],
            }
            append_to_session(session.phone_number, session.session_id, round_data)
        except Exception as e:
            logger.error("Error saving session round: %s", e)

        _clear_old_tool_results(messages, keep_last_n=2)

    return reply


async def run_agent_session(
    session: AgentSession,
    ollama_client: OllamaClient,
    skill_registry: SkillRegistry,
    wa_client: WhatsAppClient,
    mcp_manager: McpManager | None = None,
    use_planner: bool = True,
    recorder=None,
    repository=None,
    pre_classified_categories: list[str] | None = None,
) -> None:
    """Run a full agentic session in the background.

    Planner-orchestrator architecture (default):
      Phase 1 — UNDERSTAND: Planner creates structured plan
      Phase 2 — EXECUTE: Workers run each task step
      Phase 3 — SYNTHESIZE: Planner reviews, replans if needed

    Falls back to the reactive loop if the planner fails or use_planner=False.
    Proactively sends the result to the user via WhatsApp when done.
    """
    _active_sessions[session.phone_number] = session
    current_task = asyncio.current_task()
    if current_task is not None:
        _active_tasks[session.phone_number] = current_task
    logger.info(
        "Agent session %s started for %s: %s",
        session.session_id,
        session.phone_number,
        session.objective[:80],
    )

    if recorder is not None:
        async with TraceContext(
            phone_number=session.phone_number,
            input_text=session.objective,
            recorder=recorder,
            message_type="agent",
        ):
            # TraceContext sets contextvars — inner code uses get_current_trace() to add spans.
            # session_id is embedded in the spans via planner/worker span names.
            await _run_agent_body(
                session=session,
                ollama_client=ollama_client,
                skill_registry=skill_registry,
                wa_client=wa_client,
                mcp_manager=mcp_manager,
                use_planner=use_planner,
                repository=repository,
                pre_classified_categories=pre_classified_categories,
            )
    else:
        await _run_agent_body(
            session=session,
            ollama_client=ollama_client,
            skill_registry=skill_registry,
            wa_client=wa_client,
            mcp_manager=mcp_manager,
            use_planner=use_planner,
            repository=repository,
            pre_classified_categories=pre_classified_categories,
        )


async def _score_goal_completion(
    initial_message: str,
    final_output: str,
    ollama_client: OllamaClient,
    trace_ctx: TraceContext,
) -> None:
    """LLM-as-judge: did the agent complete the user's goal? Best-effort background task."""
    try:
        prompt = (
            f"User request: {initial_message[:300]}\n"
            f"Agent final response: {final_output[:400]}\n\n"
            "Did the agent's response successfully address the user's request? "
            "Reply ONLY 'yes' or 'no'."
        )
        response = await ollama_client.chat(
            messages=[ChatMessage(role="user", content=prompt)],
            think=False,
        )
        verdict = (response or "").strip().lower()
        score = 1.0 if verdict.startswith("yes") else 0.0
        await trace_ctx.add_score(
            name="goal_completion",
            value=score,
            source="system",
            comment=f"LLM-as-judge: {verdict[:20]}",
        )
    except Exception:
        logger.debug("goal_completion scoring failed (best-effort)", exc_info=True)


async def _save_task_results_note(session: AgentSession, repository: object) -> None:
    """Save combined worker results as a note for later retrieval."""
    plan = session.plan
    if not plan or not plan.tasks:
        return

    done_tasks = [t for t in plan.tasks if t.status == "done" and t.result]
    if not done_tasks:
        return

    sections = []
    for t in done_tasks:
        sections.append(f"## Task #{t.id}: {t.description}\n\n{t.result}")

    content = "\n\n---\n\n".join(sections)
    # Cap at reasonable size for a note
    if len(content) > 8000:
        content = content[:8000] + "\n\n...(truncated)"

    title = f"[Agent] {plan.objective[:80]}"
    await repository.save_note(title, content)  # type: ignore[attr-defined]
    logger.info(
        "Saved agent results as note: %s (%d tasks, %d chars)", title, len(done_tasks), len(content)
    )


async def _run_agent_body(
    session: AgentSession,
    ollama_client: OllamaClient,
    skill_registry: SkillRegistry,
    wa_client: WhatsAppClient,
    mcp_manager: McpManager | None,
    use_planner: bool,
    repository=None,
    pre_classified_categories: list[str] | None = None,
) -> None:
    """Inner implementation of run_agent_session. Run inside a TraceContext if tracing enabled."""
    try:
        # Build a session-scoped registry with HITL + task-memory tools.
        session_registry = _register_session_tools(session, skill_registry, wa_client)
        hitl_callback = _build_security_hitl_callback(session, wa_client)

        if use_planner:
            try:
                reply = await _run_planner_session(
                    session=session,
                    ollama_client=ollama_client,
                    session_registry=session_registry,
                    wa_client=wa_client,
                    mcp_manager=mcp_manager,
                    hitl_callback=hitl_callback,
                )
                # Auto-save detailed worker results as a note so the user
                # can reference them later (the synthesis is a short summary;
                # the full data lives only in the JSONL otherwise).
                if repository is not None and session.plan:
                    try:
                        await _save_task_results_note(session, repository)
                    except Exception:
                        logger.debug("Could not save agent results note", exc_info=True)
            except Exception as plan_err:
                logger.warning(
                    "Planner session failed (%s), retrying once before fallback", plan_err
                )
                try:
                    # Reset plan state before retry
                    session.plan = None
                    session.task_plan = None
                    reply = await _run_planner_session(
                        session=session,
                        ollama_client=ollama_client,
                        session_registry=session_registry,
                        wa_client=wa_client,
                        mcp_manager=mcp_manager,
                        hitl_callback=hitl_callback,
                    )
                    if repository is not None and session.plan:
                        try:
                            await _save_task_results_note(session, repository)
                        except Exception:
                            logger.debug("Could not save agent results note", exc_info=True)
                except Exception:
                    logger.exception("Planner retry failed, falling back to reactive loop")
                    # Notify user about quality downgrade
                    try:
                        await wa_client.send_message(
                            session.phone_number,
                            "⚠️ No pude crear un plan estructurado. Continuando en modo reactivo "
                            "(menos eficiente pero funcional).",
                        )
                    except Exception:
                        pass
                    # Track downgrade in tracing
                    _trace = get_current_trace()
                    if _trace:
                        try:
                            await _trace.add_score(
                                name="planner_downgrade",
                                value=1.0,
                                source="system",
                                comment="Fallback to reactive after planner failure (retry exhausted)",
                            )
                        except Exception:
                            pass
                    # Fallback to reactive loop
                    system_content = _AGENT_SYSTEM_PROMPT.format(
                        objective=session.objective,
                        workspace_info=_get_workspace_info_block(),
                    )
                    system_content = _load_bootstrap_context(system_content)
                    messages: list[ChatMessage] = [
                        ChatMessage(role="system", content=system_content),
                        ChatMessage(role="user", content=session.objective),
                    ]
                    reply = await _run_reactive_session(
                        session=session,
                        ollama_client=ollama_client,
                        session_registry=session_registry,
                        wa_client=wa_client,
                        mcp_manager=mcp_manager,
                        hitl_callback=hitl_callback,
                        messages=messages,
                        pre_classified_categories=pre_classified_categories,
                    )
        else:
            system_content = _AGENT_SYSTEM_PROMPT.format(
                        objective=session.objective,
                        workspace_info=_get_workspace_info_block(),
                    )
            system_content = _load_bootstrap_context(system_content)
            messages = [
                ChatMessage(role="system", content=system_content),
                ChatMessage(role="user", content=session.objective),
            ]
            reply = await _run_reactive_session(
                session=session,
                ollama_client=ollama_client,
                session_registry=session_registry,
                wa_client=wa_client,
                mcp_manager=mcp_manager,
                hitl_callback=hitl_callback,
                messages=messages,
                pre_classified_categories=pre_classified_categories,
            )

        # --- Session ended ---
        session.status = AgentStatus.COMPLETED
        logger.info(
            "Agent session %s completed after %d round(s)",
            session.session_id,
            session.iteration + 1,
        )

        # Final plan summary
        final_message = reply
        if session.plan:
            done = sum(1 for t in session.plan.tasks if t.status == "done")
            failed = sum(1 for t in session.plan.tasks if t.status == "failed")
            total = len(session.plan.tasks)
            plan_status = f"_Plan: {done}/{total} completed"
            if failed:
                plan_status += f", {failed} failed"
            plan_status += "._\n\n"
            final_message = plan_status + reply
        elif session.task_plan:
            done = session.task_plan.count("[x]")
            pending = session.task_plan.count("[ ]")
            plan_status = f"_Plan: {done} pasos completados, {pending} pendientes._\n\n"
            final_message = plan_status + reply

        from app.formatting.whatsapp import markdown_to_whatsapp

        await wa_client.send_message(
            session.phone_number,
            markdown_to_whatsapp(f"✅ *Sesión agéntica completada*\n\n{final_message}"),
        )

        # Bridge agent result into conversation history so the user can reference it later
        if repository is not None:
            try:
                conv_id = await repository.get_or_create_conversation(session.phone_number)
                await repository.save_message(conv_id, "assistant", reply[:4000])
            except Exception:
                logger.debug("Could not bridge agent result to conversation history")

        # Goal completion scoring — LLM-as-judge, run before TraceContext exits so the
        # score is written while the trace is still active. Guard against CancelledError
        # so a cancellation during the LLM judge call does not trigger the outer
        # CancelledError handler (which would send a spurious "session cancelled" message
        # after the completion message was already delivered).
        _trace = get_current_trace()
        if _trace:
            try:
                await _score_goal_completion(session.objective, reply, ollama_client, _trace)
            except asyncio.CancelledError:
                logger.debug("goal_completion scoring cancelled (session already completed)")

    except asyncio.CancelledError:
        session.status = AgentStatus.CANCELLED
        logger.info("Agent session %s cancelled", session.session_id)
        await wa_client.send_message(
            session.phone_number,
            "🛑 Sesión agéntica cancelada.",
        )
    except Exception:
        session.status = AgentStatus.FAILED
        logger.exception("Agent session %s failed", session.session_id)
        await wa_client.send_message(
            session.phone_number,
            "❌ La sesión agéntica falló inesperadamente. Usa /debug para investigar.",
        )
    finally:
        _active_sessions.pop(session.phone_number, None)
        _active_tasks.pop(session.phone_number, None)


def _load_bootstrap_context(system_content: str) -> str:
    """Append optional bootstrap files (SOUL.md, USER.md, TOOLS.md) to system content."""
    bootstrap_files = ["SOUL.md", "USER.md", "TOOLS.md"]
    for bs_file in bootstrap_files:
        bs_path = _PROJECT_ROOT / bs_file
        if bs_path.exists():
            try:
                content = bs_path.read_text(encoding="utf-8")
                system_content += f"\n\n--- {bs_file} ---\n{content}\n"
            except Exception as e:
                logger.warning("Could not read bootstrap file %s: %s", bs_file, e)
    return system_content


def get_active_session(phone_number: str) -> AgentSession | None:
    """Return the active agent session for this user, or None."""
    return _active_sessions.get(phone_number)


def cancel_session(phone_number: str) -> bool:
    """Cancel the active agent session for this phone number.

    Cancels the underlying asyncio.Task so the loop actually stops,
    not just setting a status flag. Also handles WAITING_USER state.
    Returns True if a session was found and cancel was requested.
    """
    session = _active_sessions.get(phone_number)
    cancellable = {AgentStatus.RUNNING, AgentStatus.WAITING_USER}
    if session and session.status in cancellable:
        session.status = AgentStatus.CANCELLED
        task = _active_tasks.get(phone_number)
        if task and not task.done():
            task.cancel()
        return True
    return False


def create_session(
    phone_number: str,
    objective: str,
    max_iterations: int = 15,
) -> AgentSession:
    """Create a new AgentSession with a fresh random session ID."""
    return AgentSession(
        session_id=uuid.uuid4().hex,
        phone_number=phone_number,
        objective=objective,
        max_iterations=max_iterations,
    )
