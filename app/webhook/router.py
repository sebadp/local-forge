from __future__ import annotations

import asyncio
import base64
import datetime
import logging
import re as _re
from typing import TYPE_CHECKING, Literal, cast

from fastapi import APIRouter, BackgroundTasks, Query, Request
from fastapi.responses import PlainTextResponse, Response

from app.audio.transcriber import Transcriber
from app.commands.context import CommandContext
from app.commands.parser import parse_command
from app.config import Settings
from app.conversation.manager import ConversationManager
from app.conversation.summarizer import maybe_summarize
from app.dependencies import (
    get_command_registry,
    get_conversation_manager,
    get_daily_log,
    get_mcp_manager,
    get_memory_file,
    get_ollama_client,
    get_rate_limiter,
    get_repository,
    get_settings,
    get_skill_registry,
    get_trace_recorder,
    get_transcriber,
    get_vec_available,
    get_whatsapp_client,
)
from app.eval.dataset import add_correction_pair, maybe_curate_to_dataset
from app.eval.prompt_manager import get_active_prompt
from app.formatting.whatsapp import markdown_to_whatsapp
from app.llm.client import OllamaClient
from app.memory.daily_log import DailyLog
from app.models import ChatMessage, Note, WhatsAppMessage
from app.platforms.base import PlatformClient
from app.platforms.models import IncomingMessage, Platform
from app.profiles.discovery import maybe_discover_profile_updates
from app.profiles.onboarding import handle_onboarding_message
from app.profiles.prompt_builder import build_system_prompt
from app.skills.executor import execute_tool_loop
from app.skills.registry import SkillRegistry
from app.skills.router import classify_intent
from app.tracing.context import TraceContext
from app.webhook.parser import extract_messages, extract_reactions
from app.webhook.security import validate_signature
from app.whatsapp.client import WhatsAppClient

if TYPE_CHECKING:
    from app.commands.registry import CommandRegistry
    from app.mcp.manager import McpManager

logger = logging.getLogger(__name__)

router = APIRouter()

_in_flight: set[asyncio.Task] = set()


class WhatsAppPlatformAdapter:
    """Adapts WhatsAppClient to the PlatformClient Protocol."""

    def __init__(self, client: WhatsAppClient, current_message_id: str = "") -> None:
        self._client = client
        self._msg_id = current_message_id

    async def send_message(self, to_id: str, text: str) -> str | None:
        return await self._client.send_message(to_id, text)

    async def download_media(self, media_id: str) -> bytes:
        return await self._client.download_media(media_id)

    async def mark_as_read(self, message_id: str) -> None:
        await self._client.mark_as_read(message_id)

    async def send_typing_indicator(self, to_id: str) -> None:
        if self._msg_id:
            await self._client.send_reaction(self._msg_id, to_id, "\u23f3")

    async def remove_typing_indicator(self, to_id: str, indicator_id: str | None = None) -> None:
        mid = indicator_id or self._msg_id
        if mid:
            await self._client.send_reaction(mid, to_id, "")

    def format_text(self, text: str) -> str:
        return markdown_to_whatsapp(text)

    def platform_name(self) -> str:
        return "whatsapp"


def _wa_msg_to_incoming(msg: WhatsAppMessage) -> IncomingMessage:
    """Convert a WhatsAppMessage to the platform-agnostic IncomingMessage."""
    return IncomingMessage(
        platform=Platform.WHATSAPP,
        user_id=msg.from_number,
        message_id=msg.message_id,
        timestamp=msg.timestamp,
        text=msg.text,
        type=cast(Literal["text", "audio", "image"], msg.type),
        media_id=msg.media_id,
        reply_to_message_id=msg.reply_to_message_id,
    )


def _track_task(task: asyncio.Task) -> asyncio.Task:
    """Track a background task for graceful shutdown."""
    _in_flight.add(task)
    task.add_done_callback(_in_flight.discard)
    return task


async def wait_for_in_flight(timeout: float = 30.0) -> None:
    """Wait for all in-flight background tasks to complete."""
    if not _in_flight:
        return
    logger.info("Waiting for %d in-flight tasks (timeout=%.1fs)", len(_in_flight), timeout)
    done, pending = await asyncio.wait(_in_flight, timeout=timeout)
    if pending:
        logger.warning("%d tasks still running after timeout", len(pending))


@router.get("/webhook")
async def verify_webhook(
    request: Request,
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
) -> Response:
    settings = get_settings(request)
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return PlainTextResponse(content=hub_challenge)
    return PlainTextResponse(content="Forbidden", status_code=403)


@router.post("/webhook")
async def incoming_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    settings = get_settings(request)
    body = await request.body()

    # Validate signature
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not validate_signature(body, signature, settings.whatsapp_app_secret):
        logger.warning("Invalid webhook signature")
        return Response(status_code=200)

    payload = await request.json()

    repository = get_repository(request)
    wa_client = get_whatsapp_client(request)

    # Process reactions first (lightweight, fire-and-forget, no dedup needed)
    reactions = extract_reactions(payload)
    for reaction in reactions:
        background_tasks.add_task(_handle_reaction, reaction, repository, wa_client, settings)

    messages = extract_messages(payload)
    ollama_client = get_ollama_client(request)
    conversation = get_conversation_manager(request)
    command_registry = get_command_registry(request)
    memory_file = get_memory_file(request)
    daily_log = get_daily_log(request)
    rate_limiter = get_rate_limiter(request)
    transcriber = get_transcriber(request)
    skill_registry = get_skill_registry(request)
    mcp_manager = get_mcp_manager(request)
    vec_available = get_vec_available(request)
    trace_recorder = get_trace_recorder(request)

    for msg in messages:
        logger.info(
            "Incoming [%s] (%s): %s",
            msg.from_number,
            msg.type,
            msg.text[:80] if msg.text else "(empty)",
        )
        if msg.from_number not in settings.allowed_phone_numbers:
            logger.warning("Message from non-whitelisted number: %s", msg.from_number)
            continue
        if not rate_limiter.is_allowed(msg.from_number):
            logger.warning("Rate limit exceeded for %s", msg.from_number)
            continue
        if await repository.try_claim_message(msg.message_id):
            logger.info("Duplicate message ignored: %s", msg.message_id)
            continue
        background_tasks.add_task(
            process_message,
            msg=msg,
            settings=settings,
            wa_client=wa_client,
            ollama_client=ollama_client,
            conversation=conversation,
            repository=repository,
            command_registry=command_registry,
            memory_file=memory_file,
            daily_log=daily_log,
            transcriber=transcriber,
            skill_registry=skill_registry,
            mcp_manager=mcp_manager,
            vec_available=vec_available,
            trace_recorder=trace_recorder,
        )

    return Response(status_code=200)


