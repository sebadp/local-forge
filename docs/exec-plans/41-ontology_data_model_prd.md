# PRD: Ontology Data Model

## Objetivo y Contexto

### El Problema

WasAP almacena datos en silos desconectados:

```
memories ──── (texto plano, sin relaciones)
notes ──────── (título + contenido, sin contexto)
projects ───── (tareas + actividad, sin link a memorias/notas)
messages ──── (historial crudo, sin link a entidades)
user_profiles ─ (JSON blob, sin estructura)
daily_logs ──── (archivos .md, sin link a DB)
```

Cuando un usuario dice "¿qué hablamos sobre el proyecto X?", el LLM tiene que:
1. Buscar en memorias (por texto, no por relación)
2. Buscar en notas (por texto)
3. Buscar en proyectos (por nombre)
4. Buscar en historial (por contenido)
5. **Adivinar** qué resultados están relacionados entre sí

No hay un **grafo de relaciones** que conecte estas entidades. El contexto se pierde entre sesiones, y la búsqueda semántica sola no captura relaciones estructurales (ej: "esta memoria fue extraída de esta conversación, que trataba sobre este proyecto").

### Qué es una Ontology

Inspirado en Palantir AIP: un **modelo semántico unificado** que modela los "sustantivos" (entidades) y "verbos" (relaciones/acciones) del dominio en una forma legible tanto para humanos como para agentes.

En nuestro contexto:
- **Entidades** (sustantivos): Memory, Note, Project, Task, Conversation, Message, Person, Topic
- **Relaciones** (verbos): `extracted_from`, `related_to`, `mentioned_in`, `belongs_to`, `created_by`, `about_topic`
- **Acciones**: búsqueda cross-entity, traversal de relaciones, context enrichment automático

### Por Qué Importa

1. **Contexto más rico sin más tokens**: en vez de inyectar 10 memorias sueltas, inyectar "el cluster de entidades relacionadas con la query del usuario"
2. **Respuestas más precisas**: "todo sobre el proyecto X" devuelve memorias + notas + conversaciones + tareas — no solo lo que coincide por texto
3. **Base para provenance**: las relaciones _son_ el lineage (esta memoria `extracted_from` esta conversación)
4. **Búsqueda relacional**: "¿qué proyectos mencioné la semana pasada?" es un traversal, no una búsqueda de texto
5. **Deduplicación inteligente**: detectar que 3 memorias hablan del mismo tema via topic clusters

---

## Alcance

### In Scope (MVP)

- **Entity Registry**: tabla unificada `entities(id, type, ref_id, name, created_at)` que indexa todas las entidades existentes
- **Relation Graph**: tabla `entity_relations(source_id, relation_type, target_id, confidence, source_trace_id, created_at)` — relaciones tipadas entre entidades
- **Topic Extraction**: extracción automática de topics desde memorias, notas y mensajes (regex + embeddings clustering, sin LLM en hot path)
- **Cross-Entity Search**: `search_related(entity_id, depth=1)` — traversal BFS de relaciones desde una entidad
- **Contextual Enrichment**: al buscar memorias para context, también traer notas y proyectos relacionados via el grafo
- **Backfill**: script para poblar el entity graph desde datos existentes
- **Tool**: `search_knowledge_graph(query, entity_types, depth)` para el LLM

### Out of Scope (v1)

- Graph database dedicada (Neo4j, etc.) — SQLite es suficiente para nuestro volumen
- UI de visualización del grafo (futuro)
- Relaciones probabilísticas con ML (v1 usa heurísticas + embeddings)
- Federation con data sources externos
- Ontology schema editor (el schema es código)

---

## Modelo de Datos

### Entidades

```sql
-- Registro unificado de todas las entidades del sistema
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,                -- UUID
    entity_type TEXT NOT NULL,          -- 'memory', 'note', 'project', 'task', 'conversation', 'topic', 'person'
    ref_id TEXT NOT NULL,               -- FK al ID en la tabla original (memories.id, notes.id, etc.)
    name TEXT NOT NULL,                 -- Display name (extracto, título, nombre)
    metadata_json TEXT DEFAULT '{}',    -- Atributos extra (JSON)
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(entity_type, ref_id)        -- No duplicar: un memory.id = una entity
);
CREATE INDEX idx_entities_type ON entities(entity_type);
CREATE INDEX idx_entities_name ON entities(name);
```

### Relaciones

