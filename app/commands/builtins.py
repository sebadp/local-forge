from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from app.commands.context import CommandContext
from app.commands.registry import CommandRegistry, CommandSpec
from app.eval.prompt_manager import activate_with_eval
from app.models import ChatMessage

logger = logging.getLogger(__name__)

# Keep references to background agent tasks to prevent GC mid-execution
_bg_agent_tasks: set[asyncio.Task] = set()


async def cmd_cancel(args: str, context: CommandContext) -> str:
    """Cancel the active agent session for this user."""
    from app.agent.loop import cancel_session, get_active_session

    session = get_active_session(context.phone_number)
    if not session:
        return "No hay ninguna sesión agéntica activa para cancelar."
    cancelled = cancel_session(context.phone_number)
    if cancelled:
        return f"🛑 Sesión agéntica cancelada.\n_Objetivo interrumpido:_ {session.objective[:100]}"
    return "La sesión ya terminó o no se pudo cancelar."


async def cmd_agent(args: str, context: CommandContext) -> str:
    """Show the status of the current agent session, or start a new one if args provided."""
    import asyncio

    from app.agent.loop import create_session, get_active_session, run_agent_session

    session = get_active_session(context.phone_number)

    # If no args, just show status
    if not args.strip():
        if not session:
            return (
                "No hay ninguna sesión agéntica activa. Usa `/agent <objetivo>` para iniciar una."
            )
        plan_preview = ""
        if session.task_plan:
            lines = session.task_plan.split("\n")[:8]
            plan_preview = "\n".join(lines)
            plan_preview = f"\n\n*Plan actual:*\n{plan_preview}"
        return (
            f"🤖 *Sesión agéntica activa*\n"
            f"Estado: {session.status.value}\n"
            f"Objetivo: {session.objective[:120]}"
            f"{plan_preview}"
        )

    # Start a new session
    if session:
        return "Ya hay una sesión activa. Usa /cancel antes de iniciar una nueva o /agent para ver su estado."

    objective = args.strip()
    new_session = create_session(context.phone_number, objective)

    # Run the agent loop in the background — save reference to prevent GC mid-execution
    task = asyncio.create_task(
        run_agent_session(
            session=new_session,
            ollama_client=context.ollama_client,
            skill_registry=context.skill_registry,
            wa_client=context.wa_client,
            mcp_manager=context.mcp_manager,
            recorder=context.trace_recorder,
            repository=context.repository,
        )
    )
    _bg_agent_tasks.add(task)
    task.add_done_callback(_bg_agent_tasks.discard)

    return (
        f"🚀 *Sesión agéntica iniciada*\n_Objetivo:_ {objective}\n\nTe iré informando mi progreso."
    )


async def cmd_agent_resume(args: str, context: CommandContext) -> str:
    """Resume the most recent agent session from disk."""
    import asyncio

    from app.agent.loop import AgentSession, get_active_session, run_agent_session
    from app.agent.persistence import get_latest_session_id, load_session_history

    session = get_active_session(context.phone_number)
    if session:
        return "Ya hay una sesión activa en memoria. Usa /agent para ver su estado."

    session_id = get_latest_session_id(context.phone_number)
    if not session_id:
        return "No encontré ninguna sesión reciente en disco para retomar."

    history = load_session_history(context.phone_number, session_id)
    if not history:
        return f"Encontré la sesión {session_id} pero no tiene historial guardado."

    # Reconstruct state from the last saved round
    last_round = history[-1]

    # We don't have the original objective saved in the JSONL directly,
    # but we can extract it or use a default. Ideally, the resume logic
    # just picks up the task plan.
    reconstructed_session = AgentSession(
        session_id=session_id,
        phone_number=context.phone_number,
        objective="[Retomado] " + (last_round.get("reply", "")[:50] + "..."),
        max_iterations=15,
    )
    reconstructed_session.task_plan = last_round.get("task_plan")
    reconstructed_session.iteration = last_round.get("iteration", 0)

    # Since we can't easily reconstruct `messages: list[ChatMessage]` with exact fidelity
    # just from the summary dict without importing internal models here,
    # the agent loop will rely on its task plan injection to reorient itself.

    task = asyncio.create_task(
        run_agent_session(
            session=reconstructed_session,
            ollama_client=context.ollama_client,
            skill_registry=context.skill_registry,
            wa_client=context.wa_client,
            mcp_manager=context.mcp_manager,
            recorder=context.trace_recorder,
            repository=context.repository,
        )
    )
    _bg_agent_tasks.add(task)
    task.add_done_callback(_bg_agent_tasks.discard)

    plan_preview = (
        reconstructed_session.task_plan.split("\n")[0]
        if reconstructed_session.task_plan
        else "Sin plan previo."
    )
    return f"♻️ *Sesión agéntica {session_id[:8]} retomada*\n_Ronda {reconstructed_session.iteration}_\n_Plan:_ {plan_preview}"