async def process_message(
    msg: WhatsAppMessage,
    settings: Settings,
    wa_client: WhatsAppClient,
    ollama_client: OllamaClient,
    conversation: ConversationManager,
    repository,
    command_registry,
    memory_file,
    daily_log: DailyLog,
    transcriber: Transcriber,
    skill_registry: SkillRegistry,
    mcp_manager: McpManager | None = None,
    vec_available: bool = False,
    trace_recorder=None,
) -> None:
    """WhatsApp-specific wrapper: converts to platform-agnostic types and delegates."""
    incoming = _wa_msg_to_incoming(msg)
    adapter = WhatsAppPlatformAdapter(wa_client, msg.message_id)
    await process_message_generic(
        msg=incoming,
        platform_client=adapter,
        settings=settings,
        ollama_client=ollama_client,
        conversation=conversation,
        repository=repository,
        command_registry=command_registry,
        memory_file=memory_file,
        daily_log=daily_log,
        transcriber=transcriber,
        skill_registry=skill_registry,
        mcp_manager=mcp_manager,
        vec_available=vec_available,
        trace_recorder=trace_recorder,
    )


async def process_message_generic(
    msg: IncomingMessage,
    platform_client: PlatformClient,
    settings: Settings,
    ollama_client: OllamaClient,
    conversation: ConversationManager,
    repository,
    command_registry,
    memory_file,
    daily_log: DailyLog,
    transcriber: Transcriber,
    skill_registry: SkillRegistry,
    mcp_manager: McpManager | None = None,
    vec_available: bool = False,
    trace_recorder=None,
) -> None:
    """Platform-agnostic message processor. Called by both WA and Telegram routers."""
    # Parallelize initial platform calls (mark-read + typing indicator)
    try:
        await asyncio.gather(
            platform_client.mark_as_read(msg.message_id),
            platform_client.send_typing_indicator(msg.user_id),
        )
    except Exception:
        logger.debug("Failed to send initial platform signals")

    # HITL: if the agent is waiting for user input, route the message to it
    if msg.text:
        from app.agent.hitl import resolve_hitl

        if resolve_hitl(msg.user_id, msg.text):
            logger.info(
                "HITL response from %s consumed by active agent session",
                msg.user_id,
            )
            try:
                await platform_client.remove_typing_indicator(msg.user_id)
            except Exception:
                pass
            return

    try:
        await _handle_message(
            msg,
            settings,
            platform_client,
            ollama_client,
            conversation,
            repository,
            command_registry,
            memory_file,
            daily_log,
            transcriber,
            skill_registry,
            mcp_manager=mcp_manager,
            vec_available=vec_available,
            trace_recorder=trace_recorder,
        )
    finally:
        # Remove typing indicator
        try:
            await platform_client.remove_typing_indicator(msg.user_id)
        except Exception:
            logger.debug("Failed to remove typing indicator")


async def _get_query_embedding(
    user_text: str,
    settings: Settings,
    ollama_client: OllamaClient,
    vec_available: bool,
) -> list[float] | None:
    """Get query embedding for semantic search. Returns None on failure."""
    if settings.semantic_search_enabled and vec_available and user_text:
        try:
            result = await ollama_client.embed(
                [user_text],
                model=settings.embedding_model,
            )
            return result[0]
        except Exception:
            logger.warning("Failed to compute query embedding", exc_info=True)
    return None


async def _get_memories(
    user_text: str,
    settings: Settings,
    ollama_client: OllamaClient,
    repository,
    vec_available: bool,
    query_embedding: list[float] | None = None,
) -> list[str]:
    """Get relevant memories: semantic search with threshold filtering if available, else all active.

    Applies memory_similarity_threshold to filter out low-relevance memories.
    Fallback: top-3 always included so there's always some context.
    """
    if query_embedding is not None:
        try:
            results = await repository.search_similar_memories_with_distance(
                query_embedding,
                top_k=settings.semantic_search_top_k,
            )
            threshold = settings.memory_similarity_threshold
            passed = [content for content, dist in results if dist < threshold]
            total = len(results)
            injected = len(passed)
            logger.debug(
                "context.memories: %d/%d passed threshold (%.2f)",
                injected,
                total,
                threshold,
            )
            # Fallback: always inject at least top-3 so there's some context
            if not passed and results:
                passed = [content for content, _ in results[:3]]
                logger.debug("context.memories: fallback to top-3 (none passed threshold)")
            return passed
        except Exception:
            logger.warning(
                "Semantic memory search failed, falling back to all memories",
                exc_info=True,
            )
    return await repository.get_active_memories(limit=settings.semantic_search_top_k)


async def _get_relevant_notes(
    query_embedding: list[float] | None,
    settings: Settings,
    repository,
    vec_available: bool,
) -> list[Note]:
    """Get relevant notes via semantic search if available."""
    if settings.semantic_search_enabled and vec_available and query_embedding:
        try:
            return await repository.search_similar_notes(
                query_embedding,
                top_k=5,
            )
        except Exception:
            logger.warning("Semantic note search failed", exc_info=True)
    return []


def _build_capabilities_section(
    skill_registry: SkillRegistry,
    command_registry: CommandRegistry,
    mcp_manager: McpManager | None,
) -> str | None:
    """Build a rich, structured capabilities section for the LLM context.

    Groups commands, skills (with their tools), and MCP servers so the
    agent knows what it can do and when to use each capability.
    """
    sections: list[str] = []

    # --- Commands (user-typed /slash commands) ---
    commands = command_registry.list_commands()
    if commands:
        cmd_lines = [
            "Commands (the user types these directly — if they ask how to save info, mention /remember):"
        ]
        for cmd in commands:
            cmd_lines.append(f"  /{cmd.name} — {cmd.description}")
        sections.append("\n".join(cmd_lines))

    # --- Skills (tool-calling) ---
    skills = skill_registry.list_skills()
    if skills:
        skill_lines = ["Skills (you call these via tool calling):"]
        for skill in skills:
            tool_names = [t.name for t in skill_registry.get_tools_for_skill(skill.name)]
            tools_str = ", ".join(tool_names) if tool_names else "no tools registered"
            skill_lines.append(f"  {skill.name} — {skill.description}")
            skill_lines.append(f"    Tools: {tools_str}")
        sections.append("\n".join(skill_lines))

    # --- MCP Servers ---
    if mcp_manager:
        mcp_tools = mcp_manager.get_tools()
        if mcp_tools:
            by_server: dict[str, list[str]] = {}
            for tool in mcp_tools.values():
                server = tool.skill_name.removeprefix("mcp::")  # type: ignore[union-attr]
                by_server.setdefault(server, []).append(f"{tool.name}: {tool.description}")

            mcp_lines = ["MCP Servers (external integrations):"]
            for server_name, tool_descs in by_server.items():
                desc = mcp_manager._server_descriptions.get(server_name, "")
                header = f"  {server_name} ({desc})" if desc else f"  {server_name}"
                mcp_lines.append(header)
                for td in tool_descs:
                    mcp_lines.append(f"    - {td}")
            sections.append("\n".join(mcp_lines))

    if not sections:
        return None

    header = "You have the following capabilities. Use them proactively when the user's message is relevant."
    meta_note = (
        "\nTool expansion: if the current tools are insufficient for the task, "
        "call request_more_tools(categories=[...]) to load additional tool categories dynamically."
    )
    return header + "\n\n" + "\n\n".join(sections) + meta_note