```sql
-- Grafo de relaciones entre entidades
CREATE TABLE IF NOT EXISTS entity_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES entities(id),
    relation_type TEXT NOT NULL,        -- Ver catálogo abajo
    target_id TEXT NOT NULL REFERENCES entities(id),
    confidence REAL DEFAULT 1.0,        -- 0.0-1.0, para relaciones inferidas
    source_trace_id TEXT,               -- Traza que originó esta relación (provenance)
    metadata_json TEXT DEFAULT '{}',    -- Contexto extra
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(source_id, relation_type, target_id)  -- No duplicar relaciones
);
CREATE INDEX idx_relations_source ON entity_relations(source_id);
CREATE INDEX idx_relations_target ON entity_relations(target_id);
CREATE INDEX idx_relations_type ON entity_relations(relation_type);
```

### Vector Index (opcional, para topic similarity)

```sql
-- Embeddings de entidades para clustering y búsqueda semántica cross-entity
CREATE VIRTUAL TABLE IF NOT EXISTS vec_entities USING vec0(
    entity_id TEXT PRIMARY KEY,
    embedding float[768]
);
```

### Catálogo de Relaciones

| Relation Type | Source → Target | Descripción | Cómo se crea |
|---|---|---|---|
| `extracted_from` | Memory → Conversation | Memoria extraída de esta conversación | Auto: en `add_memory()` |
| `mentioned_in` | Entity → Message | Entidad mencionada en este mensaje | Auto: NER/regex en mensajes |
| `belongs_to` | Task → Project | Tarea pertenece a proyecto | Auto: ya existe en `project_tasks.project_id` |
| `related_to` | Entity → Entity | Relación semántica genérica | Auto: embedding similarity > threshold |
| `about_topic` | Entity → Topic | Entidad trata sobre este tema | Auto: topic extraction |
| `created_by` | Entity → Person | Quién creó la entidad | Auto: en creation handlers |
| `supersedes` | Memory → Memory | Memoria nueva reemplaza a anterior | Auto: en `consolidate_memories()` |
| `references` | Note → Project | Nota referencia a un proyecto | Auto: name matching en contenido |
| `derived_from` | Entity → Trace | Entidad creada por esta traza | Auto: `source_trace_id` |

---

## Arquitectura

### Componente Principal: `app/ontology/`

```
app/ontology/
  models.py          # Entity, Relation, TopicCluster dataclasses
  registry.py        # EntityRegistry — CRUD de entities + relations
  graph.py           # GraphTraversal — BFS/DFS search, path finding
  extractor.py       # TopicExtractor — regex + embedding clustering
  enricher.py        # ContextEnricher — dado un query, enriquecer con grafo
  backfill.py        # BackfillJob — poblar el grafo desde datos existentes
```

### Flujo: Cómo se puebla el grafo

```
                    ┌─────────────────────┐
                    │   _handle_message()  │
                    └─────────┬───────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        save_message()   add_memory()   save_note()
              │               │               │
              ▼               ▼               ▼
     ┌────────────┐  ┌────────────┐  ┌────────────┐
     │ upsert     │  │ upsert     │  │ upsert     │
     │ entity     │  │ entity     │  │ entity     │
     │ (message)  │  │ (memory)   │  │ (note)     │
     └─────┬──────┘  └─────┬──────┘  └─────┬──────┘
           │               │               │
           ▼               ▼               ▼
     ┌─────────────────────────────────────────┐
     │  Background: extract_relations()         │
     │  - mentioned_in (NER/regex)              │
     │  - extracted_from (trace_id context)     │
     │  - about_topic (topic extraction)        │
     │  - related_to (embedding similarity)     │
     └─────────────────────────────────────────┘
```

**Reglas clave:**
- Entity upsert es **síncrono** y ligero (INSERT OR IGNORE) — no agrega latencia al hot path
- Relation extraction es **background task** (via `BackgroundTasks` de FastAPI)
- Embedding-based relations son **best-effort** (como embeddings existentes)
- El grafo es **append-only** para relations (soft delete via `confidence=0.0`)

### Flujo: Cómo se consulta el grafo

```
User: "¿qué sé sobre el proyecto X?"
                │
                ▼
     ┌──────────────────┐
     │ search_entities() │  ← búsqueda por nombre + embedding
     │ type='project'    │
     └────────┬─────────┘
              │ entity_id = "proj_123"
              ▼
     ┌──────────────────┐
     │ traverse_graph()  │  ← BFS depth=2
     │ from: proj_123    │
     └────────┬─────────┘
              │
              ▼
     ┌─────────────────────────────────────┐
     │ Related entities:                    │
     │  - 3 memories (extracted_from convs) │
     │  - 2 notes (references project)      │
     │  - 5 tasks (belongs_to project)      │
     │  - 1 topic ("backend refactor")      │
     │  - 2 conversations (mentioned_in)    │
     └─────────────────────────────────────┘
              │
              ▼
     ┌──────────────────┐
     │ ContextEnricher   │  ← Rankea por relevancia + recency
     │ budget: 2000 chars│
     │ output: sections  │
     └──────────────────┘
```