async def cmd_remember(args: str, context: CommandContext) -> str:
    if not args.strip():
        return "Usage: /remember <something to remember>"
    memory_id = await context.repository.add_memory(args.strip())
    memories = await context.repository.list_memories()
    await context.memory_file.sync(memories)

    # Embed the new memory (best-effort)
    if context.ollama_client and context.embed_model:
        from app.embeddings.indexer import embed_memory

        await embed_memory(
            memory_id,
            args.strip(),
            context.repository,
            context.ollama_client,
            context.embed_model,
        )

    return f"Remembered: {args.strip()}"


async def cmd_forget(args: str, context: CommandContext) -> str:
    if not args.strip():
        return "Usage: /forget <something to forget>"
    memory_id = await context.repository.remove_memory_return_id(args.strip())
    if memory_id is None:
        return f"No active memory found matching: {args.strip()}"
    memories = await context.repository.list_memories()
    await context.memory_file.sync(memories)

    # Remove embedding (best-effort)
    if context.embed_model:
        from app.embeddings.indexer import remove_memory_embedding

        await remove_memory_embedding(memory_id, context.repository)

    return f"Forgot: {args.strip()}"


async def cmd_memories(args: str, context: CommandContext) -> str:
    memories = await context.repository.list_memories()
    if not memories:
        return "No memories saved yet. Use /remember <info> to save something."
    lines = ["*Memories:*"]
    for m in memories:
        if m.category:
            lines.append(f"- [{m.category}] {m.content}")
        else:
            lines.append(f"- {m.content}")
    return "\n".join(lines)


async def cmd_clear(args: str, context: CommandContext) -> str:
    conv_id = await context.repository.get_or_create_conversation(context.phone_number)

    # Save snapshot before clearing (if we have the required dependencies)
    if context.ollama_client and context.daily_log:
        try:
            await _save_session_snapshot(conv_id, context)
        except Exception:
            logger.exception("Failed to save session snapshot")

    await context.repository.clear_conversation(conv_id)
    return "Conversation history cleared."


async def _save_session_snapshot(conv_id: int, context: CommandContext) -> None:
    """Save last messages as a snapshot before clearing."""
    messages = await context.repository.get_recent_messages(conv_id, 15)
    # Filter to user and assistant only
    messages = [m for m in messages if m.role in ("user", "assistant")]
    if not messages:
        return

    # Generate slug via LLM
    conversation_preview = "\n".join(f"{m.role}: {m.content[:100]}" for m in messages[:5])
    slug_prompt = (
        "Name this conversation in 3-5 words. Use lowercase and hyphens.\n"
        "Only output the name, nothing else.\n\n"
        f"{conversation_preview}"
    )
    try:
        slug = await context.ollama_client.chat_with_tools(
            [ChatMessage(role="user", content=slug_prompt)],
            think=False,
        )
        slug = slug.content.strip().lower().replace(" ", "-")
        # Sanitize: keep only alphanumeric and hyphens
        slug = "".join(c for c in slug if c.isalnum() or c == "-").strip("-")
        if not slug:
            raise ValueError("Empty slug")
    except Exception:
        slug = datetime.now(UTC).strftime("%H%M%S")

    # Format snapshot content
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    lines = [f"# {slug}", f"## {date_str}", ""]
    for m in messages:
        label = "User" if m.role == "user" else "Assistant"
        lines.append(f"**{label}**: {m.content}")
        lines.append("")
    content = "\n".join(lines)

    path = await context.daily_log.save_snapshot(slug, content)

    # Also log a summary entry in today's daily log
    topic = slug.replace("-", " ")
    await context.daily_log.append(f"Session cleared: {topic} ({len(messages)} messages saved)")
    logger.info("Saved session snapshot: %s", path.name)