def _build_capabilities_for_categories(
    categories: list[str],
    skill_registry: SkillRegistry,
    command_registry: CommandRegistry,
    mcp_manager: McpManager | None,
) -> str | None:
    """Build a capabilities section filtered to the relevant categories only.

    Commands are always included. Skills and MCP tools are filtered to only
    include those whose tools appear in the selected categories.
    """
    from app.skills.router import TOOL_CATEGORIES

    # Collect all tool names relevant to the given categories
    relevant_tools: set[str] = set()
    for cat in categories:
        relevant_tools.update(TOOL_CATEGORIES.get(cat, []))

    sections: list[str] = []

    # --- Commands (always included — short, useful for any message) ---
    commands = command_registry.list_commands()
    if commands:
        cmd_lines = [
            "Commands (the user types these directly — if they ask how to save info, mention /remember):"
        ]
        for cmd in commands:
            cmd_lines.append(f"  /{cmd.name} — {cmd.description}")
        sections.append("\n".join(cmd_lines))

    # --- Skills filtered by category ---
    skills = skill_registry.list_skills()
    if skills:
        skill_lines = ["Skills (you call these via tool calling):"]
        added = False
        for skill in skills:
            tool_names = [t.name for t in skill_registry.get_tools_for_skill(skill.name)]
            # Include skill if any of its tools are in the relevant set
            if relevant_tools and not any(t in relevant_tools for t in tool_names):
                continue
            tools_str = ", ".join(tool_names) if tool_names else "no tools registered"
            skill_lines.append(f"  {skill.name} — {skill.description}")
            skill_lines.append(f"    Tools: {tools_str}")
            added = True
        if added:
            sections.append("\n".join(skill_lines))

    # --- MCP Servers filtered by category ---
    if mcp_manager:
        mcp_tools = mcp_manager.get_tools()
        if mcp_tools:
            by_server: dict[str, list[str]] = {}
            for tool in mcp_tools.values():
                if relevant_tools and tool.name not in relevant_tools:
                    continue
                server = tool.skill_name.removeprefix("mcp::")  # type: ignore[union-attr]
                by_server.setdefault(server, []).append(f"{tool.name}: {tool.description}")

            if by_server:
                mcp_lines = ["MCP Servers (external integrations):"]
                for server_name, tool_descs in by_server.items():
                    desc = mcp_manager._server_descriptions.get(server_name, "")
                    header = f"  {server_name} ({desc})" if desc else f"  {server_name}"
                    mcp_lines.append(header)
                    for td in tool_descs:
                        mcp_lines.append(f"    - {td}")
                sections.append("\n".join(mcp_lines))

    if not sections:
        return None

    header = "You have the following capabilities. Use them proactively when the user's message is relevant."
    meta_note = (
        "\nTool expansion: if the current tools are insufficient for the task, "
        "call request_more_tools(categories=[...]) to load additional tool categories dynamically."
    )
    return header + "\n\n" + "\n\n".join(sections) + meta_note


async def _get_active_projects_summary(phone_number: str, repository) -> str | None:
    """Build a brief projects status line for the LLM context. Returns None if no active projects."""
    try:
        projects = await repository.list_projects(phone_number, status="active")
        if not projects:
            return None
        capped = projects[:5]
        lines = ["Active projects:"]
        for p in capped:
            progress = await repository.get_project_progress(p.id)
            total = progress["total"]
            done = progress["done"]
            pct = int(done / total * 100) if total > 0 else 0
            lines.append(f"  - {p.name}: {done}/{total} tasks ({pct}%)")
        return "\n".join(lines)
    except Exception:
        logger.warning("Failed to fetch active projects summary", exc_info=True)
        return None


def _format_memories(memories: list[str]) -> str | None:
    """Format memory list into a block string."""
    if not memories:
        return None
    return "Important user information:\n" + "\n".join(f"- {m}" for m in memories)


def _format_notes(relevant_notes: list[Note]) -> str | None:
    """Format notes list into a block string."""
    if not relevant_notes:
        return None
    return "Relevant notes:\n" + "\n".join(
        f"- [{n.id}] {n.title}: {n.content[:200]}" for n in relevant_notes
    )


def _build_context(
    system_prompt: str,
    memories: list[str],
    relevant_notes: list[Note],
    daily_logs: str | None,
    skills_summary: str | None,
    summary: str | None,
    history: list[ChatMessage],
    projects_summary: str | None = None,
) -> list[ChatMessage]:
    """Build LLM context from pre-fetched data (sync, no DB calls).

    Consolidates context into a single system message with XML-delimited sections
    for better attention focus in qwen3:8b.
    """
    from app.context.context_builder import ContextBuilder

    builder = ContextBuilder(system_prompt)
    builder.add_section("user_memories", _format_memories(memories))
    builder.add_section("active_projects", projects_summary)
    builder.add_section("relevant_notes", _format_notes(relevant_notes))
    builder.add_section("recent_activity", daily_logs)
    builder.add_section("capabilities", skills_summary)
    if summary:
        builder.add_section("conversation_summary", f"Previous conversation summary:\n{summary}")

    context: list[ChatMessage] = [
        ChatMessage(role="system", content=builder.build_system_message())
    ]
    context.extend(history)
    return context


_REACTION_SCORE_MAP: dict[str, float] = {
    "👍": 1.0,
    "❤️": 1.0,
    "🙏": 0.9,
    "😂": 0.8,
    "😮": 0.5,
    "😢": 0.2,
    "👎": 0.0,
}


