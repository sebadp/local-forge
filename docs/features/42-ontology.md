# Feature: Ontology Data Model / Knowledge Graph

> **Version**: v1.0
> **Fecha de implementación**: 2026-03-11
> **Fase**: Exec Plan 42
> **Estado**: Implementada

---

## Que hace?

El asistente ahora puede responder preguntas cruzadas entre entidades: "que se del proyecto X?", "que notas tengo relacionadas con Python?". En lugar de solo buscar por similitud semantica en memorias o notas por separado, el grafo de conocimiento conecta memorias, notas, proyectos y tareas mediante relaciones tipadas (pertenece a, referencia, extraido de, etc.), permitiendo que una sola query de texto devuelva entidades conectadas que el embedding search tradicional no hubiera encontrado.

---

## Arquitectura

```
[Mensaje del usuario]
        |
        v
[ConversationContext.build()]
        |
        +---> [EntityRegistry.search_entities(query)]   (text search sobre entities)
        |              |
        |              v
        |     [graph.traverse(entity_id, depth=1)]       (BFS sobre entity_relations)
        |              |
        |              v
        |     [enricher.enrich_context()]                (formato texto, budget 1000 chars)
        |              |
        v              v
[ctx.graph_enrichment: str]          (campo en ConversationContext, best-effort)
        |
        v
[search_knowledge_graph tool]        (activo cuando category="knowledge")
        |
        v
[LLM responde con entidades relacionadas]
```

La poblacion del grafo se hace via backfill al arrancar y via hooks por cada objeto nuevo:

```
[add_memory / save_note / create_project]
        |
        v
[EntityRegistry.upsert_entity()]     (INSERT OR IGNORE, idempotente)
        |
        v
[EntityRegistry.add_relation()]      (UNIQUE constraint, no duplicados)
```

---

## Archivos clave

| Archivo | Rol |
|---|---|
| `app/ontology/models.py` | `Entity`, `Relation`, `GraphResult`; listas de `ENTITY_TYPES` y `RELATION_TYPES` |
| `app/ontology/registry.py` | `EntityRegistry`: CRUD async sobre las tablas SQLite `entities` y `entity_relations` |
| `app/ontology/graph.py` | `traverse()` BFS + `find_by_query()`: busca entidades por texto y recorre el grafo |
| `app/ontology/extractor.py` | Extraccion de topicos, menciones y referencias a proyectos con regex (sin LLM) |
| `app/ontology/enricher.py` | `enrich_context()`: orquesta busqueda + traversal + formato, best-effort |
| `app/ontology/backfill.py` | `run_full_backfill()`: puebla el grafo desde datos existentes (memorias, notas, proyectos) |
| `app/skills/tools/ontology_tools.py` | Herramienta `search_knowledge_graph` registrada en `SkillRegistry` |
| `app/context/conversation_context.py` | Campo `graph_enrichment: str` populado en `build()` si `entity_registry` disponible |
| `app/database/db.py` | `ONTOLOGY_SCHEMA`: tablas `entities` y `entity_relations` creadas en `init_db()` |
| `app/main.py` | Inicializa `EntityRegistry`, registra tool y lanza backfill como background task |
| `app/config.py` | `ontology_enabled: bool = True` — gate para toda la funcionalidad |
| `app/dependencies.py` | `get_entity_registry(request)` — inyeccion via `app.state.entity_registry` |
| `scripts/backfill_ontology.py` | Script offline para backfill manual sin FastAPI |

---

## Walkthrough tecnico: como funciona

### Startup y poblacion inicial

1. **`init_db()` en `db.py`**: ejecuta `ONTOLOGY_SCHEMA` creando tablas `entities` y `entity_relations` con sus indices.
2. **`main.py` lifespan**: instancia `EntityRegistry(db_conn)` y lo guarda en `app.state.entity_registry`. Si `settings.ontology_enabled`, registra el tool `search_knowledge_graph` y lanza `_safe_ontology_backfill()` como `asyncio.create_task()` — no bloquea el startup.
3. **`backfill.run_full_backfill()`**: itera sobre memorias activas, notas y proyectos/tareas en SQLite. Por cada objeto llama `registry.upsert_entity()` (idempotente). Para tareas, tambien llama `registry.add_relation(task_id, "belongs_to", project_id)`.

### Por cada mensaje (Phase B en ConversationContext.build)

4. **`ConversationContext.build()`** recibe `entity_registry` como parametro opcional.
5. Tras completar las busquedas en paralelo de memorias/notas/historia, ejecuta best-effort:
   ```python
   enrichment = await enrich_context(entity_registry, user_text, budget_chars=1000)
   if enrichment.entities_found > 0:
       graph_enrichment = enrichment.extra_text
   ```
6. **`enricher.enrich_context()`** llama a `graph.find_by_query(registry, query, depth=1, limit=3)`:
   - Primero hace `registry.search_entities(query)` — `LIKE %query%` sobre la columna `name`.
   - Por cada entidad encontrada, llama `graph.traverse(registry, entity_id, depth=1)`.
   - `traverse()` hace BFS: obtiene vecinos via `get_neighbors()` (outgoing + incoming en `entity_relations`), capped a `_MAX_NODES_PER_HOP=10` por hop y `_MAX_RESULTS_PER_TYPE=5` por tipo.
   - Retorna `GraphResult.to_text()` respetando el presupuesto de chars.
