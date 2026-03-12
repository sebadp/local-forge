# Testing Manual: Ontology Data Model / Knowledge Graph

> **Feature documentada**: [`docs/features/42-ontology.md`](../features/42-ontology.md)
> **Requisitos previos**: Container corriendo (`docker compose up -d`), modelos de Ollama disponibles.

---

## Verificar que la feature está activa

Al arrancar el container, buscar en los logs:

```bash
docker compose logs -f localforge | head -80
```

Confirmar las siguientes líneas:
- `"Langfuse v3 tracing enabled"` o `"Langfuse tracing enabled"` (si configurado)
- `"Ontology backfill completed: {'memory': N, 'note': M, 'project': K}"` — confirma que el grafo fue poblado
- `"search_knowledge_graph"` registrado en herramientas disponibles

---

## Casos de prueba principales

| Mensaje / Acción | Resultado esperado |
|---|---|
| "¿qué sé sobre Python?" | El asistente usa `search_knowledge_graph` y devuelve memorias + notas relacionadas con Python |
| "busca en el grafo de conocimiento todo lo de [nombre proyecto]" | Traversal del grafo, resultado con entidades de tipo project, task, memory |
| "¿qué proyectos mencioné la semana pasada?" | Busca en entidades + relaciones, puede no tener datos si no hay relaciones temporales |
| `/remember Prefiero usar FastAPI` → luego "qué frameworks prefiero?" | La memoria de FastAPI aparece en el grafo, `search_knowledge_graph` la conecta con el topic "api_development" |

---

## Verificar el grafo directamente (DB)

```bash
# Contar entidades por tipo
sqlite3 data/localforge.db "SELECT entity_type, COUNT(*) as n FROM entities GROUP BY entity_type ORDER BY n DESC;"

# Ver relaciones creadas
sqlite3 data/localforge.db "SELECT relation_type, COUNT(*) as n FROM entity_relations GROUP BY relation_type;"

# Ver entidades recientes
sqlite3 data/localforge.db "SELECT entity_type, name, created_at FROM entities ORDER BY created_at DESC LIMIT 20;"

# Ver relaciones con nombres de entidades
sqlite3 data/localforge.db "
SELECT e1.name, er.relation_type, e2.name
FROM entity_relations er
JOIN entities e1 ON e1.id = er.source_id
JOIN entities e2 ON e2.id = er.target_id
LIMIT 20;
"

# Verificar que tareas tienen relación belongs_to con sus proyectos
sqlite3 data/localforge.db "
SELECT e_task.name, 'belongs_to', e_proj.name
FROM entity_relations er
JOIN entities e_task ON e_task.id = er.source_id
JOIN entities e_proj ON e_proj.id = er.target_id
WHERE er.relation_type = 'belongs_to'
LIMIT 10;
"
```

---

## Edge cases y validaciones

| Escenario | Resultado esperado |
|---|---|
| Backfill con DB vacía (nuevos usuarios) | `backfill complete: {'memory': 0, 'note': 0, 'project': 0}` — sin errores |
| Backfill repetido (varios restarts) | Idempotente: los conteos no crecen porque `INSERT OR IGNORE` previene duplicados |
| `ontology_enabled=false` en .env | No se registra `search_knowledge_graph`, no se lanza backfill, `graph_enrichment` queda vacío |
| `search_knowledge_graph` con query sin matches | Responde `"No entities found for '...'"` sin error |
| Depth=3 en grafo sparse | Retorna pocos resultados (quizás vacíos) — normal, no error |

---

## Verificar en logs

```bash
# Backfill al arrancar
docker compose logs localforge 2>&1 | grep -i "ontology"

# Entity registration (si hooks implementados)
docker compose logs localforge 2>&1 | grep -i "entity"

# Graph enrichment en Phase B (si activado)
docker compose logs localforge 2>&1 | grep -i "graph_enrichment\|enrichment"

# Errores del grafo (best-effort, no deberían detener la app)
docker compose logs localforge 2>&1 | grep -i "ontology.*error\|graph.*error"
```

---

## Verificar graceful degradation

**Escenario 1: Grafo no inicializado**

1. Setear `ONTOLOGY_ENABLED=false` en `.env`
2. Reiniciar: `docker compose restart localforge`
3. Verificar que la app arranca sin errores
4. Mensajes normales siguen funcionando — `graph_enrichment` queda vacío
5. El tool `search_knowledge_graph` no aparece en el tool loop

**Escenario 2: Tablas de ontología corruptas**

1. El `init_db()` recrea las tablas con `CREATE TABLE IF NOT EXISTS` — no falla
2. Un backfill fallido (ej. DB locked) no detiene la app — solo logea `"Ontology backfill failed (non-critical)"`

---

## Correr backfill manualmente

```bash
# Dentro del container
docker compose exec localforge python scripts/backfill_ontology.py --db data/localforge.db

# O local con venv
.venv/bin/python scripts/backfill_ontology.py --db data/localforge.db
```

---

## Troubleshooting

| Problema | Causa probable | Solución |
|---|---|---|
| `"Ontology backfill completed: {'memory': 0, ...}"` | DB vacía o usuario nuevo | Normal — el grafo se puebla con el uso |
| El tool `search_knowledge_graph` no aparece | `ontology_enabled=False` o skill no registrada | Verificar config y restart |
| `graph_enrichment` siempre vacío | `entity_registry` no pasa al `ConversationContext.build()` | Ver logs de `main.py` en startup; verificar `app.state.entity_registry` |
| Relaciones no se crean automáticamente | Los hooks en `add_memory`/`save_note` no están activos todavía | Correr backfill manual para poblar desde datos existentes |
| Queries lentas con depth=2 | Muchas entidades, N queries por hop | Usar depth=1 por defecto; depth=2 solo en tool calling directo |

---

## Variables relevantes para testing

| Variable (`.env`) | Valor de test | Efecto |
|---|---|---|
| `ONTOLOGY_ENABLED` | `true/false` | Activa/desactiva toda la feature |
