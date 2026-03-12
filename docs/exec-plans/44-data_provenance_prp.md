# PRP: Data Provenance & Lineage

## Archivos a Modificar

### Nuevos
- `app/provenance/__init__.py`: Package init
- `app/provenance/audit.py`: `AuditLogger` — best-effort async audit log + memory versioning
- `app/provenance/models.py`: Actor constants, AuditEntry dataclass
- `app/provenance/context.py`: Module-level accessor for audit logger
- `app/provenance/lineage_tool.py`: `trace_data_origin` + `get_entity_history` tool registration
- `tests/test_provenance.py`: Unit tests (18 tests)

### Modificados
- `app/database/db.py`: PROVENANCE_SCHEMA (entity_audit_log + memory_versions + ALTER TABLE)
- `app/config.py`: `provenance_enabled: bool = True`
- `app/database/repository.py`: `add_memory()` y `save_note()` aceptan `source_trace_id` opcional
- `app/commands/context.py`: `CommandContext.audit_logger` field
- `app/commands/builtins.py`: `/remember` y `/forget` pasan actor al audit logger
- `app/conversation/summarizer.py`: `flush_to_memory()` pasa actor `llm_flush`
- `app/memory/consolidator.py`: `consolidate_memories()` pasa actor `llm_consolidator`
- `app/memory/watcher.py`: file sync pasa actor `file_sync`
- `app/webhook/router.py`: self-correction pasa actor `system`, `_get_cmd_audit_logger()` helper
- `app/skills/tools/notes_tools.py`: save_note/delete_note pasan actor `tool`
- `app/skills/tools/news_tools.py`: news pref pasa actor `tool`
- `app/skills/tools/project_tools.py`: create_project/add_project_note pasan actor `tool`
- `app/skills/router.py`: Agrega categoría `"provenance"` + few-shot examples
- `app/main.py`: Inicializa `AuditLogger`, llama `set_audit_logger()`, registra provenance tools
- `CLAUDE.md`: Documentar patrones de provenance

## Fases de Implementación

### Phase 1: Schema + AuditLogger core
- [x] Agregar `PROVENANCE_SCHEMA` en `db.py` (entity_audit_log + memory_versions)
- [x] Migration para `source_trace_id` en memories y notes
- [x] Agregar `provenance_enabled` setting en `config.py`
- [x] Crear `app/provenance/models.py` (Actor constants, AuditEntry)
- [x] Crear `app/provenance/audit.py` (AuditLogger class — best-effort, async)
- [x] Crear `app/provenance/context.py` (module-level accessor)

### Phase 2: Repository integration
- [x] `add_memory()` acepta `source_trace_id` opcional
- [x] `save_note()` acepta `source_trace_id` opcional
- [x] AuditLogger.get_audit_log() query por entity
- [x] AuditLogger.get_memory_versions() query por memory_id
- [x] AuditLogger.get_entity_history() query con filtros
- [x] AuditLogger.log_mutation() hace INSERT en entity_audit_log
- [x] AuditLogger.version_memory() hace INSERT en memory_versions
- [x] AuditLogger.cleanup_old_entries() retention policy

### Phase 3: Hook call sites (memories)
- [x] `/remember` → audit log con actor=user + version
- [x] `/forget` → audit log con actor=user
- [x] `flush_to_memory()` → actor=llm_flush + version
- [x] `consolidate_memories()` → actor=llm_consolidator + MERGE metadata
- [x] `watcher._sync_from_file()` → actor=file_sync (add + remove)
- [x] `_save_self_correction_memory()` → actor=system

### Phase 4: Hook call sites (notes + projects)
- [x] `save_note` tool → actor=tool
- [x] `delete_note` tool → actor=tool (with before_snapshot)
- [x] `add_project_note` tool → actor=tool
- [x] `create_project` tool → actor=tool
- [x] `news_tools` set_news_pref → actor=tool

### Phase 5: Lineage query tool
- [x] Crear `app/provenance/lineage_tool.py` con `trace_data_origin` + `get_entity_history`
- [x] Registrar tools en `main.py` (gated por `provenance_enabled`)
- [x] Agregar categoría `"provenance"` en router.py + few-shot examples

### Phase 6: Tests + QA
- [x] Tests unitarios de AuditLogger (core, disabled, metadata, trace_id)
- [x] Test de memory versioning (auto-increment, disabled)
- [x] Test de entity history (all, filtered by type, filtered by actor)
- [x] Test de cleanup (>90 days)
- [x] Test de source_trace_id en add_memory/save_note
- [x] Test de lineage tool (full, no data, invalid type)
- [x] Test best-effort (closed connection doesn't crash)
- [x] `make check` (lint + typecheck + tests) — 739 passed, 0 errors

### Phase 7: Docs
- [x] Crear `docs/features/44-data_provenance.md`
- [x] Crear `docs/testing/44-data_provenance_testing.md`
- [x] Actualizar README indexes (features + testing + exec-plans)
- [x] Actualizar CLAUDE.md
