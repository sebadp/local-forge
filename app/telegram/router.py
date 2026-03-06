from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import Response

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
)
from app.telegram.parser import extract_telegram_messages
from app.webhook.router import _track_task, process_message_generic  # noqa: F401

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    settings = get_settings(request)

    if not settings.telegram_enabled:
        return Response(status_code=200)

    # Validate secret token
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if settings.telegram_webhook_secret and secret != settings.telegram_webhook_secret:
        logger.warning("Invalid Telegram webhook secret token")
        raise HTTPException(status_code=403)

    payload = await request.json()
    messages = extract_telegram_messages(payload)

    if not messages:
        return Response(status_code=200)

    telegram_client = getattr(request.app.state, "telegram_client", None)
    if telegram_client is None:
        logger.warning("Telegram message received but client not initialized")
        return Response(status_code=200)

    repository = get_repository(request)
    rate_limiter = get_rate_limiter(request)
    ollama_client = get_ollama_client(request)
    conversation = get_conversation_manager(request)
    command_registry = get_command_registry(request)
    memory_file = get_memory_file(request)
    daily_log = get_daily_log(request)
    transcriber = get_transcriber(request)
    skill_registry = get_skill_registry(request)
    mcp_manager = get_mcp_manager(request)
    vec_available = get_vec_available(request)
    trace_recorder = get_trace_recorder(request)

    for msg in messages:
        logger.info(
            "Telegram incoming [%s] (%s): %s",
            msg.user_id,
            msg.type,
            msg.text[:80] if msg.text else "(empty)",
        )

        # Whitelist check
        chat_id = msg.user_id.removeprefix("tg_")
        allowed = settings.allowed_telegram_chat_ids
        if allowed and chat_id not in allowed:
            logger.warning("Message from non-whitelisted Telegram chat: %s", chat_id)
            continue

        # Rate limiting
        if not rate_limiter.is_allowed(msg.user_id):
            logger.warning("Rate limit exceeded for Telegram user %s", msg.user_id)
            continue

        # Dedup
        if not await repository.try_claim_message(msg.message_id):
            logger.info("Duplicate Telegram message ignored: %s", msg.message_id)
            continue

        background_tasks.add_task(
            process_message_generic,
            msg=msg,
            platform_client=telegram_client,
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

    return Response(status_code=200)
