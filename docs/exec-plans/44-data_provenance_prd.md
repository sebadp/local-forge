# PRD: Data Provenance & Lineage

> **Origen:** Gap 2.2 del [Plan de Arquitectura](42-architecture_action_plan.md) (Palantir AIP Gap Analysis)
> **Depende de:** Plan 42 (Ontology Data Model) ✅

## Objetivo y Contexto

No sabemos de dónde vino cada dato. ¿Esta memoria fue extraída de qué conversación? ¿Quién la modificó — el usuario, el consolidator, el LLM? ¿Esta nota fue creada manualmente o por un tool call?

La falta de provenance impide:
- **Debugging**: cuando el asistente dice algo incorrecto, no hay forma de trazar la fuente
- **Confianza**: el usuario no puede verificar por qué el sistema "cree" algo
- **Auditoría**: no hay registro de quién/qué modificó datos (flush, consolidator, comando manual)
- **Evolución**: sin lineage, no podemos medir la calidad de las extracciones automáticas

El entity graph (Plan 42) provee la infraestructura de relaciones. Este plan agrega la dimensión temporal y causal: quién hizo qué, cuándo, y por qué.

## Alcance

### In Scope

1. **Audit log de entidades**: tabla `entity_audit_log` que registra cada mutación (CREATE, UPDATE, DELETE, MERGE) sobre memorias, notas y proyectos
2. **Source trace linking**: campo `source_trace_id` en memorias y notas — FK a `traces(id)` para trazar hasta la conversación/tool call original
3. **Actor tracking**: cada mutación registra el actor (`user`, `llm_flush`, `llm_consolidator`, `command`, `agent`, `system`)
4. **Memory versioning**: tabla `memory_versions` append-only — cada update crea nueva versión, la anterior se marca como superseded
5. **Lineage query tool**: tool `trace_data_origin` para el LLM — "¿de dónde vino esta memoria/nota?"
6. **Entity graph relations**: relaciones `DERIVED_FROM`, `MODIFIED_BY`, `EXTRACTED_FROM` en el ontology graph

### Out of Scope

- UI/dashboard de lineage (solo queries programáticas y via tool)
- Provenance de mensajes de chat (ya tienen conversation_id + timestamp)
- Provenance cross-user (single-tenant por diseño)
- Rollback automático a versiones anteriores (solo lectura del historial)

## Casos de Uso Críticos

1. **"¿Por qué crees que mi GitHub es X?"** → El sistema traza: memoria #42 → extraída por `llm_flush` → de conversación #15 el 2025-01-10 (trace `abc123`)
2. **"¿Quién cambió esta memoria?"** → Audit log muestra: creada por `user` via `/remember`, modificada por `llm_consolidator` (merge con duplicado), trace_id del consolidator run
3. **Debugging de memorias incorrectas** → Developer consulta `entity_audit_log` para ver la cadena de mutaciones y encontrar el punto donde se introdujo el error
4. **Métricas de calidad de extracción** → Cuántas memorias extraídas por `llm_flush` sobreviven sin ser corregidas/eliminadas por el usuario

## Modelo de Datos

### entity_audit_log
```sql
CREATE TABLE entity_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,        -- 'memory', 'note', 'project', 'project_task'
    entity_id INTEGER NOT NULL,       -- FK al registro afectado
    action TEXT NOT NULL,             -- 'CREATE', 'UPDATE', 'DELETE', 'MERGE', 'SUPERSEDE'
    actor TEXT NOT NULL,              -- 'user', 'llm_flush', 'llm_consolidator', 'command', 'agent', 'system'
    source_trace_id TEXT,            -- FK a traces(id), nullable
    before_snapshot TEXT,            -- JSON del estado anterior (nullable para CREATE)
    after_snapshot TEXT,             -- JSON del estado posterior (nullable para DELETE)
    metadata_json TEXT DEFAULT '{}', -- contexto adicional (e.g. merge_source_ids, command_name)
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_audit_entity ON entity_audit_log(entity_type, entity_id);
CREATE INDEX idx_audit_actor ON entity_audit_log(actor);
CREATE INDEX idx_audit_trace ON entity_audit_log(source_trace_id);
```

### memory_versions
```sql
CREATE TABLE memory_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL,       -- FK a memories(id)
    version INTEGER NOT NULL,         -- 1, 2, 3...
    content TEXT NOT NULL,
    actor TEXT NOT NULL,
    source_trace_id TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(memory_id, version)
);
CREATE INDEX idx_memver_memory ON memory_versions(memory_id);
```

### Cambios a tablas existentes
```sql
ALTER TABLE memories ADD COLUMN source_trace_id TEXT;
ALTER TABLE notes ADD COLUMN source_trace_id TEXT;
```

### Relaciones en entity graph
- `memory → EXTRACTED_FROM → conversation` (cuando flush extrae una memoria)
- `memory → DERIVED_FROM → memory` (cuando consolidator hace merge)
- `note → CREATED_BY_TOOL → trace` (cuando un tool call crea una nota)

## Restricciones Arquitectónicas

- **Best-effort**: audit logging nunca debe bloquear ni fallar el flujo principal (mismo patrón que tracing)
- **Performance**: INSERT a audit log debe ser async background task, no en critical path
- **Storage**: snapshots JSON pueden crecer — considerar retention policy (e.g. 90 días)
- **Backward compatible**: memorias/notas existentes tendrán `source_trace_id = NULL` — aceptable
- **Actor enum**: no enforced a nivel SQL (TEXT), pero validado a nivel app con constantes

## Métricas de Éxito

| Métrica | Target |
|---|---|
| Cobertura de audit: % de mutaciones con audit entry | >95% |
| Cobertura de trace linking: % de memorias nuevas con source_trace_id | >80% |
| Latencia adicional por audit log | <5ms (async) |
| Query "¿de dónde vino X?": respuesta exitosa | >90% de casos con provenance |
