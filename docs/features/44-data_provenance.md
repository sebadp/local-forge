# Feature: Data Provenance & Lineage (Plan 44)

> **Exec Plan**: [`44-data_provenance_prd.md`](../exec-plans/44-data_provenance_prd.md) / [`44-data_provenance_prp.md`](../exec-plans/44-data_provenance_prp.md)
> **Testing**: [`44-data_provenance_testing.md`](../testing/44-data_provenance_testing.md)

## Qué hace

Registra cada mutación (CREATE, UPDATE, DELETE, MERGE) sobre memorias, notas y proyectos con:
- **Quién** lo hizo (actor: user, llm_flush, llm_consolidator, tool, agent, system, file_sync)
- **Cuándo** (timestamp automático)
- **Qué cambió** (snapshots before/after)
- **Desde dónde** (source_trace_id, metadata)
- **Versiones de memorias** (historial append-only)

## Arquitectura

### Componentes

```
app/provenance/
  __init__.py
  models.py          # Actor, Action, EntityType constants + AuditEntry dataclass
  audit.py           # AuditLogger — CRUD audit log + memory versions
  context.py         # Module-level get/set audit_logger (avoids parameter threading)
  lineage_tool.py    # trace_data_origin + get_entity_history tools
```

### Tablas

| Tabla | Propósito |
|---|---|
| `entity_audit_log` | Registro de cada mutación con actor, snapshots, metadata |
| `memory_versions` | Historial append-only de versiones por memoria |
| `memories.source_trace_id` | FK a traza original (nueva columna) |
| `notes.source_trace_id` | FK a traza original (nueva columna) |

### Patrón de integración

El `AuditLogger` se inicializa en `main.py` y se hace accesible via `app/provenance/context.py` (module-level accessor). Cada call site que muta datos llama al audit logger con un bloque `try/except` que silencia errores (best-effort, mismo patrón que tracing).

### Call sites instrumentados

| Operación | Archivo | Actor |
|---|---|---|
| `/remember` | `commands/builtins.py` | `user` |
| `/forget` | `commands/builtins.py` | `user` |
| `flush_to_memory` | `conversation/summarizer.py` | `llm_flush` |
| `consolidate_memories` | `memory/consolidator.py` | `llm_consolidator` |
| MEMORY.md watcher | `memory/watcher.py` | `file_sync` |
| Self-correction | `webhook/router.py` | `system` |
| `save_note` tool | `skills/tools/notes_tools.py` | `tool` |
| `delete_note` tool | `skills/tools/notes_tools.py` | `tool` |
| `create_project` tool | `skills/tools/project_tools.py` | `tool` |
| `add_project_note` tool | `skills/tools/project_tools.py` | `tool` |
| `add_news_preference` tool | `skills/tools/news_tools.py` | `tool` |

### Tools LLM

- `trace_data_origin(entity_type, entity_id)` — muestra historial de mutaciones + versiones
- `get_entity_history(entity_type?, actor?, limit?)` — browse reciente del audit log

### Categoría en router

`"provenance"` en `TOOL_CATEGORIES` con few-shot examples para clasificación.

## Decisiones de diseño

1. **Best-effort**: audit log nunca bloquea ni falla el flujo. Errores silenciados con `except Exception:` + `logger.warning`
2. **Module-level accessor**: evita threading del audit_logger por 15+ parámetros de funciones
3. **No FK enforcement en audit log**: `entity_id` es un INTEGER sin FK real, para no bloquear si la entidad ya fue eliminada
4. **Snapshots truncados**: before/after se truncan a 200 chars para notas (suficiente para debugging)
5. **Memory versioning es append-only**: no hay DELETE ni UPDATE en `memory_versions`
6. **Cleanup**: `cleanup_old_entries(days=90)` disponible para futuro cron job

## Settings

| Setting | Default | Descripción |
|---|---|---|
| `provenance_enabled` | `true` | Habilita/deshabilita todo el sistema de provenance |

## Gotchas

- `source_trace_id` en memorias/notas es NULL para registros pre-existentes (backward compatible)
- La categoría `"self_correction"` no se sincroniza a MEMORY.md pero sí tiene audit entries
- El watcher usa `remove_memory_return_id` en lugar de `remove_memory` para obtener el ID para audit
- El audit logger require que el schema ya exista (run after `init_db()`)
