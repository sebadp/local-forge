# Testing Manual: Auto-Dream — Memory Consolidation

> **Feature documentada**: [`docs/features/53-auto_dream.md`](../features/53-auto_dream.md)
> **Requisitos previos**: Container corriendo (`docker compose up -d`), Ollama con qwen3.5:9b disponible.

---

## Verificar que la feature está activa

```bash
docker compose logs -f localforge | grep -i "dream"
```

Confirmar:
- `APScheduler` registra el job de auto-dream al startup
- `dream_enabled: true` en la config

---

## Casos de prueba principales

| Acción | Resultado esperado |
|---|---|
| Enviar 50+ mensajes (o `DREAM_MIN_MESSAGES` configurado), esperar a que pase `DREAM_INTERVAL_HOURS` | El dream se triggerea automáticamente. Logs muestran `run_dream` ejecutándose |
| Verificar MEMORY.md después de un dream | Memorias consolidadas: duplicados eliminados, facts obsoletos actualizados |
| Revisar DB después del dream | Memorias creadas/actualizadas/eliminadas según acciones del LLM |

---

## Edge cases y validaciones

| Escenario | Resultado esperado |
|---|---|
| No hay memorias en la DB | `run_dream` retorna inmediatamente con DreamResult vacío |
| No hay daily logs recientes | Dream corre con `"(no daily logs found)"` — solo consolida existentes |
| LLM responde JSON inválido | `_parse_dream_response` retorna `{"actions": [], "keep_ids": []}` — no-op |
| Acciones con IDs inválidos | Se filtran silenciosamente, solo se ejecutan acciones con IDs válidos |
| Proceso muere durante dream | Lock queda, se auto-resuelve después de 2h (stale threshold) |
| `DREAM_ENABLED=false` | El job no se registra en APScheduler |

---

## Verificar en logs

```bash
# Actividad del dream
docker compose logs -f localforge 2>&1 | grep -i "dream"

# Gate check
docker compose logs -f localforge 2>&1 | grep -i "should_dream"

# Lock
docker compose logs -f localforge 2>&1 | grep -i "consolidation_lock"
```

---

## Queries de verificación en DB

```bash
# Verificar memorias antes y después del dream
sqlite3 data/localforge.db "SELECT id, content FROM memories ORDER BY updated_at DESC LIMIT 20;"

# Verificar timestamp del último dream
cat data/.last_dream 2>/dev/null || echo "No dream ejecutado aún"

# Verificar lock
ls -la data/.consolidation_lock 2>/dev/null || echo "No lock activo"
```

---

## Verificar graceful degradation

1. Detener Ollama: `docker compose stop ollama`
2. Esperar al trigger del dream
3. Verificar en logs: error capturado, lock liberado, DreamResult incluye `error`
4. Reiniciar Ollama: `docker compose start ollama`
5. Siguiente dream ejecuta normalmente

---

## Tests automatizados

```bash
.venv/bin/python -m pytest tests/test_dream.py tests/test_consolidation_lock.py -v
# 22 tests: lock acquire/release/stale, timestamp, gate logic, dream execution, error handling
```

---

## Troubleshooting

| Problema | Causa probable | Solución |
|---|---|---|
| Dream nunca se ejecuta | `DREAM_ENABLED=false` o `DREAM_MIN_MESSAGES` muy alto | Verificar `.env`, bajar threshold para testing |
| Lock permanece activo | Proceso murió durante dream | Esperar 2h o eliminar `data/.consolidation_lock` manualmente |
| Dream no consolida nada | LLM retorna JSON vacío | Revisar prompt en logs, verificar que Ollama responde correctamente |
| Memorias desaparecen | Dream las marcó como duplicadas | Revisar MEMORY.md — las memorias se podaron, no se perdieron |

---

## Variables relevantes para testing

| Variable (`.env`) | Valor de test | Efecto |
|---|---|---|
| `DREAM_ENABLED` | `true` | Activa/desactiva el job |
| `DREAM_INTERVAL_HOURS` | `1` (para testing, default 24) | Horas mínimas entre dreams |
| `DREAM_MIN_MESSAGES` | `5` (para testing, default 50) | Mensajes mínimos para triggear |