---

## Integración con Sistema Existente

### 1. Context Engineering (Phase B)

**Antes:**
```python
# Phase B — búsquedas independientes
memories, notes, summary, history, projects = await asyncio.gather(
    search_memories(query_embedding),
    search_notes(query_embedding),
    get_summary(conv_id),
    get_recent_messages(conv_id),
    get_projects_summary(phone),
)
```

**Después:**
```python
# Phase B — búsquedas + enrichment via grafo
memories, notes, summary, history, projects = await asyncio.gather(
    search_memories(query_embedding),
    search_notes(query_embedding),
    get_summary(conv_id),
    get_recent_messages(conv_id),
    get_projects_summary(phone),
)

# Enrichment pass: usar el grafo para traer entidades relacionadas
# que la búsqueda semántica no encontró
enriched = await context_enricher.enrich(
    query=user_text,
    found_entities=[*memories, *notes],  # lo que ya tenemos
    budget_chars=1500,                    # presupuesto de tokens adicional
)
# enriched.extra_memories, enriched.extra_notes, enriched.related_projects
```

### 2. Memory System

El `add_memory()` en Repository se extiende:
```python
async def add_memory(self, content, category, phone_number=None, trace_id=None):
    memory_id = await self._insert_memory(content, category)

    # Register entity + relations (background-safe)
    entity_id = await self._upsert_entity('memory', memory_id, content[:100])
    if trace_id:
        trace_entity = await self._get_entity_by_ref('trace', trace_id)
        if trace_entity:
            await self._add_relation(entity_id, 'derived_from', trace_entity.id,
                                     source_trace_id=trace_id)
    # Topic extraction queued as background task
    return memory_id
```

### 3. Tool: `search_knowledge_graph`

```python
async def search_knowledge_graph(query: str, entity_types: str = "all", depth: int = 1) -> str:
    """Search the knowledge graph for entities and their relationships.

    Args:
        query: Natural language search query
        entity_types: Comma-separated types to search (memory,note,project,topic) or "all"
        depth: How many relationship hops to traverse (1-3)
    """
```

Registrado en categoría `"memory"` (ya existente), disponible en flujo normal y agéntico.

### 4. Consolidator

El memory consolidator (`app/memory/consolidator.py`) se beneficia del grafo:
- Detectar memorias que `about_topic` el mismo tema → candidatas a merge
- Detectar cadenas `supersedes` largas → compactar
- El LLM recibe el cluster de entidades relacionadas como contexto para consolidar mejor

---

## Casos de Uso Críticos

### 1. "¿Qué sé sobre X?"
- Buscar entidades cuyo `name` o contenido matchee "X"
- Traversar grafo depth=2 desde las entidades encontradas
- Retornar cluster organizado: memorias directas, notas relacionadas, proyectos vinculados, contexto de conversaciones

### 2. "Recordar algo sobre un proyecto"
- `add_memory("El deploy se hizo el viernes", category="general")`
- Background: detectar mención de "deploy" → buscar proyecto con deploy en nombre/tareas
- Crear relación `memory → references → project`
- Próxima vez que se consulte el proyecto, esta memoria aparece automáticamente

### 3. Deduplicación cross-entity
- Memoria: "Juan prefiere Python"
- Nota: "Stack técnico de Juan: Python, FastAPI"
- Proyecto: "Migración a Python" con tarea asignada a Juan
- El grafo conecta las 3 via topic "Python" + person "Juan"
- El consolidator puede detectar redundancia y sugerir merge

### 4. Context Enrichment automático
- Usuario pregunta sobre "el bug del webhook"
- Búsqueda semántica encuentra 1 memoria relevante
- Grafo traversal encuentra: 2 conversaciones anteriores sobre webhooks, 1 nota técnica, 1 proyecto con tareas relacionadas
- ContextEnricher incluye los más relevantes dentro del budget de tokens

### 5. Provenance query
- "¿Por qué crees que me gusta Python?"
- Buscar memoria "le gusta Python" → relación `extracted_from` → conversación del 2026-01-15
- Respuesta: "Lo mencionaste en nuestra conversación del 15 de enero cuando hablamos de tu stack preferido"

