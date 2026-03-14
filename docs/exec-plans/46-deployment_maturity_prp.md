# PRP: Deployment Maturity

> **PRD**: [`46-deployment_maturity_prd.md`](46-deployment_maturity_prd.md)

## Archivos a Modificar

### Nuevos
- `.github/workflows/release.yml`: Build + push imagen a ghcr.io en tags `v*`

### Modificados
- `app/health/router.py`: Refactor `/health` como liveness, agregar `/ready` con DB + Ollama
- `app/models.py`: Agregar `ReadinessChecks`, `ReadinessResponse`; eliminar `OllamaCheck` (unused)
- `app/database/repository.py`: `ping()` method (`SELECT 1`)
- `Dockerfile`: `curl` en apt-get, `HEALTHCHECK` directive, `LABEL` con source
- `docker-compose.yml`: Profiles `dev`/`prod`, healthcheck en localforge
- `.github/workflows/ci.yml`: Branch `master` → `main`
- `tests/test_health.py`: Tests para liveness + readiness

## Fases de Implementación

### Phase 1: Health endpoints (readiness)
- [x] Agregar `ping()` en `Repository` — `await self._conn.execute("SELECT 1")`
- [x] Refactor `/health` a liveness-only (return `{"status": "ok"}` sin dependency checks)
- [x] Crear `/ready` endpoint — checks: DB (`repository.ping()`) + Ollama (`is_available()`)
- [x] Retornar 200 si ambos OK, 503 si alguno falla
- [x] Agregar `ReadinessChecks`, `ReadinessResponse` models; eliminar `OllamaCheck` (unused)

### Phase 2: Docker hardening
- [x] Agregar `curl` al `apt-get install` en Dockerfile
- [x] Agregar `HEALTHCHECK` en Dockerfile: `--interval=30s --timeout=5s --start-period=60s --retries=3`
- [x] Agregar `LABEL` con source metadata

### Phase 3: Compose profiles
- [x] Agregar profiles `dev`/`prod` a todos los servicios
- [x] `ollama` y `ngrok`: solo profile `dev`
- [x] `localforge`, `langfuse-*`: profiles `dev` + `prod`
- [x] Agregar `healthcheck` config en service `localforge`

### Phase 4: CI/CD — Release workflow
- [x] Crear `.github/workflows/release.yml` (build+push ghcr.io en tags `v*`)
- [x] Actualizar `.github/workflows/ci.yml`: branch `master` → `main`

### Phase 5: Tests
- [x] `test_health_liveness` — liveness siempre 200
- [x] `test_ready_all_ok` — DB + Ollama OK → 200
- [x] `test_ready_ollama_down` — Ollama down → 503 degraded
- [x] `test_ready_db_down` — DB down → 503 degraded
- [x] `test_ready_both_down` — ambos down → 503 degraded
- [x] Full test suite: 755 passed, lint OK, mypy OK

### Phase 6: Documentación
- [x] Actualizar `CLAUDE.md`
- [x] Actualizar `docs/exec-plans/README.md`