7. El campo `ctx.graph_enrichment` queda disponible para el pipeline (actualmente almacenado en `ConversationContext`; la integracion en `_build_context()` como seccion XML es el siguiente paso natural).

### Tool calling: search_knowledge_graph

8. Cuando el clasificador asigna categoria `"knowledge"` (mensaje con "grafo de conocimiento", "que se de X", etc.), el tool loop incluye `search_knowledge_graph`.
9. El LLM llama al tool con `query`, `entity_types` y `depth`.
10. `ontology_tools.search_knowledge_graph()` llama directamente a `graph.find_by_query()` y formatea el resultado como texto legible con cabeceras por tipo de entidad.

### Extractor (sin LLM)

`extractor.py` provee funciones puras usadas por el backfill y por hooks futuros:
- `extract_topics(text)` — regex contra 10 patrones tematicos (programming, devops, debugging, etc.)
- `extract_mentions(text)` — regex `@\w+` para referencias a personas
- `extract_project_refs(text)` — regex `project|proyecto` seguido de nombre
- `extract_memory_name(content)` — primera linea no vacia, max 120 chars (usado para dar nombre a la entidad)

---

## Como extenderla

### Agregar un nuevo tipo de entidad

1. Agregar el string al tuple `ENTITY_TYPES` en `app/ontology/models.py`.
2. Crear la funcion de backfill correspondiente en `app/ontology/backfill.py` siguiendo el patron de `backfill_memories()`.
3. Llamar esa funcion desde `run_full_backfill()`.
4. Agregar el hook en el metodo de escritura del repository correspondiente.

### Agregar un nuevo tipo de relacion

1. Agregar el string al tuple `RELATION_TYPES` en `app/ontology/models.py`.
2. Llamar `registry.add_relation(source_id, "nuevo_tipo", target_id)` desde el punto donde se crea la relacion.

### Cambiar la profundidad del traversal

- El default de `enrich_context()` es `depth=1`. Se puede pasar `depth=2` para explorar dos hops.
- El tool `search_knowledge_graph` acepta `depth` como parametro (1-3, capped en el handler).
- Aumentar la profundidad incrementa la latencia proporcional al numero de entidades conectadas.

### Integrar graph_enrichment en el contexto LLM

`ctx.graph_enrichment` ya esta disponible en `ConversationContext`. Para inyectarlo en el sistema LLM, agregar en `_build_context()` de `router.py`:
```python
builder.add_section("knowledge_graph", ctx.graph_enrichment or None)
```
Y actualizar la firma de `_build_context()` para aceptar el campo.

---

## Guia de testing

Ver [`docs/testing/42-ontology_testing.md`](../testing/42-ontology_testing.md)

---

## Decisiones de diseno

| Decision | Alternativa descartada | Motivo |
|---|---|---|
| SQLite para el grafo | Neo4j / NetworkX en memoria | Zero dependencias extra; los grafos de uso personal son chicos (miles de nodos max); SQLite ya es el storage central |
| Best-effort en todo el pipeline | Fallar si el grafo no esta disponible | El grafo es un enriquecimiento opcional; la app debe funcionar identico sin el |
| Sin LLM en el hot path | LLM para extraer relaciones en tiempo real | Demasiada latencia por mensaje; el backfill + hooks es suficiente para el caso de uso |
| BFS con caps (`_MAX_NODES_PER_HOP=10`, `_MAX_RESULTS_PER_TYPE=5`) | BFS sin limite | Previene explosiones de cardinalidad en grafos densos; la mayoria de las queries relevantes estan a 1 hop |
| `UNIQUE(entity_type, ref_id)` en entities | UUID puro sin constraint | Garantiza idempotencia en el upsert sin logica adicional; `INSERT OR IGNORE` es O(1) |
| `UNIQUE(source_id, relation_type, target_id)` en entity_relations | Permitir duplicados | Previene explosion de relaciones en backfills repetidos |
| Backfill como background task en startup | Backfill bloqueante | No agrega latencia al arranque; si falla, se puede correr el script offline |

---

## Gotchas y edge cases

- **graph_enrichment no inyectado en LLM context todavia**: el campo existe en `ConversationContext` y se puebla, pero `_build_context()` en `router.py` no lo incluye aun como seccion XML. Esto es un integration point pendiente.
- **Entidades nuevas no se registran en tiempo real (sin hooks en repository)**: el backfill puebla entidades existentes al arrancar, pero las memorias/notas creadas despues del backfill no se agregan automaticamente al grafo. Los hooks en `add_memory`, `save_note`, `create_project` del repository son el siguiente paso de implementacion.
- **Text search es case-insensitive via LIKE**: `%query%` no usa FTS5, por lo que puede ser lento en tablas muy grandes. Para bases con >100K entidades considerar agregar `FTS5 virtual table`.
- **depth >= 2 puede ser lento**: cada hop hace N queries SQLite. Para depth=2 con 10 vecinos por nodo = hasta 100 queries adicionales. Mantener depth=1 en el hot path.
- **Backfill idempotente**: correr el backfill multiples veces es seguro gracias a `INSERT OR IGNORE` y las constraints `UNIQUE`. No hay riesgo de duplicados.

---

## Variables de configuracion relevantes

| Variable (`config.py`) | Default | Efecto |
|---|---|---|
| `ontology_enabled` | `True` | Activa/desactiva el registro del tool, el backfill al startup, y la inyeccion de `entity_registry` en `ConversationContext.build()` |
