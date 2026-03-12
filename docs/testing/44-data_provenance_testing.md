# Testing: Data Provenance & Lineage (Plan 44)

## Tests automatizados

**Archivo**: `tests/test_provenance.py` (18 tests)

### AuditLogger core
- `test_log_mutation_and_retrieve` — CREATE + retrieve audit entry
- `test_log_mutation_disabled` — no entries when `enabled=False`
- `test_multiple_mutations` — multiple actions on same entity, order DESC
- `test_log_mutation_with_metadata` — JSON metadata serialized correctly
- `test_log_mutation_with_trace_id` — source_trace_id persisted

### Memory versioning
- `test_version_memory` — auto-incrementing versions, correct content/actor
- `test_version_memory_disabled` — no versions when disabled

### Entity history
- `test_get_entity_history_all` — unfiltered query
- `test_get_entity_history_filtered_by_type` — filter by entity_type
- `test_get_entity_history_filtered_by_actor` — filter by actor

### Cleanup
- `test_cleanup_old_entries` — deletes entries >90 days old, keeps recent

### Repository integration
- `test_add_memory_with_source_trace_id` — new column works
- `test_save_note_with_source_trace_id` — new column works

### Lineage tools
- `test_lineage_tool_trace_data_origin` — full history with versions
- `test_lineage_tool_no_data` — graceful empty response
- `test_lineage_tool_invalid_type` — validation error
- `test_entity_history_tool` — browse all mutations

### Best-effort
- `test_audit_logger_does_not_raise` — closed connection doesn't crash

## Testing manual

### Caso 1: /remember + trace_data_origin
1. Enviar `/remember my favorite language is Python`
2. Enviar "de donde salió esa memoria?"
3. Verificar que el LLM use `trace_data_origin` y muestre "CREATE by user"

### Caso 2: Consolidación
1. Agregar varias memorias similares con `/remember`
2. Trigger consolidación (enviar suficientes mensajes para flush)
3. Verificar audit log muestra "MERGE by llm_consolidator"

### Caso 3: MEMORY.md edit
1. Editar `data/MEMORY.md` manualmente (agregar una línea)
2. Verificar en logs que el watcher sincroniza
3. Usar `get_entity_history` tool — debe mostrar "CREATE by file_sync"

### Verificación en DB
```sql
-- Ver últimas 10 mutaciones
SELECT * FROM entity_audit_log ORDER BY id DESC LIMIT 10;

-- Ver versiones de una memoria
SELECT * FROM memory_versions WHERE memory_id = ? ORDER BY version;

-- Memorias con source_trace_id
SELECT id, content, source_trace_id FROM memories WHERE source_trace_id IS NOT NULL;
```
