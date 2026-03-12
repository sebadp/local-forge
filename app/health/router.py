from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.models import HealthResponse, ReadinessChecks, ReadinessResponse

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
    except Exception as e:
        checks["db"] = f"error: {e}"

    # Ollama check
    try:
        available = await request.app.state.ollama_client.is_available()
        checks["ollama"] = "ok" if available else "error: not responding"
    except Exception as e:
        checks["ollama"] = f"error: {e}"

    all_ok = all(v == "ok" for v in checks.values())
    resp = ReadinessResponse(
        status="ok" if all_ok else "degraded",
        checks=ReadinessChecks(**checks),
    )
    return JSONResponse(
        content=resp.model_dump(),
        status_code=200 if all_ok else 503,
    )
