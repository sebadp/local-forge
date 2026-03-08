# PRP: Performance Optimization — Plan de Implementación

## Archivos a Modificar

### Phase 1 (Tier 1 — Quick Wins)
- `app/conversation/manager.py`: Paralelizar queries en `get_windowed_history()`
- `app/database/db.py`: Agregar índices + PRAGMAs faltantes
- `app/guardrails/checks.py`: Wrap `langdetect.detect()` en `asyncio.to_thread()`
- `app/llm/client.py`: Pre-compilar regex `<think>`
- `app/skills/router.py`: Pre-compilar regex de URL
- `app/formatting/whatsapp.py`: Pre-compilar regexes de formatting (si aplica)

### Phase 2 (Tier 2 — Impacto Alto)
- `app/context/conversation_context.py`: Paralelizar project progress
- `app/database/repository.py`: Método batch para project progress (query JOIN)
- `app/main.py`: Mover backfill a post-yield
- `app/embeddings/indexer.py`: Batch saves con single commit
- `app/skills/executor.py`: Threshold más agresivo pre-compaction

### Phase 3 (Tier 3 — Polish)
- `app/database/repository.py`: `INSERT OR IGNORE` en `get_or_create_conversation`
- `app/mcp/manager.py`: Reducir timeout, cache `_tools_by_server`
- `app/conversation/manager.py`: LRU en `_conv_id_cache`

### Tests
- `tests/test_manager.py` (nuevo o existente): test de `get_windowed_history` paralelo
- `tests/test_guardrails.py` (existente): verificar que langdetect sigue funcionando
- `tests/test_performance.py` (nuevo, opcional): benchmarks básicos

---

## Fases de Implementación

### Phase 1: Quick Wins (~2-3 horas)

Cambios quirúrgicos que no alteran interfaces ni comportamiento. Cada uno es un commit
independiente que se puede revertir sin afectar los demás.

#### 1A. Paralelizar `get_windowed_history()`

- [ ] Leer `app/conversation/manager.py` y confirmar que las queries son independientes
- [ ] Importar `asyncio` en el archivo
- [ ] Cambiar las 2 queries secuenciales por `asyncio.gather()`
- [ ] Verificar que `get_context()` (método legacy, línea 62-95) también se beneficia
      — evaluar si aplicar la misma optimización ahí
- [ ] Correr tests existentes: `.venv/bin/python -m pytest tests/ -v -k manager`

**Cambio concreto**:
```python
# ANTES (manager.py:52-60):
history = await self._repo.get_recent_messages(conv_id, self._max_messages)
if len(history) <= verbatim_count:
    return history, None
summary = await self._repo.get_latest_summary(conv_id)

# DESPUÉS:
# Lanzar ambas en paralelo (summary se descarta si no se necesita)
history, summary = await asyncio.gather(
    self._repo.get_recent_messages(conv_id, self._max_messages),
    self._repo.get_latest_summary(conv_id),
)
if len(history) <= verbatim_count:
    return history, None
```

**Trade-off**: En el caso de historial corto (<=8 msgs), hacemos un query de summary
innecesario. Pero el costo de ese query es ~1ms (índice nuevo + `LIMIT 1`), y nos
ahorramos ~50-150ms en el caso común (historial largo). El trade-off es favorable.

---

#### 1B. Agregar índices y PRAGMAs a SQLite

- [ ] Agregar `CREATE INDEX IF NOT EXISTS idx_summaries_conversation ON summaries(conversation_id, id)` al schema en `db.py`
- [ ] Agregar `CREATE INDEX IF NOT EXISTS idx_memories_active ON memories(active, id)` al schema
- [ ] Agregar `PRAGMA mmap_size=30000000` después de los PRAGMAs existentes en `init_db()`
- [ ] Agregar `PRAGMA busy_timeout=5000` después de los PRAGMAs existentes
- [ ] Correr tests: `.venv/bin/python -m pytest tests/ -v -k "db or repository"`