async def _save_self_correction_memory(
    user_text: str,
    failed_checks: list[str],
    repository,
    memory_file,
    ollama_client,
    embed_model: str | None,
    vec_available: bool,
) -> None:
    """Persist a guardrail failure as a self_correction memory. Best-effort.

    Implements cooldown: if the same check type was already recorded in the last 2 hours,
    skip to prevent spamming the memory store and causing watcher loops.
    """
    try:
        # Cooldown check: find which check types already have a recent correction
        recent = await repository.get_recent_self_corrections(hours=2)
        recent_check_types: set[str] = set()
        for m in recent:
            # Extract check names from existing correction notes
            if "detectaron:" in m.content:
                existing_checks_str = m.content.split("detectaron:")[1].split(".")[0]
                recent_check_types.update(c.strip() for c in existing_checks_str.split(","))

        new_checks = [c for c in failed_checks if c not in recent_check_types]
        if not new_checks:
            logger.info(
                "Self-correction skipped (cooldown active for: %s)", ", ".join(failed_checks)
            )
            return

        checks_str = ", ".join(new_checks)
        note = (
            f"[auto-corrección] Al responder '{user_text[:60]}...', "
            f"los guardrails detectaron: {checks_str}. "
            f"Recordar evitar este tipo de error."
        )
        # Save to DB only — do NOT sync to MEMORY.md (self_correction excluded from watcher)
        mem_id = await repository.add_memory(note, category="self_correction")
        logger.info("Saved self-correction memory for checks: %s", checks_str)

        if embed_model and vec_available and ollama_client:
            from app.embeddings.indexer import embed_memory

            await embed_memory(mem_id, note, repository, ollama_client, embed_model)
    except Exception:
        logger.warning("Failed to save self-correction memory", exc_info=True)


async def _handle_reaction(reaction, repository, wa_client=None, settings=None) -> None:
    """Convert a WhatsApp reaction to a trace score and trigger dataset curation.

    Best-effort — no exceptions propagated.
    """
    from app.models import WhatsAppReaction

    if not isinstance(reaction, WhatsAppReaction):
        return
    try:
        trace_id = await repository.get_trace_id_by_wa_message_id(reaction.reacted_message_id)
        if not trace_id:
            logger.debug(
                "Reaction %s to unknown message %s, ignoring",
                reaction.emoji,
                reaction.reacted_message_id,
            )
            return

        value = _REACTION_SCORE_MAP.get(reaction.emoji, 0.5)
        await repository.save_trace_score(
            trace_id=trace_id,
            name="user_reaction",
            value=value,
            source="user",
            comment=reaction.emoji,
        )
        logger.info(
            "Reaction %s from %s → trace %s (score=%.1f)",
            reaction.emoji,
            reaction.from_number,
            trace_id,
            value,
        )

        # Feed reaction signal into the dataset curation pipeline
        if settings and getattr(settings, "eval_auto_curate", False):
            io = await repository.get_trace_io_by_id(trace_id)
            if io:
                input_text, output_text = io
                _track_task(
                    asyncio.create_task(
                        maybe_curate_to_dataset(
                            trace_id=trace_id,
                            input_text=input_text,
                            output_text=output_text,
                            repository=repository,
                        )
                    )
                )

        # For very negative reactions, ask the user what the correct answer should have been
        if value <= 0.2 and wa_client:
            existing_scores = await repository.get_trace_scores(trace_id)
            already_prompted = any(s["name"] == "correction_prompted" for s in existing_scores)
            if not already_prompted:
                await repository.save_trace_score(
                    trace_id=trace_id,
                    name="correction_prompted",
                    value=1.0,
                    source="system",
                    comment="correction prompt sent after negative reaction",
                )
                _track_task(
                    asyncio.create_task(
                        wa_client.send_message(
                            reaction.from_number,
                            "Vi que no te gustó mi respuesta. "
                            "¿Qué debería haber dicho? "
                            "Respondé con la respuesta correcta y la recordaré para mejorar.",
                        )
                    )
                )

    except Exception:
        logger.warning("Failed to process reaction", exc_info=True)


async def _handle_guardrail_failure(
    report,
    context: list[ChatMessage],
    ollama_client: OllamaClient,
    original_reply: str,
    trace_ctx=None,
) -> str:
    """Attempt one remediation for guardrail failure. Returns fixed reply or original.

    Single-shot: no recursion, no re-check after remediation.
    trace_ctx: optional TraceContext — if present, wraps the remediation LLM call in a span.
    """
    from app.guardrails.checks import redact_pii

    failed_names = {r.check_name for r in report.results if not r.passed}

    current_reply = original_reply

    # Empty: try once more
    if "not_empty" in failed_names:
        try:
            retry = await ollama_client.chat(context)
            current_reply = retry if retry.strip() else "Disculpa, no pude generar una respuesta."
        except Exception:
            current_reply = "Disculpa, no pude generar una respuesta."

    # Language mismatch: re-prompt with bilingual explicit instruction
    elif "language_match" in failed_names:
        lang_result = next(r for r in report.results if r.check_name == "language_match")
        detected_code = lang_result.details  # 2-letter ISO code of user's language
        _LANG_NAMES = {
            "es": "español",
            "en": "English",
            "fr": "français",
            "de": "Deutsch",
            "it": "italiano",
            "pt": "português",
            "ru": "русский",
            "zh-cn": "中文",
            "zh-tw": "中文",
            "ja": "日本語",
            "ko": "한국어",
            "ar": "العربية",
            "nl": "Nederlands",
            "pl": "polski",
            "sv": "svenska",
            "tr": "Türkçe",
            "uk": "українська",
            "ca": "català",
            "gl": "galego",
        }
        lang_name = _LANG_NAMES.get(detected_code)
        if lang_name:
            # Bilingual instruction: target language first, then English fallback
            # This ensures the LLM understands even if it biases toward English
            hint_content = (
                f"IMPORTANTE: El usuario escribió en {lang_name}. "
                f"Reescribe la respuesta SOLO en {lang_name}, sin cambiar el contenido.\n"
                f"IMPORTANT: The user wrote in {lang_name}. "
                f"Rewrite the response ONLY in {lang_name}, do not change the content."
            )
        else:
            # Unknown language code — bilingual generic fallback
            hint_content = (
                "IMPORTANTE: Tu respuesta anterior estaba en el idioma equivocado. "
                "Mira el historial y responde en el mismo idioma que usa el usuario.\n"
                "IMPORTANT: Your previous reply was in the wrong language. "
                "Look at the conversation history and respond in the user's language."
            )
        hint_msg = ChatMessage(role="user", content=hint_content)
        try:
            if trace_ctx:
                async with trace_ctx.span("guardrails:remediation", kind="generation") as span:
                    span.set_metadata({"check": "language_match", "lang_code": detected_code})
                    retry = await ollama_client.chat(context + [hint_msg])
            else:
                retry = await ollama_client.chat(context + [hint_msg])
            if retry.strip():
                current_reply = retry
        except Exception:
            pass

    # PII: redact in-place, no re-prompt needed
    # Done after generating text so it applies to the (potentially) regenerated reply as well
    if "no_pii" in failed_names:
        current_reply = redact_pii(current_reply)

    # Everything else (excessive_length, no_raw_tool_json): log and pass through
    return current_reply


