import logging
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import httpx
from fastapi import FastAPI

from app.audio.transcriber import Transcriber
from app.commands.builtins import register_builtins
from app.commands.registry import CommandRegistry
from app.config import Settings
from app.conversation.manager import ConversationManager
from app.database.db import init_db
from app.database.repository import Repository
from app.health.router import router as health_router
from app.llm.client import OllamaClient
from app.logging_config import configure_logging
from app.memory.daily_log import DailyLog
from app.memory.markdown import MemoryFile
from app.models import ChatMessage
from app.skills.registry import SkillRegistry
from app.skills.tools import register_builtin_tools
from app.telegram.router import router as telegram_router
from app.webhook.rate_limiter import RateLimiter
from app.webhook.router import router as webhook_router
from app.webhook.router import wait_for_in_flight
from app.whatsapp.client import WhatsAppClient

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()  # type: ignore[call-arg]

    configure_logging(level=settings.log_level, json_format=settings.log_json)

    http_client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0))

    # Database
    Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
    db_conn, vec_available = await init_db(
        settings.database_path,
        embedding_dims=settings.embedding_dimensions,
    )
    repository = Repository(db_conn)

    # Memory
    memory_file = MemoryFile(path="data/MEMORY.md")
    daily_log = DailyLog(memory_dir=settings.memory_dir)

    # Command registry
    command_registry = CommandRegistry()
    register_builtins(command_registry)

    app.state.vec_available = vec_available
    app.state.settings = settings
    app.state.http_client = http_client
    app.state.rate_limiter = RateLimiter(
        max_requests=settings.rate_limit_max,
        window_seconds=settings.rate_limit_window,
    )
    app.state.whatsapp_client = WhatsAppClient(
        http_client=http_client,
        access_token=settings.whatsapp_access_token,
        phone_number_id=settings.whatsapp_phone_number_id,
    )

    # Telegram (optional — only if telegram_enabled=true and token provided)
    if settings.telegram_enabled and settings.telegram_bot_token:
        from app.telegram.client import TelegramClient

        tg_client = TelegramClient(http_client, settings.telegram_bot_token)
        app.state.telegram_client = tg_client
        if settings.telegram_webhook_url:
            await tg_client.set_webhook(
                settings.telegram_webhook_url + "/telegram/webhook",
                settings.telegram_webhook_secret or None,
            )
        logger.info("Telegram integration enabled")
    else:
        app.state.telegram_client = None
    app.state.ollama_client = OllamaClient(
        http_client=http_client,
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
    )
    app.state.repository = repository

    # Prompt registry: seed default prompts into DB at startup (idempotent)
    if settings.prompt_versioning_enabled:
        from app.eval.prompt_registry import PROMPT_DEFAULTS

        await repository.seed_default_prompts(PROMPT_DEFAULTS)

    # TraceRecorder singleton: one Langfuse client for the lifetime of the app
    if settings.tracing_enabled:
        from app.tracing.recorder import TraceRecorder

        app.state.trace_recorder = TraceRecorder.create(repository)
    else:
        app.state.trace_recorder = None

    app.state.memory_file = memory_file
    app.state.daily_log = daily_log
    app.state.command_registry = command_registry
    app.state.conversation_manager = ConversationManager(
        repository=repository,
        max_messages=settings.conversation_max_messages,
    )
    app.state.transcriber = Transcriber(
        model_size=settings.whisper_model,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
    )

    # MCP Manager (initialized before skills so expand tools can reference it)
    from app.mcp.manager import McpManager

    mcp_manager = McpManager(config_path=settings.mcp_config_path)
    await mcp_manager.initialize()
    app.state.mcp_manager = mcp_manager

    # Skills
    skill_registry = SkillRegistry(skills_dir=settings.skills_dir)
    skill_registry.load_skills()

    from app.skills.tools import conversation_tools

    conversation_tools.register(skill_registry, repository)

    register_builtin_tools(
        skill_registry,
        repository,
        ollama_client=app.state.ollama_client,
        embed_model=settings.embedding_model
        if settings.semantic_search_enabled and vec_available
        else None,
        vec_available=vec_available,
        settings=settings,
        mcp_manager=mcp_manager,
        daily_log=daily_log,
    )
    app.state.skill_registry = skill_registry

    # Scheduler Skill
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from app.skills.tools.scheduler_tools import set_repository, set_scheduler

    scheduler = AsyncIOScheduler()
    scheduler.start()
    _sched_clients: dict = {"whatsapp": app.state.whatsapp_client}
    if settings.telegram_enabled and app.state.telegram_client is not None:
        _sched_clients["telegram"] = app.state.telegram_client
    set_scheduler(scheduler, **_sched_clients)
    set_repository(repository)
    app.state.scheduler = scheduler

    # Restore persistent cron jobs from DB
    try:
        from zoneinfo import ZoneInfo

        from apscheduler.triggers.cron import CronTrigger

        active_crons = await repository.get_active_cron_jobs()
        for cron in active_crons:
            try:
                tz_obj = ZoneInfo(cron.get("timezone", "UTC"))
                trigger = CronTrigger.from_crontab(cron["cron_expr"], timezone=tz_obj)
                from app.skills.tools.scheduler_tools import _send_reminder

                scheduler.add_job(
                    _send_reminder,
                    trigger,
                    args=[cron["phone_number"], cron["message"]],
                    name=cron["message"],
                    id=f"cron_{cron['id']}",
                    replace_existing=True,
                )
            except Exception:
                logger.exception("Failed to restore cron job %s", cron.get("id"))
        if active_crons:
            logger.info("Restored %d cron job(s) from database", len(active_crons))
    except Exception:
        logger.exception("Cron job restore failed at startup")

    # Trace cleanup job: daily purge of traces older than trace_retention_days
    if settings.tracing_enabled:

        async def _cleanup_old_traces() -> None:
            try:
                deleted = await repository.cleanup_old_traces(days=settings.trace_retention_days)
                logger.info("Trace cleanup: deleted %d old traces", deleted)
            except Exception:
                logger.exception("Trace cleanup job failed")

        scheduler.add_job(
            _cleanup_old_traces,
            trigger="cron",
            hour=3,
            minute=0,
            id="trace_cleanup",
            replace_existing=True,
        )
        logger.info(
            "Scheduled trace cleanup job (daily at 03:00, retention=%d days)",
            settings.trace_retention_days,
        )

    # Self-correction memory cleanup: expire corrections older than 24h
    async def _cleanup_self_corrections() -> None:
        try:
            removed = await repository.cleanup_expired_self_corrections(ttl_hours=24)
            if removed:
                logger.info("Self-correction cleanup: expired %d old corrections", removed)
        except Exception:
            logger.exception("Self-correction cleanup job failed")

    scheduler.add_job(
        _cleanup_self_corrections,
        trigger="interval",
        hours=1,
        id="self_correction_cleanup",
        replace_existing=True,
    )
    # Also run once at startup to clean up pre-existing stale corrections
    import asyncio as _asyncio

    _startup_cleanup_task = _asyncio.create_task(_cleanup_self_corrections())

    # Memory file watcher (bidirectional sync)
    memory_watcher = None
    if settings.memory_file_watch_enabled:
        try:
            import asyncio

            from app.memory.watcher import MemoryWatcher

            memory_watcher = MemoryWatcher(
                memory_file=memory_file,
                repository=repository,
                loop=asyncio.get_event_loop(),
            )
            memory_file.set_watcher(memory_watcher)
            memory_watcher.start()
        except ImportError:
            import logging

            logging.getLogger(__name__).warning(
                "watchdog not installed, MEMORY.md file watching disabled. "
                "Install with: pip install watchdog"
            )
    app.state.memory_watcher = memory_watcher

    # Warmup: pre-load Ollama models to avoid cold-start on first message
    try:
        await asyncio.gather(
            app.state.ollama_client.embed(["warmup"], model=settings.embedding_model),
            app.state.ollama_client.chat_with_tools(
                [ChatMessage(role="user", content="hi")],
                think=False,
            ),
        )
        logger.info("Ollama models warmed up")
    except Exception:
        logger.warning("Model warmup failed (non-critical)", exc_info=True)

    # Backfill embeddings as background task — doesn't block request acceptance
    _backfill_task = None
    if vec_available and settings.semantic_search_enabled:

        async def _safe_backfill() -> None:
            from app.embeddings.indexer import (
                backfill_embeddings,
                backfill_note_embeddings,
                backfill_project_note_embeddings,
                embed_tool_descriptions,
            )
            from app.skills.executor import _get_cached_tools_map

            try:
                await backfill_embeddings(
                    repository,
                    app.state.ollama_client,
                    settings.embedding_model,
                )
                await backfill_note_embeddings(
                    repository,
                    app.state.ollama_client,
                    settings.embedding_model,
                )
                await backfill_project_note_embeddings(
                    repository,
                    app.state.ollama_client,
                    settings.embedding_model,
                )
                # Embed tool descriptions for semantic tool discovery (Tool RAG)
                tools_map = _get_cached_tools_map(skill_registry, mcp_manager)
                await embed_tool_descriptions(
                    tools_map,
                    repository,
                    app.state.ollama_client,
                    settings.embedding_model,
                )
                logger.info("Embedding backfill completed (background)")
            except Exception:
                logger.warning("Embedding backfill failed (non-critical)", exc_info=True)

        _backfill_task = asyncio.create_task(_safe_backfill())

    yield

    await wait_for_in_flight(timeout=30.0)
    if memory_watcher:
        memory_watcher.stop()
    scheduler.shutdown()
    await mcp_manager.cleanup()
    # Flush Langfuse before exit so buffered spans are not lost
    trace_recorder = getattr(app.state, "trace_recorder", None)
    if trace_recorder is not None and trace_recorder.langfuse is not None:
        trace_recorder.langfuse.flush()
    # Shield DB and HTTP close from any active anyio cancellation so
    # "address already in use" restarts don't leave connections dangling.
    with anyio.CancelScope(shield=True):
        await db_conn.close()
        await http_client.aclose()


app = FastAPI(title="LocalForge", lifespan=lifespan)
app.include_router(health_router)
app.include_router(webhook_router)
app.include_router(telegram_router)