**Nota de migración**: Los `CREATE INDEX IF NOT EXISTS` son idempotentes — no rompen DBs
existentes. Los PRAGMAs se aplican por conexión, no persisten en el archivo.

---

#### 1C. Mover `langdetect` a thread

- [ ] Importar `asyncio` en `app/guardrails/checks.py` (si no está)
- [ ] Wrap ambas llamadas a `detect()` en `asyncio.to_thread()`
- [ ] Paralelizar ambos `detect()` con `asyncio.gather()`
- [ ] Correr tests: `.venv/bin/python -m pytest tests/ -v -k guardrail`

**Cambio concreto**:
```python
# ANTES (checks.py:72-77):
from langdetect import detect
user_lang = detect(user_text)
reply_lang = detect(reply)

# DESPUÉS:
from langdetect import detect
user_lang, reply_lang = await asyncio.gather(
    asyncio.to_thread(detect, user_text),
    asyncio.to_thread(detect, reply),
)
```

---

#### 1D. Pre-compilar regexes en hot paths

- [ ] `app/llm/client.py`: Crear `_RE_THINK = re.compile(r"<think>.*?</think>\n*", re.DOTALL)` a module-level, usar `_RE_THINK.sub()` en `chat_with_tools()`
- [ ] `app/skills/router.py`: Mover `url_pattern = re.compile(...)` dentro de `classify_intent()` a module-level como `_RE_URL`
- [ ] Verificar si `app/formatting/whatsapp.py` tiene regexes inline — si sí, pre-compilar
- [ ] Correr tests: `.venv/bin/python -m pytest tests/ -v`

---

#### 1E. Validación Phase 1

- [ ] `make check` (lint + typecheck + tests) pasa limpio
- [ ] Verificar con `EXPLAIN QUERY PLAN` que los nuevos índices se usan:
      ```sql
      EXPLAIN QUERY PLAN SELECT content FROM memories WHERE active=1 ORDER BY id DESC;
      EXPLAIN QUERY PLAN SELECT content FROM summaries WHERE conversation_id=1 ORDER BY id DESC LIMIT 1;
      ```
- [ ] Deploy a staging y comparar latencia en spans de tracing

---

### Phase 2: Optimizaciones de Impacto Alto (~4-6 horas)

Cambios que requieren algo más de cuidado — tocan la lógica de startup, queries
compuestas, o el flujo del LLM.

#### 2A. Project progress: eliminar N+1

- [ ] Crear método `Repository.get_projects_with_progress(phone_number, status, limit)` que usa un solo query JOIN + GROUP BY
- [ ] Refactorizar `_get_projects_summary()` en `conversation_context.py` para usar el nuevo método
- [ ] Eliminar el loop secuencial de `get_project_progress()`
- [ ] Correr tests: `.venv/bin/python -m pytest tests/ -v -k project`

**Query propuesto**:
```sql
SELECT p.id, p.name, p.status,
       COUNT(pt.id) as total_tasks,
       COUNT(CASE WHEN pt.status = 'done' THEN 1 END) as done_tasks
FROM projects p
LEFT JOIN project_tasks pt ON pt.project_id = p.id
WHERE p.phone_number = ? AND p.status = ?
GROUP BY p.id
ORDER BY p.updated_at DESC
LIMIT ?
```

---

#### 2B. Mover backfill a post-yield

- [ ] Refactorizar el bloque de backfill en `main.py` para que corra como `asyncio.create_task()` después del `yield`
- [ ] Mover el `yield` antes del bloque de backfill
- [ ] Wrap en función `_safe_backfill()` con try/except y logging
- [ ] Verificar que el warmup sigue funcionando correctamente (antes del yield)
- [ ] Test manual: reiniciar app, enviar mensaje inmediatamente, verificar que responde

