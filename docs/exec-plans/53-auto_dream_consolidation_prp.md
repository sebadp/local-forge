# PRP: Auto-Dream — Memory Consolidation Background (Plan 53)

## Archivos a Modificar

- `app/memory/dream.py`: **Nuevo** — Prompt de 4 fases + orquestación del dream
- `app/memory/consolidation_lock.py`: **Nuevo** — Lock file + timestamp de última consolidación
- `app/memory/consolidator.py`: Extender con lógica de 4 fases (reutilizar lo existente)
- `app/main.py`: Registrar job de APScheduler para auto-dream
- `app/config.py`: Settings para `DREAM_INTERVAL_HOURS`, `DREAM_MIN_SESSIONS`
- `tests/test_dream.py`: **Nuevo** — Tests del dream loop
- `tests/test_consolidation_lock.py`: **Nuevo** — Tests del lock

## Fases de Implementación

### Phase 1: Lock & Gate

- [x] Crear `app/memory/consolidation_lock.py`:
  - `try_acquire_lock(lock_path: Path) -> bool` — crea `.consolidation_lock` con PID+timestamp, retorna False si ya existe y no es stale (>2h = stale)
  - `release_lock(lock_path: Path) -> None`
  - `read_last_consolidated_at(lock_path: Path) -> datetime | None` — lee timestamp del lock file
  - `write_last_consolidated_at(lock_path: Path) -> None` — persiste timestamp en `data/.last_dream`
- [x] Crear `should_dream()` gate function:
  - Check 1: horas desde `last_consolidated_at` ≥ `DREAM_INTERVAL_HOURS` (default 24)
  - Check 2: contar mensajes en `messages` table con `created_at > last_consolidated_at`, necesita ≥ `DREAM_MIN_SESSIONS` mensajes (default 50, proxy de actividad)
  - Check 3: `try_acquire_lock()` exitoso
- [x] Agregar settings a `app/config.py`: `dream_interval_hours: int = 24`, `dream_min_messages: int = 50`
- [x] Tests para lock acquire/release/stale detection y gate logic

### Phase 2: Dream Prompt & Execution

- [x] Crear `app/memory/dream.py` con:
  - `DREAM_PROMPT` — prompt de 4 fases adaptado:
    ```
    Phase 1 — ORIENT: Te paso las memorias actuales y el índice MEMORY.md
    Phase 2 — GATHER: Te paso los daily logs desde {last_dream_date} y hechos recientes
    Phase 3 — CONSOLIDATE: Retorna JSON con acciones:
      {"actions": [
        {"type": "update", "id": 5, "new_content": "..."},
        {"type": "remove", "id": 12, "reason": "superseded by #5"},
        {"type": "create", "content": "...", "category": "..."},
      ]}
    Phase 4 — PRUNE_INDEX: Lista de memory IDs que deben estar en MEMORY.md (max 40)
    ```
  - `run_dream(repository, ollama_client, memory_file) -> DreamResult`
    - Cargar memorias + daily logs + mensajes recientes
    - Single LLM call con todo el contexto (más eficiente que multi-call)
    - Parsear JSON response
    - Ejecutar acciones: update/remove/create memorias via Repository
    - Regenerar `MEMORY.md` con las memorias restantes
    - Retornar `DreamResult(removed=N, updated=N, created=N)`
- [x] `DreamResult` dataclass con métricas del dream
- [x] Tests con mocked Ollama response

### Phase 3: Scheduler Integration

- [x] En `app/main.py`, dentro del `lifespan()`:
  - Agregar job: `scheduler.add_job(dream_job, "interval", hours=settings.dream_interval_hours, id="auto_dream")`
  - `dream_job()`: async function que llama `should_dream()` → `run_dream()` → `release_lock()`, todo en try/except best-effort
- [x] Registrar como span en tracing: `async with TraceContext("dream_consolidation"):`
- [x] Logging estructurado: `logger.info("dream.completed", extra={"removed": N, "updated": N, "created": N})`

### Phase 4: Documentación & QA

- [x] `make test` pasa
- [x] `make lint` pasa  
- [x] Crear `docs/features/53-auto_dream.md`
- [x] Actualizar `AGENTS.md` con el nuevo módulo
- [x] Actualizar `CLAUDE.md` si hay patrones nuevos
