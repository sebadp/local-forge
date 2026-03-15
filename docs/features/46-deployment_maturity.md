# Feature: Deployment Maturity

> **Versión**: v1.0
> **Fecha de implementación**: 2026-03-12
> **Exec Plan**: 46
> **Estado**: ✅ Implementada

---

## ¿Qué hace?

Agrega health checks (liveness + readiness), Docker healthcheck, compose profiles (dev/prod), y un workflow de CI/CD para publicar imágenes versionadas a GitHub Container Registry en cada release.

---

## Arquitectura

```
[Docker / Orchestrator]
        │
        ├──► GET /health  (liveness: proceso vivo → siempre 200)
        │
        ├──► GET /ready   (readiness: DB + Ollama → 200 ó 503)
        │
        ▼
[Dockerfile HEALTHCHECK]
        │ curl -f http://localhost:8000/health
        │ interval=30s, start-period=60s, retries=3
        │
        ▼
[Docker restart policy: unless-stopped]
        │ Container unhealthy → Docker lo reinicia
        │
[GitHub Actions: release.yml]
        │ Push tag v* → Build → Push ghcr.io/repo:tag + :latest
```

---

## Archivos clave

| Archivo | Rol |
|---|---|
| `app/health/router.py` | Endpoints `/health` (liveness) y `/ready` (readiness) |
| `app/models.py` | `HealthResponse`, `ReadinessChecks`, `ReadinessResponse` |
| `app/database/repository.py` | `ping()` — `SELECT 1` para DB check |
| `Dockerfile` | `HEALTHCHECK`, `curl` install, `LABEL` |
| `docker-compose.yml` | Profiles `dev`/`prod`, healthcheck config |
| `.github/workflows/release.yml` | Build + push a ghcr.io en tags `v*` |
| `.github/workflows/ci.yml` | CI actualizado a branch `main` |
| `tests/test_health.py` | 5 tests (liveness + readiness scenarios) |

---

## Walkthrough técnico: cómo funciona

### Health endpoints

1. **`GET /health`** — Liveness probe. Retorna `{"status": "ok"}` con 200. No verifica dependencias. Si este endpoint falla, el proceso está muerto → `router.py:10`
2. **`GET /ready`** — Readiness probe. Ejecuta `repository.ping()` (SELECT 1) y `ollama_client.is_available()` (GET /api/tags). Si ambos OK → 200. Si alguno falla → 503 con detalle del error → `router.py:15`

### Docker integration

3. **Dockerfile HEALTHCHECK**: `curl -f http://localhost:8000/health` cada 30s. Si falla 3 veces consecutivas → container marcado `unhealthy` → Docker restart policy lo levanta → `Dockerfile:51`
4. **Start period**: 60s de gracia al inicio para que Ollama cargue modelos antes de empezar health checks

### Compose profiles

5. **`docker compose --profile dev up`**: levanta todo (localforge + Ollama + ngrok + Langfuse)
6. **`docker compose --profile prod up`**: solo localforge + Langfuse (Ollama y ngrok son externos en producción)

### Release workflow

7. **Push tag `v1.2.3`** → GitHub Actions: checkout → login ghcr.io → build Docker image → push con tags `:v1.2.3` + `:latest`

---

## Cómo extenderla

- **Agregar más checks a `/ready`**: agregar un bloque `try/except` en `ready()` para el nuevo servicio (e.g. Redis, MCP)
- **Cambiar health check interval**: modificar `HEALTHCHECK --interval=` en Dockerfile o `healthcheck.interval` en docker-compose.yml
- **Agregar servicio nuevo al compose**: asignar el profile correcto (`dev`, `prod`, o ambos)
- **Rollback de versión**: `docker pull ghcr.io/sebadp/localforge:v1.2.2` → `docker compose --profile prod up -d`

---

## Guía de testing

→ Ver [`docs/testing/46-deployment_maturity_testing.md`](../testing/46-deployment_maturity_testing.md)

---

## Decisiones de diseño

| Decisión | Alternativa descartada | Motivo |
|---|---|---|
| `/health` sin dependency checks | `/health` con Ollama check (diseño anterior) | Liveness debe ser barato y siempre responder — un Ollama caído no significa que el proceso esté muerto |
| `/ready` separado | Todo en `/health` | Separation of concerns: liveness ≠ readiness. Kubernetes/Docker usan ambos de forma diferente |
| `curl` en HEALTHCHECK | Python script custom | `curl` es más simple, más rápido, y no carga el runtime de Python |
| Profiles en compose | Archivos compose separados | Profiles es el mecanismo nativo de Docker Compose, más mantenible |
| ghcr.io (GitHub Container Registry) | Docker Hub | Integración nativa con GitHub Actions, no requiere cuenta extra |
| CI branch `main` (no `master`) | Mantener `master` | El repo usa `main` como branch principal |

---

## Gotchas y edge cases

- **Ollama startup lento**: el `start-period=60s` del HEALTHCHECK da tiempo a Ollama para cargar modelos. Si los modelos son muy grandes, puede necesitar más tiempo
- **Network mode host**: el compose usa `network_mode: host`, así que los health checks usan `localhost` directamente
- **`/ready` latencia**: cada check tiene timeout implícito (Ollama `is_available()` usa timeout=5s). El endpoint debería responder en <100ms en estado normal
- **Profiles obligatorios**: con profiles configurados, `docker compose up` sin `--profile` no levanta nada. Siempre usar `--profile dev` o `--profile prod`
- **GPU override**: `docker-compose.gpu.yml` se usa como override file: `docker compose -f docker-compose.yml -f docker-compose.gpu.yml --profile dev up`

---

## Variables de configuración relevantes

| Variable | Default | Efecto |
|---|---|---|
| N/A | — | Los health endpoints no tienen configuración — siempre activos |

La feature no introduce variables de configuración nuevas. Los endpoints están siempre disponibles.
