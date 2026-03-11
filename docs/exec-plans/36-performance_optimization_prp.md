# PRP: Performance Optimization — Plan de Implementación

**Estado: ✅ Completado**

## Archivos Modificados

### Phase 1 (Tier 1 — Quick Wins)
- `app/conversation/manager.py`: Paralelizar queries en `get_windowed_history()` + `get_context()`
- `app/database/db.py`: Agregar índices + PRAGMAs faltantes
- `app/guardrails/checks.py`: Wrap `langdetect.detect()` en `asyncio.to_thread()`
- `app/guardrails/pipeline.py`: Actualizar para llamar `check_language_match` como async
- `app/llm/client.py`: Pre-compilar regex `<think>`
- `app/skills/router.py`: Pre-compilar regex de URL
- `app/formatting/whatsapp.py`: Pre-compilar 8 regexes de formatting

### Phase 2 (Tier 2 — Impacto Alto)
- `app/context/conversation_context.py`: Usar query JOIN para project progress
- `app/database/repository.py`: Método `get_projects_with_progress()` + `commit()` + `auto_commit` param
- `app/main.py`: Mover backfill a background task pre-yield via `asyncio.create_task()`
- `app/embeddings/indexer.py`: Batch saves con single commit por batch

### Phase 3 (Tier 3 — Polish)
- `app/database/repository.py`: Atomic `get_or_create_conversation` con `INSERT OR IGNORE`
- `app/mcp/manager.py`: Reducir timeout 30s→10s, cache `_tools_by_server`
- `app/conversation/manager.py`: LRU en `_conv_id_cache` con `OrderedDict`

### Tests
- `tests/guardrails/test_checks.py`: Actualizar tests de `check_language_match` a async
- `tests/test_guardrails.py`: Actualizar tests de `check_language_match` a async

---

## Fases de Implementación

### Phase 1: Quick Wins

#### 1A. Paralelizar `get_windowed_history()`

- [x] Leer `app/conversation/manager.py` y confirmar que las queries son independientes
- [x] Importar `asyncio` en el archivo
- [x] Cambiar las 2 queries secuenciales por `asyncio.gather()`
- [x] Verificar que `get_context()` (método legacy) también se beneficia — aplicada misma optimización
- [x] Correr tests existentes

---

#### 1B. Agregar índices y PRAGMAs a SQLite

- [x] Agregar `CREATE INDEX IF NOT EXISTS idx_summaries_conversation ON summaries(conversation_id, id)`
- [x] Agregar `CREATE INDEX IF NOT EXISTS idx_memories_active ON memories(active, id)`
- [x] Agregar `PRAGMA mmap_size=30000000` (30MB mmap)
- [x] Agregar `PRAGMA busy_timeout=5000` (5s lock contention wait)
- [x] Correr tests

---

#### 1C. Mover `langdetect` a thread

- [x] Importar `asyncio` en `app/guardrails/checks.py`
- [x] Convertir `check_language_match` a `async def`
- [x] Wrap llamadas a `detect()` en `asyncio.to_thread()`
- [x] Paralelizar ambos `detect()` con `asyncio.gather()`
- [x] Actualizar `pipeline.py` para llamar como async via `_run_async_check()`
- [x] Actualizar tests a async
- [x] Correr tests

---

#### 1D. Pre-compilar regexes en hot paths

- [x] `app/llm/client.py`: `_RE_THINK` module-level
- [x] `app/skills/router.py`: `_RE_URL` module-level
- [x] `app/formatting/whatsapp.py`: 8 regexes pre-compilados a module-level
- [x] Correr tests

---

#### 1E. Validación Phase 1

- [x] Lint + typecheck + tests pasa limpio (721 tests, 0 nuevos errores mypy)

---

### Phase 2: Optimizaciones de Impacto Alto

#### 2A. Project progress: eliminar N+1

- [x] Crear método `Repository.get_projects_with_progress()` con query JOIN + GROUP BY
- [x] Refactorizar `_get_projects_summary()` para usar el nuevo método
- [x] Eliminar el loop secuencial de `get_project_progress()` en context builder
- [x] Correr tests

---

#### 2B. Mover backfill a background task

- [x] Convertir backfill en `asyncio.create_task(_safe_backfill())` antes del `yield`
- [x] Warmup sigue antes del yield (sincrónico)
- [x] `_safe_backfill()` wrapper con try/except y logging

---

#### 2C. Batch embedding saves

- [x] Agregar `auto_commit=True` param a `save_embedding()` y `save_note_embedding()`
- [x] Agregar `commit()` público a Repository
- [x] Backfill usa `auto_commit=False` + single `commit()` por batch
- [x] Correr tests

---

#### 2D. Optimizar compaction threshold

- [x] Revisado: compaction ya prioriza JSON extraction antes de LLM (correcta implementación)
- [x] No se requieren cambios — threshold es configurable via `settings.compaction_threshold`

---

#### 2E. Validación Phase 2

- [x] Lint + typecheck + tests pasa limpio

---

### Phase 3: Polish

#### 3A. `get_or_create_conversation` atómico

- [x] Cambiar a patrón `INSERT OR IGNORE` + `UPDATE` + `SELECT` con single commit
- [x] Correr tests

#### 3B. MCP timeout y cache

- [x] Reducir `MCP_CONNECT_TIMEOUT` de 30s a 10s
- [x] Agregar `_tools_by_server: dict[str, list[str]]` cache
- [x] Refactorizar `_register_fetch_category()`, `list_servers()`, `hot_remove_server()` para usar cache

#### 3C. LRU en `_conv_id_cache`

- [x] Cambiar de `dict` a `OrderedDict` con eviction en 10000 entries
- [x] `move_to_end()` en hit, `popitem(last=False)` en eviction

#### 3D. Validación Phase 3

- [x] Lint + typecheck + tests pasa limpio

---

### Phase 4: Documentación y Cierre

- [x] Actualizar `CLAUDE.md` con patrones de performance
- [x] Actualizar `docs/features/README.md`
- [x] Actualizar `docs/exec-plans/README.md`
- [x] Crear `docs/testing/36-performance_testing.md`
- [x] Marcar este PRP como ✅ Completado