async def cmd_setup(args: str, context: CommandContext) -> str:
    await context.repository.reset_user_profile(context.phone_number)
    return (
        "Profile reset! Send me any message to start the guided setup.\n\n"
        "Perfil reiniciado. Envíame cualquier mensaje para comenzar la configuración guiada."
    )


async def cmd_review_skill(args: str, context: CommandContext) -> str:
    name = args.strip()

    # No args → list all skills and MCP servers
    if not name:
        lines = []

        if context.skill_registry:
            skills = context.skill_registry.list_skills()
            if skills:
                lines.append("*Skills:*")
                for s in skills:
                    tool_count = len(context.skill_registry.get_tools_for_skill(s.name))
                    lines.append(
                        f"- 🔧 {s.name} v{s.version} ({tool_count} tools) — {s.description}"
                    )

        if context.mcp_manager:
            servers = context.mcp_manager.list_servers()
            if servers:
                if lines:
                    lines.append("")
                lines.append("*MCP Servers:*")
                for s in servers:
                    icon = "🟢" if s["status"] == "connected" else "🔴"
                    desc = f" — {s['description']}" if s.get("description") else ""
                    lines.append(f"- {icon} {s['name']} ({s['status']}, {s['tools']} tools){desc}")

        if not lines:
            return "No skills or MCP servers installed."

        lines.append("\nUse /review-skill <name> for details.")
        return "\n".join(lines)

    # Check if it's a skill
    if context.skill_registry:
        skill = context.skill_registry.get_skill(name)
        if skill:
            lines = [
                f"*Skill: {skill.name}*",
                f"Version: {skill.version}",
                f"Description: {skill.description}",
                "",
                "*Tools:*",
            ]
            registered_tools = context.skill_registry.get_tools_for_skill(skill.name)
            registered_names = {t.name for t in registered_tools}
            for tool_name in skill.tools:
                status = "✓" if tool_name in registered_names else "✗"
                tool = next((t for t in registered_tools if t.name == tool_name), None)
                desc = f" — {tool.description}" if tool else ""
                lines.append(f"  {status} {tool_name}{desc}")
            # Tools registered but not in SKILL.md
            extra = registered_names - set(skill.tools)
            for tool_name in sorted(extra):
                tool = next(t for t in registered_tools if t.name == tool_name)
                lines.append(f"  ✓ {tool_name} — {tool.description}")

            if skill.instructions:
                lines.append("")
                lines.append("*Instructions:*")
                lines.append(skill.instructions)

            return "\n".join(lines)

    # Check if it's an MCP server
    if context.mcp_manager:
        servers = context.mcp_manager.list_servers()
        match = next((s for s in servers if s["name"] == name), None)
        if match:
            cfg = context.mcp_manager._server_configs.get(name, {})
            server_type = cfg.get("type", "stdio")
            lines = [
                f"*MCP Server: {match['name']}*",
                f"Status: {match['status']}",
                f"Type: {server_type}",
            ]
            if match.get("description"):
                lines.append(f"Description: {match['description']}")

            # List tools from this server
            tools = context.mcp_manager.get_tools()
            server_tools = [t for t in tools.values() if t.skill_name == f"mcp::{name}"]
            if server_tools:
                lines.append(f"\n*Tools ({len(server_tools)}):*")
                for t in server_tools:
                    lines.append(f"  - {t.name}: {t.description}")
            else:
                lines.append("\nNo tools available.")

            return "\n".join(lines)

    return f"No skill or MCP server found with name '{name}'."


