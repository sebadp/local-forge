import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.models import HealthResponse, ReadinessChecks, ReadinessResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness probe: process is alive."""
    return HealthResponse(status="ok")


@router.get("/ready", response_model=ReadinessResponse)
async def ready(request: Request) -> JSONResponse:
    """Readiness probe: DB + Ollama responding."""
    checks: dict[str, str] = {}

    # DB check
    try:
        await request.app.state.repository.ping()
        checks["db"] = "ok"
    except Exception:
        logger.warning("Readiness check: DB failed", exc_info=True)
        checks["db"] = "error: unavailable"

    # Ollama check
    try:
        available = await request.app.state.ollama_client.is_available()
        checks["ollama"] = "ok" if available else "error: not responding"
    except Exception:
        logger.warning("Readiness check: Ollama failed", exc_info=True)
        checks["ollama"] = "error: unavailable"

    all_ok = all(v == "ok" for v in checks.values())
    resp = ReadinessResponse(
        status="ok" if all_ok else "degraded",
        checks=ReadinessChecks(**checks),
    )
    return JSONResponse(
        content=resp.model_dump(),
        status_code=200 if all_ok else 503,
    )