# --- Correction detection patterns ---

# High-confidence: almost always indicate the user is correcting the bot
_CORRECTION_PATTERNS_HIGH = [
    r"te pregunté|te pregunte",
    r"no era eso",
    r"eso no es lo que",
    r"no te pedí|no te pedi",
    r"está mal|esta mal",
    r"eso es incorrecto",
    r"no[,.]?\s+(?:yo\s+)?(?:dije|quise|pregunté|pregunte)",
]

# Low-confidence: may be corrections OR normal messages
_CORRECTION_PATTERNS_LOW = [
    r"^no[,.]?\s+(?:eso|así|asa|esa|ese)",
    r"(?:está|esta)\s+(?:mal|equivocado|equivocada)$",
]

_CORR_HIGH_RE = [_re.compile(p, _re.IGNORECASE) for p in _CORRECTION_PATTERNS_HIGH]
_CORR_LOW_RE = [_re.compile(p, _re.IGNORECASE) for p in _CORRECTION_PATTERNS_LOW]


def _detect_correction(user_text: str) -> float | None:
    """Detect if user_text is correcting the bot's previous response.

    Returns:
        0.3 — low-confidence correction (possible but uncertain)
        0.0 — high-confidence correction
        None — no correction detected
    """
    for pattern in _CORR_HIGH_RE:
        if pattern.search(user_text):
            return 0.0  # high-confidence failure
    for pattern in _CORR_LOW_RE:
        if pattern.search(user_text):
            return 0.3  # low-confidence suspicion
    return None


async def _is_repeated_question(
    query_embedding: list[float],
    conv_id: int,
    repository,
) -> bool:
    """Check if this question is semantically similar to recent ones (cosine > 0.9).

    Returns False if embeddings are unavailable or comparison fails.
    """
    try:
        recent_embeddings = await repository.get_recent_user_message_embeddings(conv_id)
        if not recent_embeddings:
            return False

        # Compute cosine similarity (dot product of normalized vectors)
        def cosine(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b, strict=False))
            na = sum(x * x for x in a) ** 0.5
            nb = sum(x * x for x in b) ** 0.5
            if na == 0 or nb == 0:
                return 0.0
            return dot / (na * nb)

        for emb in recent_embeddings:
            if cosine(query_embedding, emb) > 0.9:
                return True
    except Exception:
        pass
    return False