async def cmd_feedback(args: str, context: CommandContext) -> str:
    """Tag the last interaction with human feedback (free text)."""
    if not args.strip():
        return "Uso: /feedback <comentario>\nEjemplo: /feedback La respuesta estuvo bien pero faltó más detalle"

    trace_id = await context.repository.get_latest_trace_id(context.phone_number)
    if not trace_id:
        return "No encontré una interacción reciente para evaluar."

    # Analyze sentiment to assign a numeric score
    sentiment_value = 0.5  # default: neutral
    if context.ollama_client:
        try:
            result = await context.ollama_client.chat(
                [
                    ChatMessage(
                        role="user",
                        content=(
                            "Rate the sentiment of this feedback about an AI response on a scale of 0.0 to 1.0. "
                            "0.0=very negative, 0.5=neutral, 1.0=very positive. "
                            "Reply ONLY with the number, nothing else.\n\n"
                            f"Feedback: {args.strip()}"
                        ),
                    )
                ]
            )
            sentiment_value = max(0.0, min(1.0, float(result.strip())))
        except (ValueError, Exception):
            pass  # keep default 0.5

    from app.tracing.recorder import TraceRecorder

    recorder = TraceRecorder(context.repository)
    await recorder.add_score(
        trace_id=trace_id,
        name="human_feedback",
        value=sentiment_value,
        source="human",
        comment=args.strip(),
    )
    return "Gracias por el feedback. Lo voy a tener en cuenta para mejorar."


async def cmd_rate(args: str, context: CommandContext) -> str:
    """Rate the last response on a 1-5 scale."""
    try:
        score = int(args.strip())
        if not 1 <= score <= 5:
            raise ValueError
    except ValueError:
        return "Uso: /rate <1-5>\nEjemplo: /rate 4"

    trace_id = await context.repository.get_latest_trace_id(context.phone_number)
    if not trace_id:
        return "No encontré una interacción reciente para evaluar."

    from app.tracing.recorder import TraceRecorder

    recorder = TraceRecorder(context.repository)
    await recorder.add_score(
        trace_id=trace_id,
        name="human_rating",
        value=score / 5.0,
        source="human",
        comment=f"{score}/5",
    )
    return f"Calificación {score}/5 registrada. ¡Gracias!"


async def cmd_approve_prompt(args: str, context: CommandContext) -> str:
    """Activate a proposed prompt version, optionally running eval first."""
    parts = args.strip().split()
    if len(parts) != 2:
        return "Uso: /approve-prompt <nombre> <versión>\nEjemplo: /approve-prompt system_prompt 3"

    prompt_name, version_str = parts
    try:
        version = int(version_str)
    except ValueError:
        return "La versión debe ser un número."

    row = await context.repository.get_prompt_version(prompt_name, version)
    if not row:
        return f"No encontré la versión {version} del prompt '{prompt_name}'."
    if row["is_active"]:
        return "Esa versión ya está activa."

    # Run eval if tracing is enabled and we have an ollama client
    eval_summary = ""
    if context.ollama_client:
        try:
            eval_result = await activate_with_eval(
                prompt_name=prompt_name,
                version=version,
                repository=context.repository,
                ollama_client=context.ollama_client,
            )
            if "error" not in eval_result:
                score = eval_result["score"]
                passed = eval_result["passed"]
                details = eval_result["details"]
                n = eval_result["entries_evaluated"]
                if n == 0:
                    eval_summary = "\n_Eval: sin dataset entries para evaluar._"
                else:
                    icon = "✅" if passed else "⚠️"
                    eval_summary = f"\n{icon} *Eval score:* {score:.0%} — {details}"
                    if not passed:
                        eval_summary += (
                            "\n_Score bajo threshold. Activando de todas formas (advisory)._"
                        )
        except Exception:
            logger.exception("activate_with_eval failed in /approve-prompt")
            eval_summary = "\n_Eval: no se pudo correr (activando de todas formas)._"

    await context.repository.activate_prompt_version(prompt_name, version)

    from app.eval.prompt_manager import invalidate_prompt_cache

    invalidate_prompt_cache(prompt_name)

    return (
        f"Prompt '{prompt_name}' v{version} activado.{eval_summary}\n"
        "Los próximos mensajes usarán la nueva versión."
    )


