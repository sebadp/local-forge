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

    # Ontology: entity registry (knowledge graph)
    from app.ontology.registry import EntityRegistry

    entity_registry = EntityRegistry(db_conn)
    app.state.entity_registry = entity_registry

    # Provenance: audit logger (best-effort mutation tracking)
    from app.provenance.audit import AuditLogger

    audit_logger = AuditLogger(db_conn, enabled=settings.provenance_enabled)
    app.state.audit_logger = audit_logger

    from app.provenance.context import set_audit_logger

    set_audit_logger(audit_logger)

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

        # Sync default prompts to Langfuse (idempotent, best-effort)
        if settings.prompt_versioning_enabled and app.state.trace_recorder.langfuse:
            try:
                from app.eval.prompt_registry import PROMPT_DEFAULTS

                for pname, pcontent in PROMPT_DEFAULTS.items():
                    await app.state.trace_recorder.upsert_prompt(
                        name=pname, content=pcontent, labels=["default"]
                    )
                logger.info("Synced %d default prompts to Langfuse", len(PROMPT_DEFAULTS))
            except Exception:
                logger.warning("Failed to sync default prompts to Langfuse", exc_info=True)
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

    # Provenance tool registration
    if settings.provenance_enabled:
        from app.provenance.lineage_tool import register as register_provenance

        register_provenance(skill_registry, audit_logger)

    # Background tasks — declared here so cleanup block can always reference them
    import asyncio as _asyncio

    _ontology_backfill_task: _asyncio.Task[None] | None = None
    _backfill_task: _asyncio.Task[None] | None = None

    # Ontology tool registration + backfill
    if settings.ontology_enabled:
        from app.skills.tools.ontology_tools import register as register_ontology

        register_ontology(skill_registry, entity_registry)

        async def _safe_ontology_backfill() -> None:
            try:
                from app.ontology.backfill import run_full_backfill

                counts = await run_full_backfill(db_conn, entity_registry)
                logger.info("Ontology backfill completed: %s", counts)
            except Exception:
                logger.warning("Ontology backfill failed (non-critical)", exc_info=True)

        _ontology_backfill_task = _asyncio.create_task(_safe_ontology_backfill())

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
    _startup_cleanup_task = _asyncio.create_task(_cleanup_self_corrections())

    # Auto-Dream: background memory consolidation (Plan 53)
    if settings.dream_enabled:
        from pathlib import Path as _Path

        from app.memory.consolidation_lock import should_dream as _should_dream
        from app.memory.dream import run_dream as _run_dream

        _dream_data_dir = _Path(settings.memory_dir).parent  # data/
        _dream_repo = repository
        _dream_ollama = app.state.ollama_client
        _dream_memory_file = memory_file
        _dream_daily_log = daily_log
        _dream_settings = settings

        async def _dream_job() -> None:
            try:
                if not await _should_dream(
                    _dream_data_dir,
                    _dream_repo,
                    interval_hours=_dream_settings.dream_interval_hours,
                    min_messages=_dream_settings.dream_min_messages,
                ):
                    return
                from app.tracing.context import TraceContext

                async with TraceContext("dream_consolidation"):
                    result = await _run_dream(
                        _dream_repo,
                        _dream_ollama,
                        _dream_memory_file,
                        _dream_daily_log,
                        _dream_data_dir,
                    )
                if result.error:
                    logger.warning("Auto-dream completed with error: %s", result.error)
                else:
                    logger.info(
                        "Auto-dream completed: removed=%d, updated=%d, created=%d",
                        result.removed,
                        result.updated,
                        result.created,
                    )
            except Exception:
                logger.exception("Auto-dream job failed (non-critical)")

        scheduler.add_job(
            _dream_job,
            trigger="interval",
            hours=settings.dream_interval_hours,
            id="auto_dream",
            replace_existing=True,
        )
        logger.info(
            "Auto-dream scheduled (interval=%dh, min_messages=%d)",
            settings.dream_interval_hours,
            settings.dream_min_messages,
        )

    # Operational Automation (Plan 47): data-driven triggers
    if settings.automation_enabled:
        from app.automation.builtin_rules import seed_builtin_rules
        from app.automation.evaluator import evaluate_rules as _evaluate_automation

        await seed_builtin_rules(repository)

        # Pick the right platform client for notifications
        _auto_platform_client: object | None = None
        if settings.automation_admin_phone.startswith("tg_"):
            _auto_platform_client = getattr(app.state, "telegram_client", None)
        else:
            _auto_platform_client = app.state.whatsapp_client

        # Capture in closure for the scheduler job
        _auto_repo = repository
        _auto_settings = settings
        _auto_ollama = app.state.ollama_client
        _auto_pclient = _auto_platform_client

        async def _run_automation() -> None:
            try:
                count = await _evaluate_automation(
                    _auto_repo,
                    platform_client=_auto_pclient,
                    admin_phone=_auto_settings.automation_admin_phone,
                    user_phone=(
                        _auto_settings.allowed_phone_numbers[0]
                        if _auto_settings.allowed_phone_numbers
                        else ""
                    ),
                    ollama_client=_auto_ollama,
                    embed_model=_auto_settings.embedding_model,
                    vec_available=vec_available,
                )
                if count:
                    logger.info("Automation evaluator: %d rule(s) triggered", count)
            except Exception:
                logger.exception("Automation evaluator job failed")

        scheduler.add_job(
            _run_automation,
            trigger="interval",
            minutes=settings.automation_interval_minutes,
            id="automation_evaluator",
            replace_existing=True,
        )
        logger.info(
            "Operational automation enabled (interval=%d min, %d built-in rules)",
            settings.automation_interval_minutes,
            len(await repository.get_all_automation_rules()),
        )

    # Scheduled eval regression (Plan 62)
    if settings.eval_scheduled_enabled:
        _eval_settings = settings
        _eval_repo = repository

        async def _scheduled_eval() -> None:
            """Run regression eval and persist score; alert if below threshold."""
            try:
                from scripts.run_eval import _run_eval

                exit_code = await _run_eval(
                    db_path=_eval_settings.database_path,
                    ollama_url=_eval_settings.ollama_base_url,
                    model=_eval_settings.ollama_model,
                    mode=_eval_settings.eval_scheduled_mode,
                    entry_type=None,
                    limit=100,
                    threshold=_eval_settings.eval_scheduled_threshold,
                )
                # Persist score
                accuracy_pass = exit_code == 0
                await _eval_repo.save_trace_score(
                    trace_id=f"eval_scheduled_{_eval_settings.eval_scheduled_mode}",
                    name=f"eval_regression_{_eval_settings.eval_scheduled_mode}",
                    value=1.0 if accuracy_pass else 0.0,
                    source="system",
                    comment=f"scheduled eval exit_code={exit_code}",
                )

                if not accuracy_pass:
                    admin_phone = _eval_settings.automation_admin_phone
                    if admin_phone and hasattr(app.state, "whatsapp_client"):
                        await app.state.whatsapp_client.send_message(
                            admin_phone,
                            f"⚠️ Scheduled eval FAILED (mode={_eval_settings.eval_scheduled_mode}, "
                            f"threshold={_eval_settings.eval_scheduled_threshold:.0%}). "
                            f"Run `make eval-{_eval_settings.eval_scheduled_mode}` for details.",
                        )
                        logger.warning("Scheduled eval FAILED, alert sent to %s", admin_phone)
                    else:
                        logger.warning("Scheduled eval FAILED (no admin phone for alert)")
            except Exception:
                logger.exception("Scheduled eval job failed")

        from apscheduler.triggers.cron import CronTrigger as _EvalCronTrigger

        scheduler.add_job(
            _scheduled_eval,
            trigger=_EvalCronTrigger(hour=settings.eval_scheduled_hour),
            id="scheduled_eval",
            replace_existing=True,
        )
        logger.info(
            "Scheduled eval enabled (hour=%d UTC, mode=%s, threshold=%.0f%%)",
            settings.eval_scheduled_hour,
            settings.eval_scheduled_mode,
            settings.eval_scheduled_threshold * 100,
        )

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

        _backfill_task = _asyncio.create_task(_safe_backfill())

    yield

    # Cancel the embedding backfill before tearing down DB/HTTP so it doesn't
    # try to use already-closed resources.
    for _bg_task in (_ontology_backfill_task, _backfill_task):
        if _bg_task is not None and not _bg_task.done():
            _bg_task.cancel()
            try:
                await _bg_task
            except _asyncio.CancelledError:
                pass

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