---

## Restricciones Arquitectónicas

1. **SQLite only**: No agregar Neo4j ni otra DB. SQLite con índices + BFS en Python escala hasta ~100K entidades sin problema. Si crecemos más allá, evaluar SQLite con recursive CTEs (`WITH RECURSIVE`).

2. **Zero latencia en hot path**: Entity upsert es INSERT OR IGNORE (< 1ms). Relation extraction es background task. El grafo se consulta en Phase B (ya es I/O-bound, unos ms extra son insignificantes).

3. **Best-effort** (patrón existente): Errores en entity/relation ops → logueados, nunca propagados. La app funciona sin ontology igual que funciona sin embeddings.

4. **Backward compatible**: Las tablas existentes (`memories`, `notes`, `projects`, etc.) NO se modifican. La ontology es una **capa de indexación encima**, no un reemplazo. Si se borra la tabla `entities`, todo sigue funcionando — solo pierde las relaciones.

5. **Sin LLM en hot path**: Topic extraction y relation inference usan regex + embedding similarity. El LLM solo participa en consolidation (ya existente, background).

6. **Confidence scoring**: Relaciones inferidas tienen `confidence < 1.0`. Relaciones explícitas (extracted_from, belongs_to) tienen `confidence = 1.0`. El enricher prioriza por confidence.

---

## Métricas de Éxito

| Métrica | Medición | Target |
|---|---|---|
| Entity coverage | `count(entities) / (count(memories) + count(notes) + count(projects))` | ≥ 95% |
| Relation density | `count(relations) / count(entities)` | ≥ 2.0 (promedio 2 relaciones por entidad) |
| Cross-entity search recall | "queries multi-entidad que retornan resultados de ≥2 tipos" | ≥ 80% |
| Context enrichment hit rate | "veces que el enricher agrega algo útil que la búsqueda semántica no encontró" | ≥ 30% |
| Latencia Phase B | Overhead del enrichment pass | < 50ms adicionales |
| Goal completion (agent) | Mejora en sesiones que involucran queries cross-entity | +15% vs baseline |

---

## Riesgos y Mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| Relaciones ruidosas (false positives) | Alta | Medio | Threshold de confidence conservador (0.7), prune periódico |
| Grafo crece indefinidamente | Media | Bajo | TTL en relaciones inferidas (90 días), cleanup en cron job |
| Topic extraction imprecisa | Media | Medio | Empezar con regex conservador, iterar con feedback |
| Overhead de backfill | Baja | Bajo | Backfill como script offline, no en startup |
| Complejidad de queries BFS | Baja | Medio | Cap depth=3, limit results per hop=10 |

---

## Fases de Implementación (Estimado)

### Phase 1: Schema + Entity Registry (~3h)
- Schema en `db.py`
- `app/ontology/models.py` + `app/ontology/registry.py`
- Tests unitarios del registry

### Phase 2: Relation Extraction (~4h)
- `app/ontology/extractor.py` (topic extraction, relation inference)
- Hooks en `add_memory()`, `save_note()`, `create_project()` — background
- Tests

### Phase 3: Graph Traversal + Search Tool (~3h)
- `app/ontology/graph.py` (BFS, path finding)
- Tool `search_knowledge_graph`
- Tests de traversal

### Phase 4: Context Enrichment (~3h)
- `app/ontology/enricher.py`
- Integración en Phase B de `_run_normal_flow()`
- Tests de enrichment + token budget

### Phase 5: Backfill + Documentation (~2h)
- `scripts/backfill_ontology.py`
- Feature doc + testing doc
- Update CLAUDE.md + AGENTS.md

---

## Apéndice: Comparación con Palantir Ontology

| Aspecto | Palantir | WasAP (propuesta) |
|---|---|---|
| Escala | Billions de objetos | Miles (personal assistant) |
| Storage | Distributed (Spark/Flink) | SQLite + indices |
| Schema | Dynamic (UI editor) | Static (code-defined) |
| Relaciones | First-class con UI | SQL table + BFS en Python |
| Actions | Ontology-native | Tool calls existentes |
| Security | Role/marking/purpose based | Single-user (phone-based) |
| Query | Ontology query language | SQL + Python traversal |

La diferencia clave es de **escala**, no de **concepto**. Ambas ontologías modelan entidades + relaciones + acciones. La nuestra es SQLite-native y single-user, lo cual es apropiado para un asistente personal.
