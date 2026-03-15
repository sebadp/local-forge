# PRD: Deployment Maturity

> **Origen:** Gap 2.4 del [Plan de Arquitectura](42-architecture_action_plan.md) (Palantir AIP Gap Analysis)
> **Independiente** — no depende de otros planes

## Objetivo y Contexto

El deployment actual es Docker + docker-compose sin health checks, secrets management, ni release channels. Esto significa:

- **No hay health checks**: si Ollama se cae o la DB se corrompe, el container sigue reportando "healthy"
- **Secrets en `.env`**: tokens de WhatsApp/Telegram, API keys en texto plano sin cifrar
- **Sin rollback**: no hay versionamiento de imágenes ni forma de volver a una versión anterior rápidamente
- **Sin staging**: cambios van directo a producción sin entorno de validación
- **Sin readiness probe**: el container acepta tráfico antes de que Ollama y la DB estén listos

Para un asistente personal que maneja conversaciones reales, la disponibilidad y la recuperación rápida son críticas.

## Alcance

### In Scope

1. **Health endpoints**: `GET /health` (liveness — proceso vivo) + `GET /ready` (readiness — DB + Ollama respondiendo)
2. **Docker health check**: `HEALTHCHECK` en Dockerfile usando `/health`
3. **Secrets management**: Docker secrets para producción + documentación de setup con SOPS para `.env` cifrado
4. **Image versioning**: tags semánticos (`v1.2.3`) + `latest` en GitHub Container Registry (ghcr.io)
5. **CI/CD pipeline**: GitHub Actions workflow: lint → test → build image → push → deploy (opcional)
6. **Compose profiles**: `docker-compose.yml` con profiles `dev` y `prod` (Ollama bundled vs external)
7. **Graceful shutdown**: verificar que `SIGTERM` drena requests in-flight antes de cerrar

### Out of Scope

- Kubernetes manifests (premature — single-host es suficiente por ahora)
- Multi-region / HA (single-tenant, single-instance)
- Blue-green deployment (overkill para el volumen actual)
- Terraform / IaC (manual Docker es suficiente)
- Monitoring stack (Grafana/Prometheus) — la observabilidad via Langfuse ya cubre esto

## Casos de Uso Críticos

1. **Ollama crash**: readiness probe detecta que Ollama no responde → container marcado como unhealthy → Docker restart policy lo levanta → readiness espera a que Ollama responda antes de aceptar mensajes
2. **Deploy de nueva versión**: `docker pull ghcr.io/user/localforge:v1.3.0` → `docker-compose up -d` → health check confirma que todo está OK → si falla, `docker-compose up -d` con tag anterior
3. **Secrets rotation**: cambiar WhatsApp token → actualizar Docker secret → restart container → token viejo nunca estuvo en disco como texto plano
4. **CI green → auto-deploy staging**: push a `main` → CI pasa → imagen taggeada → deploy automático a staging (si configurado)

## Diseño Técnico

### Health endpoints (`app/main.py`)

```python
@app.get("/health")
async def health():
    """Liveness: proceso vivo."""
    return {"status": "ok"}

@app.get("/ready")
async def ready():
    """Readiness: DB + Ollama respondiendo."""
    checks = {}
    # DB
    try:
        await app.state.repository.execute("SELECT 1")
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {e}"
    # Ollama
    try:
        resp = await app.state.ollama_client.health()
        checks["ollama"] = "ok" if resp else "error"
    except Exception as e:
        checks["ollama"] = f"error: {e}"

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        content={"status": "ok" if all_ok else "degraded", "checks": checks},
        status_code=200 if all_ok else 503,
    )
```

### Dockerfile additions

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1
```

### GitHub Actions workflow (`.github/workflows/release.yml`)

```yaml
on:
  push:
    tags: ['v*']
jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v5
        with:
          push: true
          tags: |
            ghcr.io/${{ github.repository }}:${{ github.ref_name }}
            ghcr.io/${{ github.repository }}:latest
```

### Docker Compose profiles

```yaml
services:
  app:
    profiles: ["dev", "prod"]
    # ...
  ollama:
    profiles: ["dev"]  # solo en dev; en prod es externo
    # ...
```

## Restricciones Arquitectónicas

- **Zero downtime**: health checks no deben causar side effects ni queries pesadas
- **Backward compatible**: `.env` sigue funcionando para dev; Docker secrets es opt-in para prod
- **Fast startup**: readiness puede bloquear tráfico pero no debe bloquear el proceso
- **CI existente**: el nuevo workflow de release es **adicional** al CI existente (lint/test), no lo reemplaza

## Métricas de Éxito

| Métrica | Target |
|---|---|
| MTTR (Mean Time To Recovery) | <2 min (con Docker restart + health check) |
| Secrets en texto plano en prod | 0 |
| Deploys con rollback disponible | 100% |
| Health check latency | <100ms |
| CI → imagen publicada | <5 min |