**Estructura propuesta**:
```python
# Warmup (sigue antes del yield — es rápido, ~1-2s)
try:
    await asyncio.gather(embed(["warmup"]), chat_with_tools([...]))
except Exception:
    logger.warning("Warmup failed (non-critical)")

yield  # App acepta requests

# Backfill en background (no bloquea)
if vec_available and settings.semantic_search_enabled:
    asyncio.create_task(_safe_backfill(repository, ollama_client, settings))

# Shutdown...
```

---

#### 2C. Batch embedding saves

- [ ] Refactorizar `backfill_embeddings()` en `indexer.py` para acumular INSERTs y hacer un solo `commit()` por batch
- [ ] Aplicar lo mismo a `backfill_note_embeddings()`
- [ ] Correr tests: `.venv/bin/python -m pytest tests/ -v -k embed`

---

#### 2D. Optimizar compaction threshold

- [ ] En `executor.py`, revisar el threshold de `compact_tool_output()` — aumentar el límite de chars antes de invocar LLM compaction
- [ ] Priorizar: si `_try_json_extraction()` produce un resultado ≤ max_length, no llamar al LLM
- [ ] Verificar que tool results no se truncan prematuramente
- [ ] Correr tests: `.venv/bin/python -m pytest tests/ -v -k "executor or compaction"`

---

#### 2E. Validación Phase 2

- [ ] `make check` pasa limpio
- [ ] Test de startup time: `time docker compose up -d && curl health`
- [ ] Comparar spans de tracing antes/después para project progress
- [ ] Verificar que backfill completa en background (log "Backfill completed")

---

### Phase 3: Polish (~2-3 horas)

Optimizaciones menores que mejoran la robustez y previenen edge cases.

#### 3A. `get_or_create_conversation` atómico

- [ ] Cambiar a patrón `INSERT OR IGNORE` + `UPDATE` + `SELECT` con single commit
- [ ] Correr tests de conversation

#### 3B. MCP timeout y cache

- [ ] Reducir `MCP_CONNECT_TIMEOUT` de 30s a 10s
- [ ] Agregar `_tools_by_server: dict[str, list[str]]` en `McpManager`
- [ ] Refactorizar `_register_fetch_category()` para usar el cache en vez de triple scan

#### 3C. LRU en `_conv_id_cache`

- [ ] Cambiar de `dict` a `OrderedDict` con eviction en 10000 entries
- [ ] O alternativamente: usar `functools.lru_cache` en `_get_conv_id` si la firma lo permite

#### 3D. Validación Phase 3

- [ ] `make check` pasa limpio
- [ ] Verificar edge case: MCP server inalcanzable con timeout de 10s no bloquea el startup

---

### Phase 4: Documentación y Cierre

- [ ] Actualizar `CLAUDE.md` con patrones de performance que deben preservarse
- [ ] Actualizar `docs/features/README.md` con la nueva entrada
- [ ] Actualizar `docs/exec-plans/README.md` con el plan
- [ ] Crear `docs/testing/36-performance_testing.md` con instrucciones de benchmark
- [ ] Marcar este PRP como ✅ Completado

---

## Orden de commits sugerido

```
1. feat: parallelize get_windowed_history queries
2. feat: add missing SQLite indexes for summaries and memories
3. feat: add mmap_size and busy_timeout PRAGMAs
4. fix: wrap langdetect in asyncio.to_thread
5. perf: pre-compile regexes in hot paths (client, router, formatting)
6. perf: eliminate N+1 in project progress with JOIN query
7. perf: move embedding backfill to post-yield background task
8. perf: batch embedding saves with single commit
9. perf: raise compaction threshold to reduce LLM calls
10. refactor: atomic get_or_create_conversation
11. perf: reduce MCP timeout, cache tools_by_server
12. perf: add LRU eviction to conv_id_cache
13. docs: performance optimization analysis and plan
```

Cada commit es independiente y revertible. El orden respeta dependencias (índices antes de
paralelización, backfill refactor antes de batch saves).
