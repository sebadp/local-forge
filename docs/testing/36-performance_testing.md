# Testing: Performance Optimization (Plan 36)

> **Feature**: [`docs/features/36-performance_optimization.md`](../features/36-performance_optimization.md)
> **Exec Plan**: [`docs/exec-plans/36-performance_optimization_prp.md`](../exec-plans/36-performance_optimization_prp.md)

---

## Tests automatizados

```bash
# Suite completa (721 tests)
.venv/bin/python -m pytest tests/ -v

# Tests relevantes por area
.venv/bin/python -m pytest tests/ -v -k "manager"        # get_windowed_history paralelo
.venv/bin/python -m pytest tests/ -v -k "guardrail"      # langdetect async
.venv/bin/python -m pytest tests/ -v -k "db or repository"  # indices, atomic upsert
.venv/bin/python -m pytest tests/ -v -k "whatsapp"       # regexes pre-compilados
.venv/bin/python -m pytest tests/ -v -k "embed"          # batch saves
```

---

## Verificar en startup logs

```bash
docker compose logs -f localforge 2>&1 | grep -E "(mmap|busy_timeout|backfill|warmed)"
```

Esperado:
- `Ollama models warmed up` (warmup antes del yield)
- `Embedding backfill completed (background)` (backfill post-yield)

---

## Verificar indices SQLite

```bash
sqlite3 data/localforge.db "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name;"
```

Debe incluir:
- `idx_summaries_conversation`
- `idx_memories_active`

Verificar que los indices se usan:
```bash
sqlite3 data/localforge.db "EXPLAIN QUERY PLAN SELECT content FROM memories WHERE active=1 ORDER BY id DESC;"
# Esperado: SEARCH ... USING INDEX idx_memories_active

sqlite3 data/localforge.db "EXPLAIN QUERY PLAN SELECT content FROM summaries WHERE conversation_id=1 ORDER BY id DESC LIMIT 1;"
# Esperado: SEARCH ... USING INDEX idx_summaries_conversation
```

---

## Casos de prueba manual

| Escenario | Accion | Esperado |
|---|---|---|
| Mensaje simple | Enviar "hola" | Respuesta < 2s |
| Con proyectos | Crear 5 proyectos, enviar mensaje | Sin demora extra por N+1 |
| Cold start | Reiniciar container, enviar mensaje inmediato | Responde sin esperar backfill |
| MCP server caido | Desconectar MCP server, reiniciar | Timeout en 10s (no 30s) |
| Cache eviction | Enviar desde >10K numeros distintos | Ultimos numeros siempre en cache |

---

## Edge cases

| Escenario | Esperado |
|---|---|
| `check_language_match` con texto < 30 chars | Skip sin llamar `asyncio.to_thread` |
| Backfill + primer request al mismo tiempo | Ambos corren, request no bloqueado |
| DB sin indices (primera vez) | `CREATE INDEX IF NOT EXISTS` los crea |
| `get_or_create_conversation` concurrente | `INSERT OR IGNORE` previene duplicados |

---

## Regression checklist

- [ ] `check_language_match` tests son async (no sync)
- [ ] `pipeline.py` llama language check via `_run_async_check()` (no `_run_check()`)
- [ ] Warmup sigue ANTES del yield (no movido a post-yield)
- [ ] `get_project_progress()` original sin cambios (usado por project_tools)
- [ ] `save_embedding()` default `auto_commit=True` (backward compatible)