async def _handle_message(
    msg: IncomingMessage,
    settings: Settings,
    platform_client: PlatformClient,
    ollama_client: OllamaClient,
    conversation: ConversationManager,
    repository,
    command_registry,
    memory_file,
    daily_log: DailyLog,
    transcriber: Transcriber,
    skill_registry: SkillRegistry,
    mcp_manager: McpManager | None = None,
    vec_available: bool = False,
    trace_recorder=None,
) -> None:
    # Handle audio: transcribe to text
    if msg.type == "audio" and msg.media_id:
        try:
            audio_bytes = await platform_client.download_media(msg.media_id)
            transcription = await transcriber.transcribe_async(audio_bytes)
            logger.info("Transcribed audio [%s]: %s", msg.user_id, transcription[:80])
            msg = msg.model_copy(update={"text": transcription})
        except Exception:
            logger.exception("Audio transcription failed")
            await platform_client.send_message(
                msg.user_id,
                "Sorry, I couldn't process that audio. Please try again.",
            )
            return

    # Load user profile early (after audio transcription, so audio can feed into onboarding)
    profile_row = await repository.get_user_profile(msg.user_id)
    in_onboarding = settings.onboarding_enabled and profile_row["onboarding_state"] != "complete"

    # Handle image: llava describes → used as onboarding answer OR qwen3 responds normally
    if msg.type == "image" and msg.media_id:
        try:
            image_bytes = await platform_client.download_media(msg.media_id)
            image_b64 = base64.b64encode(image_bytes).decode()

            # Step 1: llava describes the image
            vision_messages = [
                ChatMessage(
                    role="user",
                    content="Describe this image in detail.",
                    images=[image_b64],
                ),
            ]
            description = await ollama_client.chat(vision_messages, model=settings.vision_model)
            logger.debug("Vision (LLaVA) RAW OUTPUT: %r", description)
            logger.info("Vision description [%s]: %s", msg.user_id, description[:120])

            if in_onboarding:
                # During onboarding: use image description as the user's answer to current step
                user_text = description
                next_state, reply, new_data = await handle_onboarding_message(
                    user_text,
                    profile_row["onboarding_state"],
                    profile_row["data"],
                    ollama_client,
                )
                await repository.save_user_profile(msg.user_id, next_state, new_data)
                await platform_client.send_message(msg.user_id, platform_client.format_text(reply))
                return

            # Normal image flow: pass description to qwen3 with conversation context
            user_text = msg.text or "Describe what you see in this image"
            history_text = f"[Image] {user_text}"
            await conversation.add_message(msg.user_id, "user", history_text, msg.message_id)

            # Build context with image description injected
            query_emb = await _get_query_embedding(
                user_text,
                settings,
                ollama_client,
                vec_available,
            )
            memories = await _get_memories(
                user_text,
                settings,
                ollama_client,
                repository,
                vec_available,
                query_embedding=query_emb,
            )
            context = await conversation.get_context(
                msg.user_id,
                settings.system_prompt,
                memories,
            )
            # Add image description as context for qwen3
            context.append(
                ChatMessage(
                    role="user",
                    content=f"[The user sent an image. Description: {description}]\n\n{user_text}",
                )
            )

            reply = await ollama_client.chat(context)
            await conversation.add_message(msg.user_id, "assistant", reply)
            await platform_client.send_message(msg.user_id, platform_client.format_text(reply))
        except Exception:
            logger.exception("Image processing failed")
            await platform_client.send_message(
                msg.user_id,
                "Sorry, I couldn't process that image. Please try again.",
            )
        return

    # Check for commands (text-based only)
    parsed = parse_command(msg.text)
    if parsed:
        cmd_name, cmd_args = parsed
        spec = command_registry.get(cmd_name)
        if spec:
            ctx = CommandContext(
                repository=repository,
                memory_file=memory_file,
                phone_number=msg.user_id,
                registry=command_registry,
                skill_registry=skill_registry,
                mcp_manager=mcp_manager,
                ollama_client=ollama_client,
                daily_log=daily_log,
                embed_model=(
                    settings.embedding_model
                    if settings.semantic_search_enabled and vec_available
                    else None
                ),
                wa_client=platform_client,
                settings=settings,
                trace_recorder=trace_recorder,
            )
            try:
                reply = await spec.handler(cmd_args, ctx)
            except Exception:
                logger.exception("Command %s failed", cmd_name)
                reply = "Sorry, that command failed. Please try again."
        else:
            reply = f"Unknown command: /{cmd_name}. Type /help for available commands."
        try:
            await platform_client.send_message(msg.user_id, reply)
        except Exception:
            logger.exception("Failed to send platform message")
        return

    # Onboarding interception: handle before normal flow
    if in_onboarding:
        try:
            next_state, reply, new_data = await handle_onboarding_message(
                msg.text,
                profile_row["onboarding_state"],
                profile_row["data"],
                ollama_client,
            )
            await repository.save_user_profile(msg.user_id, next_state, new_data)
            await platform_client.send_message(msg.user_id, platform_client.format_text(reply))
        except Exception:
            logger.exception("Onboarding step failed")
            await platform_client.send_message(
                msg.user_id,
                "Sorry, something went wrong. Please try again.",
            )
        return

    # Normal message flow (text or transcribed audio)
    user_text = msg.text
    if msg.type == "audio":
        user_text = f"[Audio] {msg.text}"

    # Inject current date + user profile into system prompt
    # (date only — for current time the LLM should call get_current_datetime)
    now = datetime.datetime.now(datetime.UTC)
    current_date = now.strftime("%Y-%m-%d")
    base_prompt = await get_active_prompt("system_prompt", repository, settings.system_prompt)
    system_prompt_with_date = build_system_prompt(
        base_prompt,
        profile_row["data"],
        current_date,
    )

    # Reply context: prepend quoted message if replying (sequential, needs result before Phase A)
    if msg.reply_to_message_id:
        quoted = await repository.get_message_by_wa_id(msg.reply_to_message_id)
        if quoted:
            user_text = f'[Replying to: "{quoted.content[:200]}"]\n{user_text}'

    # Get/create conversation once (populates the manager's cache)
    conv_id = await conversation.get_conversation_id(msg.user_id)

    # Determine message type for tracing
    _msg_type = "audio" if msg.type == "audio" else "text"

    recorder = trace_recorder

    # Determine if tracing is enabled (sample rate check)
    import random

    _trace_enabled = (
        settings.tracing_enabled
        and recorder is not None
        and random.random() < settings.tracing_sample_rate
    )

    trace_ctx: TraceContext | None = None

    async def _run_normal_flow(trace_ctx: TraceContext | None) -> None:
        """Inner function to run the normal message flow, optionally within a trace."""
        nonlocal conv_id

        from app.context.conversation_context import ConversationContext

        # Determine if tools are available (used for classify_task)
        has_tools = skill_registry.has_tools() or bool(mcp_manager and mcp_manager.get_tools())

        # Kick off classify_intent in parallel with Phase A/B (LLM call, 1-3s)
        classify_task: asyncio.Task[list[str]] | None = None
        if has_tools:
            classify_task = asyncio.create_task(classify_intent(user_text, ollama_client))

        # Phase A+B: ConversationContext.build() || save user message
        # Build fetches in parallel: embed → memories, windowed history, notes, daily logs, projects
        # save_message runs concurrently with the build (independent side effect)
        if trace_ctx:
            async with trace_ctx.span("phase_ab") as span:
                span.set_metadata({"phase": "context_build+save"})
                ctx, _ = await asyncio.gather(
                    ConversationContext.build(
                        phone_number=msg.user_id,
                        user_text=user_text,
                        repository=repository,
                        conversation_manager=conversation,
                        ollama_client=ollama_client,
                        settings=settings,
                        daily_log=daily_log,
                        vec_available=vec_available,
                    ),
                    repository.save_message(conv_id, "user", user_text, msg.message_id),
                )
                # Persist phase timing + search stats in span metadata for baseline measurement
                if ctx.build_timing:
                    span.set_metadata(ctx.build_timing)
                if ctx.search_stats:
                    span.set_metadata(ctx.search_stats)
        else:
            ctx, _ = await asyncio.gather(
                ConversationContext.build(
                    phone_number=msg.user_id,
                    user_text=user_text,
                    repository=repository,
                    conversation_manager=conversation,
                    ollama_client=ollama_client,
                    settings=settings,
                    daily_log=daily_log,
                    vec_available=vec_available,
                ),
                repository.save_message(conv_id, "user", user_text, msg.message_id),
            )

        # Unpack context fields for compatibility with the rest of the flow
        query_embedding = ctx.query_embedding
        memories = ctx.memories
        relevant_notes = ctx.relevant_notes
        daily_logs = ctx.daily_logs
        history = ctx.history
        summary = ctx.summary
        projects_summary = ctx.projects_summary

        # Log search stats for observability (best-effort)
        if ctx.search_stats:
            logger.info(
                "context.search_stats",
                extra={"search_stats": ctx.search_stats, "phone": msg.user_id},
            )

        # Implicit signal: detect if user is responding to a bot correction prompt (👎 flow)
        if user_text and settings.eval_auto_curate:
            prev_trace_id = await repository.get_latest_trace_id(msg.user_id)
            if prev_trace_id:
                prev_scores = await repository.get_trace_scores(prev_trace_id)
                prompted = any(s["name"] == "correction_prompted" for s in prev_scores)
                received = any(s["name"] == "correction_received" for s in prev_scores)
                if prompted and not received:
                    await repository.save_trace_score(
                        trace_id=prev_trace_id,
                        name="correction_received",
                        value=1.0,
                        source="human",
                        comment=user_text[:200],
                    )
                    prev_io = await repository.get_trace_io_by_id(prev_trace_id)
                    _track_task(
                        asyncio.create_task(
                            add_correction_pair(
                                previous_trace_id=prev_trace_id,
                                input_text=(prev_io[0] if prev_io else ""),
                                bad_output=(prev_io[1] if prev_io else None),
                                correction_text=user_text,
                                repository=repository,
                            )
                        )
                    )
                    logger.info(
                        "Correction pair saved from reaction prompt for trace %s", prev_trace_id
                    )

        # Implicit signal: detect if user is correcting the bot's previous response
        if trace_ctx and user_text:
            correction_score = _detect_correction(user_text)
            if correction_score is not None:
                prev_trace_id = await repository.get_latest_trace_id(msg.user_id)
                if prev_trace_id:
                    await repository.save_trace_score(
                        trace_id=prev_trace_id,
                        name="user_correction",
                        value=correction_score,
                        source="system",
                        comment=f"Pattern detected in: {user_text[:80]}",
                    )
                    logger.debug(
                        "Correction detected (score=%.1f) for trace %s",
                        correction_score,
                        prev_trace_id,
                    )
                    if correction_score == 0.0 and settings.eval_auto_curate:
                        prev_trace = await repository.get_trace_with_spans(prev_trace_id)
                        _track_task(
                            asyncio.create_task(
                                add_correction_pair(
                                    previous_trace_id=prev_trace_id,
                                    input_text=(prev_trace["input_text"] if prev_trace else ""),
                                    bad_output=(prev_trace["output_text"] if prev_trace else None),
                                    correction_text=user_text,
                                    repository=repository,
                                )
                            )
                        )

        # Implicit signal: detect repeated question (semantic similarity > 0.9 vs last 24h)
        if trace_ctx and query_embedding:
            if await _is_repeated_question(query_embedding, conv_id, repository):
                await trace_ctx.add_score(
                    name="repeated_question",
                    value=0.0,
                    source="system",
                    comment="High similarity to recent message (>0.9)",
                )

        # Phase C: await classify_task + use ctx routing state
        pre_classified: list[str] | None = None
        sticky_categories: list[str] = ctx.sticky_categories
        user_facts: dict[str, str] = ctx.user_facts

        if classify_task is not None:
            try:
                base_result = await classify_task
                needs_context_upgrade = False

                if base_result == ["none"] and (history or sticky_categories):
                    # Stage 1 returned "none" — re-classify with context
                    needs_context_upgrade = True
                elif sticky_categories and base_result != ["none"]:
                    # Stage 1 classified confidently, but sticky categories exist —
                    # check if sticky categories overlap; if not, re-classify with
                    # context to avoid losing continuity (e.g. user says "busca en el
                    # contenido" while still working on a GitHub repo review).
                    if not set(sticky_categories) & set(base_result):
                        needs_context_upgrade = True

                if needs_context_upgrade:
                    pre_classified = await classify_intent(
                        user_text,
                        ollama_client,
                        recent_messages=history,
                        sticky_categories=sticky_categories or None,
                    )
                else:
                    pre_classified = base_result
            except Exception:
                logger.warning("classify_intent task failed, executor will retry", exc_info=True)

        # Update Langfuse trace tags with intent categories (best-effort, upsert)
        if trace_ctx and recorder and pre_classified and pre_classified != ["none"]:
            try:
                _platform_tag = "telegram" if msg.user_id.startswith("tg_") else "whatsapp"
                await recorder.update_trace_tags(
                    trace_ctx.trace_id,
                    [_platform_tag] + pre_classified,
                )
            except Exception:
                logger.debug("update_trace_tags failed", exc_info=True)

        # Capabilities summary (sync, fast) — built AFTER classify so we can filter by category.
        # Skip entirely when classify returned "none" (plain chat, no tools needed).
        if not has_tools or pre_classified == ["none"]:
            skills_summary: str | None = None
            logger.debug(
                "context.capabilities: skipped (has_tools=%s, categories=%s)",
                has_tools,
                pre_classified,
            )
        elif pre_classified:
            skills_summary = _build_capabilities_for_categories(
                pre_classified, skill_registry, command_registry, mcp_manager
            )
            logger.debug("context.capabilities: filtered to categories %s", pre_classified)
        else:
            # pre_classified is None (classification failed) — include full capabilities
            skills_summary = _build_capabilities_section(
                skill_registry, command_registry, mcp_manager
            )

        # Fallback notification: if user sent a URL but Puppeteer is unavailable,
        # inject a system note so the LLM can inform the user transparently.
        _url_pattern = _re.compile(r"https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+")
        _has_url_in_msg = bool(_url_pattern.search(user_text or ""))
        _fetch_mode = mcp_manager.get_fetch_mode() if mcp_manager else "unavailable"
        if _has_url_in_msg and _fetch_mode == "mcp-fetch":
            logger.info("URL fetch request using mcp-fetch fallback (Puppeteer unavailable)")
        _mcp_fetch_note: str | None = (
            "NOTA DEL SISTEMA: Puppeteer no está disponible. "
            "Estás usando fetch básico (sin renderizado JavaScript). "
            "Informa brevemente al usuario de esto en tu respuesta."
            if _has_url_in_msg and _fetch_mode == "mcp-fetch"
            else None
        )

        # Phase D: build context (sync) → main LLM call (~3-8s)
        context = _build_context(
            system_prompt_with_date,
            memories,
            relevant_notes,
            daily_logs,
            skills_summary,
            summary,
            history,
            projects_summary=projects_summary,
        )

        # Inject mcp-fetch fallback note after context is built (append as system message)
        if _mcp_fetch_note:
            context.append(ChatMessage(role="system", content=_mcp_fetch_note))

        # Token budget tracking (logging only, fail-open)
        try:
            from app.context.token_estimator import (
                estimate_sections,
                log_context_budget,
                log_context_budget_breakdown,
            )

            estimated_tokens = log_context_budget(
                context,
                extra={"phone": msg.user_id, "categories": pre_classified},
            )
            ctx.token_estimate = estimated_tokens

            # Breakdown: system_prompt (first message) vs history (rest)
            system_text = context[0].content if context else ""
            history_text = " ".join(m.content or "" for m in context[1:])
            sections = estimate_sections({"system_prompt": system_text, "history": history_text})
            log_context_budget_breakdown(sections)
        except Exception:
            logger.debug("Token budget estimation failed", exc_info=True)

        # Set current user context for tools that need it (e.g. scheduler, projects)
        from app.skills.tools.conversation_tools import (
            set_current_user as set_conversation_user,
        )
        from app.skills.tools.project_tools import set_current_user as set_project_user
        from app.skills.tools.scheduler_tools import set_current_user

        set_current_user(msg.user_id, received_at=now)
        set_project_user(msg.user_id)
        set_conversation_user(msg.user_id)

        try:
            if trace_ctx:
                if has_tools:
                    async with trace_ctx.span("tool_loop", kind="span") as loop_span:
                        loop_span.set_input({"categories": pre_classified})
                        reply = await execute_tool_loop(
                            context,
                            ollama_client,
                            skill_registry,
                            mcp_manager=mcp_manager,
                            max_tools=settings.max_tools_per_call,
                            pre_classified_categories=pre_classified,
                            user_facts=user_facts or None,
                            recent_messages=history,
                            sticky_categories=sticky_categories or None,
                            parent_span_id=loop_span.span_id,
                        )
                        loop_span.set_output({"reply_preview": reply[:100]})
                else:
                    async with trace_ctx.span("llm:chat", kind="generation") as gen_span:
                        gen_span.set_input({"message_count": len(context)})
                        response = await ollama_client.chat_with_tools(context, tools=None)
                        gen_span.set_metadata(
                            {
                                "gen_ai.usage.input_tokens": response.input_tokens,
                                "gen_ai.usage.output_tokens": response.output_tokens,
                                "gen_ai.request.model": response.model,
                            }
                        )
                        reply = response.content
                        gen_span.set_output({"reply_preview": reply[:100]})
            else:
                if has_tools:
                    reply = await execute_tool_loop(
                        context,
                        ollama_client,
                        skill_registry,
                        mcp_manager=mcp_manager,
                        max_tools=settings.max_tools_per_call,
                        pre_classified_categories=pre_classified,
                        user_facts=user_facts or None,
                        recent_messages=history,
                        sticky_categories=sticky_categories or None,
                    )
                else:
                    reply = await ollama_client.chat(context)
        except Exception:
            logger.exception("Ollama chat failed")
            reply = "Sorry, I'm having trouble processing your message right now. Please try again later."

        # Persist sticky categories for next turn
        # If tools were used, save the categories so follow-up messages can continue in context.
        # If no tools were used, clear them so stale categories don't bleed into unrelated turns.
        if conv_id:
            try:
                tools_used = pre_classified and pre_classified not in (["none"], [])
                if tools_used:
                    _track_task(
                        asyncio.create_task(
                            repository.save_sticky_categories(conv_id, pre_classified)
                        )
                    )
                else:
                    _track_task(asyncio.create_task(repository.clear_sticky_categories(conv_id)))
            except Exception:
                logger.debug("Could not save sticky_categories", exc_info=True)

        # Failed guardrail check names — populated below, used for dataset curation tags
        failed_checks_for_curation: list[str] = []

        # Guardrail pipeline (between LLM and WA delivery)
        if settings.guardrails_enabled:
            from app.guardrails.pipeline import run_guardrails

            tools_were_used = (
                has_tools and pre_classified is not None and pre_classified != ["none"]
            )
            if trace_ctx:
                async with trace_ctx.span("guardrails", kind="guardrail") as span:
                    guardrail_report = await run_guardrails(
                        user_text=user_text,
                        reply=reply,
                        tool_calls_used=tools_were_used,
                        settings=settings,
                        ollama_client=ollama_client,
                    )
                    span.set_metadata(
                        {
                            "passed": guardrail_report.passed,
                            "latency_ms": guardrail_report.total_latency_ms,
                            "failed_checks": [
                                r.check_name for r in guardrail_report.results if not r.passed
                            ],
                        }
                    )
            else:
                guardrail_report = await run_guardrails(
                    user_text=user_text,
                    reply=reply,
                    tool_calls_used=tools_were_used,
                    settings=settings,
                    ollama_client=ollama_client,
                )

            if not guardrail_report.passed:
                reply = await _handle_guardrail_failure(
                    guardrail_report,
                    context,
                    ollama_client,
                    reply,
                    trace_ctx=trace_ctx,
                )

            # Record guardrail results as trace scores
            if trace_ctx:
                for gr in guardrail_report.results:
                    await trace_ctx.add_score(
                        name=gr.check_name,
                        value=1.0 if gr.passed else 0.0,
                        source="system",
                    )

            # Self-correction memory: persist guardrail failures so the LLM learns
            if not guardrail_report.passed:
                failed_checks = [r.check_name for r in guardrail_report.results if not r.passed]
                failed_checks_for_curation = failed_checks  # propagate to dataset curation
                _track_task(
                    asyncio.create_task(
                        _save_self_correction_memory(
                            user_text=user_text,
                            failed_checks=failed_checks,
                            repository=repository,
                            memory_file=memory_file,
                            ollama_client=ollama_client,
                            embed_model=(
                                settings.embedding_model
                                if settings.semantic_search_enabled and vec_available
                                else None
                            ),
                            vec_available=vec_available,
                        )
                    )
                )

        await repository.save_message(conv_id, "assistant", reply)

        if trace_ctx:
            trace_ctx.set_output(reply)

        try:
            if trace_ctx:
                async with trace_ctx.span("delivery") as span:
                    sent_message_id = await platform_client.send_message(
                        msg.user_id,
                        platform_client.format_text(reply),
                    )
                    if sent_message_id:
                        trace_ctx.set_wa_message_id(sent_message_id)
                        span.set_metadata({"message_id": sent_message_id})
            else:
                await platform_client.send_message(msg.user_id, platform_client.format_text(reply))
        except Exception:
            logger.exception("Failed to send platform message")

        # First-message welcome: send a brief follow-up mentioning /setup
        if profile_row["message_count"] == 0 and settings.onboarding_enabled and not in_onboarding:
            try:
                await platform_client.send_message(
                    msg.user_id,
                    platform_client.format_text(
                        "Tip: you can run /setup anytime to personalize me "
                        "(your name, what you do, how to call me).\n\n"
                        "Tip: podés usar /setup en cualquier momento para personalizarme "
                        "(tu nombre, a qué te dedicás, cómo llamarme)."
                    ),
                )
            except Exception:
                logger.debug("Failed to send first-message tip", exc_info=True)

        # Increment profile message count and maybe run progressive discovery
        if settings.onboarding_enabled:
            new_count = await repository.increment_profile_message_count(msg.user_id)
            _track_task(
                asyncio.create_task(
                    maybe_discover_profile_updates(
                        msg.user_id,
                        new_count,
                        settings.profile_discovery_interval,
                        repository,
                        ollama_client,
                        settings,
                    )
                )
            )

        # Auto-curate completed trace to eval dataset (best-effort, background)
        if trace_ctx and settings.eval_auto_curate:
            _track_task(
                asyncio.create_task(
                    maybe_curate_to_dataset(
                        trace_id=trace_ctx.trace_id,
                        input_text=user_text,
                        output_text=reply,
                        repository=repository,
                        failed_check_names=failed_checks_for_curation or None,
                        trace_recorder=recorder,
                    )
                )
            )

        # Summarize in background if needed
        _track_task(
            asyncio.create_task(
                maybe_summarize(
                    conversation_id=conv_id,
                    repository=repository,
                    ollama_client=ollama_client,
                    threshold=settings.summary_threshold,
                    max_messages=settings.conversation_max_messages,
                    daily_log=daily_log,
                    memory_file=memory_file,
                    flush_enabled=settings.memory_flush_enabled,
                    embed_model=(
                        settings.embedding_model
                        if settings.semantic_search_enabled and vec_available
                        else None
                    ),
                )
            )
        )

    if _trace_enabled:
        _platform_tag = "telegram" if msg.user_id.startswith("tg_") else "whatsapp"
        async with TraceContext(
            msg.user_id,
            user_text,
            recorder,
            message_type=_msg_type,
            platform=_platform_tag,
        ) as trace_ctx:
            await _run_normal_flow(trace_ctx)
    else:
        await _run_normal_flow(None)