async def cmd_prompts(args: str, context: CommandContext) -> str:
    """List registered prompts or show a specific prompt's content and history.

    Usage:
        /prompts                    → list all prompts with active version
        /prompts <name>             → show active content + version history
        /prompts <name> <version>   → show content of a specific version
    """
    parts = args.strip().split()

    if not parts:
        # List all prompts
        rows = await context.repository.list_all_active_prompts()
        if not rows:
            return "No hay prompts registrados en la base de datos todavía."
        lines = ["*Prompts registrados:*"]
        for r in rows:
            created = r["approved_at"] or r["created_at"] or ""
            created = created[:10] if created else "—"
            lines.append(f"- `{r['prompt_name']}` v{r['version']} — {r['created_by']} ({created})")
        lines.append(
            "\n_Usa `/prompts <nombre>` para ver el contenido y el historial de versiones._"
        )
        return "\n".join(lines)

    prompt_name = parts[0]

    if len(parts) >= 2:
        # Show specific version
        try:
            version = int(parts[1])
        except ValueError:
            return "La versión debe ser un número.\nUso: /prompts <nombre> <versión>"
        row = await context.repository.get_prompt_version(prompt_name, version)
        if not row:
            return f"No encontré la versión {version} del prompt '{prompt_name}'."
        active_marker = " ✅ *activo*" if row["is_active"] else ""
        header = f"*{prompt_name}* v{version}{active_marker} (por {row['created_by']})"
        content_preview = row["content"][:800]
        if len(row["content"]) > 800:
            content_preview += f"\n…[{len(row['content'])} chars total]"
        return f"{header}\n\n```\n{content_preview}\n```"

    # Show active content + version history for prompt_name
    active = await context.repository.get_active_prompt_version(prompt_name)
    if not active:
        return (
            f"No encontré el prompt '{prompt_name}' en la base de datos.\n"
            "Usa `/prompts` para ver los prompts disponibles."
        )

    content_preview = active["content"][:600]
    if len(active["content"]) > 600:
        content_preview += f"\n…[{len(active['content'])} chars total]"

    versions = await context.repository.list_prompt_versions(prompt_name)
    history_lines = []
    for v in versions:
        active_marker = " ✅" if v["is_active"] else ""
        date = (v["approved_at"] or v["created_at"] or "")[:10]
        history_lines.append(f"  v{v['version']}{active_marker} — {v['created_by']} ({date})")

    history = "\n".join(history_lines) if history_lines else "  (sin historial)"
    return (
        f"*{prompt_name}* v{active['version']} ✅\n\n"
        f"```\n{content_preview}\n```\n\n"
        f"*Historial:*\n{history}\n\n"
        "_Usa `/prompts {name} <versión>` para ver otra versión, "
        "o `/approve-prompt {name} <versión>` para activarla._"
    ).replace("{name}", prompt_name)


async def cmd_help(args: str, context: CommandContext) -> str:
    registry: CommandRegistry = context.registry
    lines = ["*Available commands:*"]
    for spec in registry.list_commands():
        lines.append(f"- {spec.usage} — {spec.description}")

    if context.skill_registry:
        skills = context.skill_registry.list_skills()
        if skills:
            lines.append("")
            lines.append("*Skills (just ask me!):*")
            for skill in skills:
                lines.append(f"- 🔧 {skill.name}: {skill.description}")

    if context.mcp_manager:
        mcp_summary = context.mcp_manager.get_tools_summary()
        if mcp_summary:
            lines.append("")
            lines.append("*MCP integrations (just ask me!):*")
            # get_tools grouped by server
            tools = context.mcp_manager.get_tools()
            by_server: dict[str, list] = {}
            for tool in tools.values():
                server = tool.skill_name.removeprefix("mcp::")
                by_server.setdefault(server, []).append(tool)
            for server, server_tools in by_server.items():
                desc = context.mcp_manager._server_descriptions.get(server, server)
                lines.append(f"- 📡 {server} ({desc})")
                for tool in server_tools:
                    lines.append(f"    - {tool.name}: {tool.description}")

    return "\n".join(lines)


