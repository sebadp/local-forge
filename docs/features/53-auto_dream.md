# Feature: Auto-Dream — Memory Consolidation

> **Version**: v1.0
> **Fecha de implementacion**: 2026-04-01
> **Fase**: Fase 6
> **Estado**: ✅ Implementada

---

## Que hace?

Consolida automaticamente las memorias del usuario en segundo plano, eliminando duplicados, actualizando hechos obsoletos y extrayendo nuevas memorias desde los daily logs. Corre cada 24 horas (configurable) si hubo suficiente actividad.

---

## Arquitectura

```
APScheduler (interval job)
        │
        ▼
  should_dream()  ── time gate ── activity gate ── lock gate
        │ (si pasa)
        ▼
    run_dream()
        │
        ├── 1. Cargar memorias actuales (Repository)
        ├── 2. Cargar daily logs recientes (DailyLog)
        ├── 3. Single LLM call con prompt de 4 fases
        ├── 4. Parsear JSON response (acciones: remove/update/create)
        ├── 5. Ejecutar acciones en DB
        ├── 6. Regenerar MEMORY.md (prune index, max 40)
        └── 7. Persistir timestamp + release lock
```

---

## Archivos clave

| Archivo | Rol |
|---|---|
| `app/memory/dream.py` | Prompt de 4 fases, orquestacion del dream, DreamResult |
| `app/memory/consolidation_lock.py` | Lock file-based + timestamp + gate function |
| `app/main.py` | Registro del job en APScheduler |
| `app/config.py` | Settings: `dream_enabled`, `dream_interval_hours`, `dream_min_messages` |
| `tests/test_dream.py` | Tests del dream loop (11 tests) |
| `tests/test_consolidation_lock.py` | Tests del lock y gates (11 tests) |

---

## Walkthrough tecnico: como funciona

1. **Gate check** (`should_dream()`): Evalua 3 condiciones en orden de costo → `consolidation_lock.py`
   - Tiempo desde ultimo dream >= `dream_interval_hours`
   - Mensajes desde ultimo dream >= `dream_min_messages`
   - Lock adquirido exitosamente
2. **Orient**: Carga todas las memorias via `repository.list_memories()` → `dream.py:156`
3. **Gather**: Carga daily logs de los ultimos N dias (desde ultimo dream, max 14) → `dream.py:168`
4. **Consolidate**: Single LLM call con `think=False`, respuesta JSON con acciones → `dream.py:189`
5. **Execute**: Itera acciones (remove/update/create), valida IDs contra memorias existentes → `dream.py:208`
6. **Prune**: Regenera MEMORY.md con solo los `keep_ids` (max 40 en indice) → `dream.py:244`
7. **Cleanup**: Persiste timestamp en `.last_dream`, libera lock → `dream.py:259-260`

---

## Como extenderla

- Para cambiar la frecuencia: modificar `DREAM_INTERVAL_HOURS` en `.env`
- Para agregar un nuevo tipo de accion: extender el bloque `for action in actions` en `run_dream()` y actualizar `_DREAM_PROMPT`
- Para cambiar el umbral de actividad: modificar `DREAM_MIN_MESSAGES` en `.env`
- Para deshabilitar: `DREAM_ENABLED=false` en `.env`

---

## Guia de testing

```bash
# Unit + integration tests
.venv/bin/python -m pytest tests/test_dream.py tests/test_consolidation_lock.py -v
```

Tests cubren: lock acquire/release/stale, timestamp read/write, gate logic (time/activity/messages), dream execution (remove/update/create), error handling (LLM errors, invalid IDs, no memories).

---

## Decisiones de diseno

| Decision | Alternativa descartada | Motivo |
|---|---|---|
| Single LLM call con prompt de 4 fases | Multi-call (orient → consolidate → prune) | Mas eficiente en tokens y latencia con Ollama local |
| File-based lock (`.consolidation_lock`) | Redis/DB lock | No hay Redis; file lock es suficiente para single-instance |
| `think=False` | `think=True` | Output es JSON estructurado, no requiere razonamiento visible |
| Gate function separada (`should_dream`) | Check dentro de `run_dream` | Separacion de concerns; gate es barata, dream es costosa |
| Stale lock threshold 2h | Configurable threshold | Simplicity; 2h es suficiente para cualquier run razonable |

---

## Gotchas y edge cases

- **Lock stale**: Si el proceso muere durante un dream, el lock queda. Se auto-resuelve despues de 2h.
- **LLM responde garbage**: `_parse_dream_response` retorna `{"actions": [], "keep_ids": []}` — no-op seguro.
- **IDs invalidos en acciones**: Se filtran silenciosamente (solo se ejecutan acciones con IDs validos).
- **Sin daily logs**: El dream igual corre pero con `"(no daily logs found)"` — solo consolida memorias existentes.
- **Sin memorias**: `run_dream` retorna inmediatamente con DreamResult vacio.
- **Error de LLM**: Se captura, se loguea, se libera el lock, DreamResult incluye `error`.

---

## Variables de configuracion relevantes

| Variable (`config.py`) | Default | Efecto |
|---|---|---|
| `dream_enabled` | `true` | Activa/desactiva el job de auto-dream |
| `dream_interval_hours` | `24` | Horas minimas entre consolidaciones |
| `dream_min_messages` | `50` | Mensajes minimos para triggear un dream |
