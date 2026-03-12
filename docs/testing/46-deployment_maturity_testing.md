# Testing Manual: Deployment Maturity

> **Feature documentada**: [`docs/features/46-deployment_maturity.md`](../features/46-deployment_maturity.md)
> **Requisitos previos**: Container corriendo (`docker compose --profile dev up -d`), modelos de Ollama disponibles.

---

## Verificar que la feature está activa

Los endpoints de salud están siempre activos (no requieren configuración).

```bash
# Liveness
curl -s http://localhost:8000/health | python3 -m json.tool

# Readiness
curl -s http://localhost:8000/ready | python3 -m json.tool
```

---

## Casos de prueba principales

| Mensaje / Acción | Resultado esperado |
|---|---|
| `curl http://localhost:8000/health` | `200 {"status": "ok"}` |
| `curl http://localhost:8000/ready` (todo OK) | `200 {"status": "ok", "checks": {"db": "ok", "ollama": "ok"}}` |
| `curl http://localhost:8000/ready` (Ollama caído) | `503 {"status": "degraded", "checks": {"db": "ok", "ollama": "error: ..."}}` |
| `docker inspect --format='{{.State.Health.Status}}' <container>` | `healthy` (después del start-period) |

---

## Edge cases y validaciones

| Escenario | Resultado esperado |
|---|---|
| Ollama detenido (`docker compose stop ollama`) | `/health` sigue 200; `/ready` retorna 503 con `ollama: "error: ..."` |
| DB corrupta/bloqueada | `/health` sigue 200; `/ready` retorna 503 con `db: "error: ..."` |
| Container recién arrancado (dentro de start-period) | HEALTHCHECK no reporta unhealthy durante los primeros 60s |
| `docker compose --profile prod up` sin Ollama local | Funciona si `OLLAMA_BASE_URL` apunta a Ollama externo |
| `docker compose up` sin `--profile` | No levanta ningún servicio (profiles son obligatorios) |

---

## Verificar Docker healthcheck

```bash
# Ver estado de salud del container
docker inspect --format='{{json .State.Health}}' $(docker compose ps -q localforge) | python3 -m json.tool

# Campos clave:
# - "Status": "healthy" / "unhealthy" / "starting"
# - "FailingStreak": 0 (si healthy)
# - "Log": últimos health check results
```

---

## Verificar profiles

```bash
# Dev: levanta todo (localforge + ollama + ngrok + langfuse)
docker compose --profile dev config --services
# Esperado: localforge, ollama, ngrok, langfuse-server, langfuse-db

# Prod: solo localforge + langfuse
docker compose --profile prod config --services
# Esperado: localforge, langfuse-server, langfuse-db
```

---

## Verificar release workflow

```bash
# Simular un release (no pushea)
git tag v0.0.1-test
# Verificar que .github/workflows/release.yml existe y tiene el trigger correcto
grep -A3 "on:" .github/workflows/release.yml
# Esperado: push → tags: ['v*']

# Limpiar tag de test
git tag -d v0.0.1-test
```

---

## Tests automatizados

```bash
# Correr los 5 tests de health
.venv/bin/python -m pytest tests/test_health.py -v

# Tests cubiertos:
# - test_health_liveness: /health siempre retorna 200
# - test_ready_all_ok: DB + Ollama OK → 200
# - test_ready_ollama_down: Ollama caído → 503 degraded
# - test_ready_db_down: DB caída → 503 degraded
# - test_ready_both_down: ambos caídos → 503 degraded
```

---

## Verificar graceful shutdown

```bash
# Enviar SIGTERM al container
docker compose kill -s SIGTERM localforge

# Verificar en logs que drena requests in-flight
docker compose logs localforge 2>&1 | tail -20
# Esperado: "Waiting for in-flight tasks..." o cierre limpio sin tracebacks
```

---

## Troubleshooting

| Problema | Causa probable | Solución |
|---|---|---|
| `/ready` retorna 503 con `ollama: error` | Ollama no está corriendo o URL incorrecta | Verificar `OLLAMA_BASE_URL` en `.env`, correr `curl http://localhost:11435/api/tags` |
| Container queda en `unhealthy` | App no responde en `/health` dentro del timeout (5s) | Verificar que uvicorn está corriendo, revisar logs |
| `docker compose up` no levanta nada | Falta `--profile` | Usar `docker compose --profile dev up` |
| Release workflow no se triggerea | Tag no empieza con `v` | Tags deben ser `v1.0.0`, `v2.1.3`, etc. |
| CI falla por branch incorrecto | CI apuntaba a `master` | Actualizado a `main` en Plan 46 |

---

## Variables relevantes para testing

| Variable (`.env`) | Valor de test | Efecto |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11435` | URL del Ollama que `/ready` verifica |
| `DATABASE_PATH` | `data/localforge.db` | DB que `/ready` verifica con `SELECT 1` |