async def cmd_dev_review(args: str, context: CommandContext) -> str:
    """Launch a planner-orchestrator session to analyze a user's recent interactions."""
    import asyncio

    from app.agent.loop import create_session, get_active_session, run_agent_session

    session = get_active_session(context.phone_number)
    if session:
        return "Ya hay una sesión activa. Usa /cancel antes de iniciar una nueva."

    # Args may be a phone number (for admins reviewing other users) or free-text instructions.
    # If it looks like a phone number, use it as the target; otherwise use the caller's number
    # and treat the args as additional focus instructions.
    args_stripped = args.strip()
    extra_focus = ""
    if args_stripped and (args_stripped.startswith("+") or args_stripped.lstrip("+").isdigit()):
        phone_target = args_stripped
    else:
        phone_target = context.phone_number
        if args_stripped:
            extra_focus = f" Focus especially on: {args_stripped}."

    objective = (
        f"Analyze the recent interactions of user with phone number {phone_target}. "
        f"Use exactly this phone number when calling review_interactions or get_conversation_transcript. "
        f"1. Read their conversation transcript to understand what happened. "
        f"2. Review their traces to find anomalies (low scores, errors, hallucinations). "
        f"3. For any problematic trace, deep-dive into tool calls and context. "
        f"4. Write a debug report with findings and suggested fixes."
        f"{extra_focus}"
    )

    new_session = create_session(context.phone_number, objective)

    task = asyncio.create_task(
        run_agent_session(
            session=new_session,
            ollama_client=context.ollama_client,
            skill_registry=context.skill_registry,
            wa_client=context.wa_client,
            mcp_manager=context.mcp_manager,
            use_planner=True,
            recorder=context.trace_recorder,
            repository=context.repository,
        )
    )
    _bg_agent_tasks.add(task)
    task.add_done_callback(_bg_agent_tasks.discard)

    return (
        f"🔍 *Dev review iniciado*\n"
        f"_Analizando interacciones de {phone_target}_\n\n"
        "Te enviaré un reporte cuando termine."
    )


async def cmd_debug(args: str, context: CommandContext) -> str:
    """Toggle Auto Debug mode on or off."""
    profile = await context.repository.get_user_profile(context.phone_number)
    state = profile.get("onboarding_state", "pending")
    data = profile.get("data", {})

    current_debug = bool(data.get("debug_mode", False))
    args = args.strip().lower()

    if args == "on":
        new_debug = True
    elif args == "off":
        new_debug = False
    else:
        new_debug = not current_debug

    data["debug_mode"] = new_debug

    await context.repository.save_user_profile(context.phone_number, state, data)

    if new_debug:
        return "🪲 Auto Debug activado. A partir de ahora enviaré un log de mis ejecuciones para ayudarte a investigar."
    else:
        return "Auto Debug desactivado. He vuelto a mi modo normal."


async def cmd_reset_metrics(args: str, context: CommandContext) -> str:
    """Reset all metrics (traces, scores, eval dataset) without touching memories."""
    counts = await context.repository.reset_metrics()
    total = sum(counts.values())
    if total == 0:
        return "No hay métricas que borrar — todo ya está en cero."

    lines = [f"Métricas reseteadas ({total} registros eliminados):"]
    labels = {
        "traces": "Trazas",
        "trace_spans": "Spans",
        "trace_scores": "Scores",
        "eval_dataset": "Dataset eval",
        "eval_dataset_tags": "Dataset tags",
        "agent_command_log": "Agent command log",
    }
    for table, count in counts.items():
        if count > 0:
            label = labels.get(table, table)
            lines.append(f"  - {label}: {count}")

    lines.append("\nMemories, notas, conversaciones y proyectos no fueron afectados.")
    return "\n".join(lines)


def register_builtins(registry: CommandRegistry) -> None:
    registry.register(
        CommandSpec(
            name="remember",
            description="Guardar información importante",
            usage="/remember <dato>",
            handler=cmd_remember,
        )
    )
    registry.register(
        CommandSpec(
            name="forget",
            description="Olvidar un recuerdo guardado",
            usage="/forget <dato>",
            handler=cmd_forget,
        )
    )
    registry.register(
        CommandSpec(
            name="memories",
            description="Listar todos los recuerdos",
            usage="/memories",
            handler=cmd_memories,
        )
    )
    registry.register(
        CommandSpec(
            name="memory",
            description="Listar todos los recuerdos",
            usage="/memory",
            handler=cmd_memories,
        )
    )
    registry.register(
        CommandSpec(
            name="clear",
            description="Borrar historial de conversación",
            usage="/clear",
            handler=cmd_clear,
        )
    )
    registry.register(
        CommandSpec(
            name="setup",
            description="Guided profile setup / Configuración guiada de perfil",
            usage="/setup",
            handler=cmd_setup,
        )
    )
    registry.register(
        CommandSpec(
            name="review-skill",
            description="Ver skills instalados o servidores MCP",
            usage="/review-skill [nombre]",
            handler=cmd_review_skill,
        )
    )
    registry.register(
        CommandSpec(
            name="feedback",
            description="Dar feedback sobre la última respuesta (texto libre)",
            usage="/feedback <comentario>",
            handler=cmd_feedback,
        )
    )
    registry.register(
        CommandSpec(
            name="rate",
            description="Calificar la última respuesta del 1 al 5",
            usage="/rate <1-5>",
            handler=cmd_rate,
        )
    )
    registry.register(
        CommandSpec(
            name="approve-prompt",
            description="Activar una versión de prompt propuesta por el agente",
            usage="/approve-prompt <nombre> <versión>",
            handler=cmd_approve_prompt,
        )
    )
    registry.register(
        CommandSpec(
            name="prompts",
            description="Listar prompts registrados o ver contenido e historial de versiones",
            usage="/prompts [nombre] [versión]",
            handler=cmd_prompts,
        )
    )
    registry.register(
        CommandSpec(
            name="debug",
            description="Activar o desactivar el modo de autodiagnóstico",
            usage="/debug [on|off]",
            handler=cmd_debug,
        )
    )
    registry.register(
        CommandSpec(
            name="help",
            description="Mostrar comandos disponibles",
            usage="/help",
            handler=cmd_help,
        )
    )
    registry.register(
        CommandSpec(
            name="cancel",
            description="Cancelar la sesión agéntica activa",
            usage="/cancel",
            handler=cmd_cancel,
        )
    )
    registry.register(
        CommandSpec(
            name="agent",
            description="Iniciar nueva sesión agéntica o ver estado actual",
            usage="/agent [objetivo]",
            handler=cmd_agent,
        )
    )
    registry.register(
        CommandSpec(
            name="agent-resume",
            description="Retomar la última sesión agéntica si el bot se reinició",
            usage="/agent-resume",
            handler=cmd_agent_resume,
        )
    )
    registry.register(
        CommandSpec(
            name="dev-review",
            description="Analizar interacciones recientes de un usuario (planner-orchestrator)",
            usage="/dev-review [phone_number]",
            handler=cmd_dev_review,
        )
    )
    registry.register(
        CommandSpec(
            name="reset-metrics",
            description="Borrar trazas, scores y eval dataset (sin tocar memorias)",
            usage="/reset-metrics",
            handler=cmd_reset_metrics,
        )
    )
