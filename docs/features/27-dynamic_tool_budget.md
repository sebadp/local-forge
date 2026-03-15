# Feature: Dynamic Tool Budget & `request_more_tools`

> **Versión**: v1.0
> **Fecha de implementación**: 2026-02-25
> **Fase**: Agent Mode
> **Estado**: ✅ Implementada

---

## ¿Qué hace?

Corrige un bug de producción en la selección de herramientas y agrega un mecanismo de escape dinámico. Antes, cuando el asistente necesitaba herramientas de dos categorías distintas (por ejemplo, proyectos + GitHub), la primera categoría agotaba el budget de 8 slots dejando a la segunda con cero herramientas. Ahora el budget se distribuye proporcionalmente. Además, si el clasificador inicial elige categorías incorrectas, el LLM puede llamar `request_more_tools(categories=[...])` para cargar las herramientas que necesita en el siguiente ciclo, sin reiniciar la conversación.

---

## Arquitectura

```
[Usuario: "Crea una issue en GitHub para el proyecto X"]
        │
        ▼
[classify_intent] → ["projects", "github"]
        │
        ▼
[select_tools — distribución proporcional]
        │   per_cat = max(2, 8 // 2) = 4
        │   ├── projects: create_project, list_projects, add_task, update_task  (4)
        │   └── github:   list_issues, create_issue, get_file_contents, list_pull_requests  (4)
        │   total: 8 tools ≤ max_tools
        ▼
[execute_tool_loop]
        │   tools = [request_more_tools (meta)] + [8 tools seleccionadas]
        │
        │   ┌─── Camino normal: LLM usa tools disponibles directamente
        │   │
        │   └─── Camino de escape (clasificador incorrecto):
        │           LLM llama request_more_tools(categories=["github"])
        │                   │
        │                   ▼
        │           [handler inline en executor]
        │                   │── select_tools(["github"], all_tools_map)
        │                   │── merge en tools (dedup por nombre)
        │                   │── logger.info("request_more_tools: added=N")
        │                   └── ChatMessage(role="tool", content="Loaded N tools: ...")
        │                   │
        │                   ▼
        │           [siguiente iteración: LLM tiene las tools correctas]
        │
        ▼
[respuesta al usuario]
```

---

## Archivos clave

| Archivo | Rol |
|---|---|
| `app/skills/router.py` | `select_tools()` (distribución proporcional), `REQUEST_MORE_TOOLS_NAME`, `build_request_more_tools_schema()` |
| `app/skills/executor.py` | Prepend del meta-tool, handler inline de `request_more_tools` en `execute_tool_loop()` |
| `app/webhook/router.py` | `_build_capabilities_section()` — nota al LLM sobre expansión dinámica |
| `tests/test_tool_router.py` | Tests de distribución proporcional + schema del meta-tool |
| `tests/test_tool_executor.py` | Test de expansión dinámica en el loop |
| `docs/exec-plans/27-dynamic_tool_budget_prd.md` | Decisiones arquitectónicas y contexto |
| `docs/exec-plans/27-dynamic_tool_budget_prp.md` | Plan de ejecución con checkboxes |

---

## Walkthrough técnico: cómo funciona

### Bug original (pre-fix): budget starvation

1. Clasificador devuelve `["projects", "github"]` → `select_tools()` itera `projects` (10 tools)
2. El loop interno añade tools hasta `len(selected) >= max_tools` → `return selected` temprano
3. GitHub nunca se procesa → LLM recibe 0 tools de GitHub → presenta un plan sin ejecutar

### Fix: distribución proporcional en `select_tools()` (`app/skills/router.py:221`)

```
per_cat = max(2, max_tools // len(categories))
```

- Con 2 categorías y `max_tools=8`: `per_cat = max(2, 4) = 4` → cada categoría recibe hasta 4 tools
- Con 1 categoría: `per_cat = max(2, 8) = 8` → retrocompatible, sin cambio de comportamiento
- Con 5 categorías: `per_cat = max(2, 1) = 2` → 2 tools por categoría → capped por `[:max_tools]`
- Si una categoría tiene menos tools que `per_cat`, toma todo lo que tenga y el resto se "desperdicia" (no redistribuye entre otras categorías — la siguiente iteración ajusta)

### Meta-tool `request_more_tools` — schema (`app/skills/router.py:268`)

`build_request_more_tools_schema(available_categories)` genera un schema Ollama estándar:

```json
{
  "type": "function",
  "function": {
    "name": "request_more_tools",
    "description": "Request additional tool categories... Available categories: ...",
    "parameters": {
      "categories": { "type": "array", "items": {"type": "string"}, "required": true },
      "reason":     { "type": "string" }
    }
  }
}
```

La lista de categorías se incluye en la descripción para que qwen3.5:9b sepa exactamente cuáles puede pedir.

### Prepend del meta-tool en `execute_tool_loop()` (`app/skills/executor.py:285`)

```python
meta_tool_schema = build_request_more_tools_schema(list(TOOL_CATEGORIES.keys()))
tools = [meta_tool_schema] + tools
```

El meta-tool está SIEMPRE en posición 0, independientemente de las categorías seleccionadas. El LLM lo ve en cada iteración sin ocupar espacio del budget de categorías.

### Handler inline de `request_more_tools` (`app/skills/executor.py:330`)

Dentro del loop de iteraciones, antes de ejecutar tool calls regulares:

1. **Separación por índice**: los calls se dividen en `meta_indices` y `regular_indices` usando `enumerate(response.tool_calls)` — preserva el orden original para el append de resultados
2. **Meta calls (secuenciales)**: para cada índice en `meta_indices`:
   - Extrae `categories` y `reason` de los argumentos
   - Llama `select_tools(categories, all_tools_map)` para obtener los nuevos schemas
   - Hace merge dedup: solo agrega tools cuyo `name` no existe ya en `tools`
   - Genera `ChatMessage(role="tool", content="Loaded N tools: ...")` como resultado
3. **Regular calls (paralelas)**: `asyncio.gather(*[_run_tool_call(response.tool_calls[i], ...) for i in regular_indices])`
4. **Append en orden original**: `working_messages.extend(tool_result_map[i] for i in sorted(tool_result_map))`

**Crítico**: `request_more_tools` NO pasa por `PolicyEngine` ni `AuditTrail`. Es meta-infraestructura — no ejecuta código externo, solo modifica el estado del loop.

---

## Cómo extenderla

- **Para cambiar el budget por categoría**: modificar `max_tools` en `execute_tool_loop()` (parámetro, default 8)
- **Para deshabilitar `request_more_tools`**: remover las líneas de prepend + el bloque de `meta_indices` en el executor; el meta-tool es aditivo, no rompe nada si se elimina
- **Para redistribuir budget sobrante entre categorías**: en `select_tools()`, hacer un segundo pass sobre categorías que no llenaron su `per_cat` con el budget sobrante de las que sí lo hicieron
- **Para limitar qué categorías puede pedir el LLM**: filtrar `available_categories` antes de llamar `build_request_more_tools_schema()` en el executor

---

## Guía de testing

→ Ver [`docs/testing/27-dynamic_tool_budget_testing.md`](../testing/27-dynamic_tool_budget_testing.md)

---

## Decisiones de diseño

| Decisión | Alternativa descartada | Motivo |
|---|---|---|
| Distribución proporcional (`max_tools // N`) | Aumentar cap de 8 a 16 | El cap mayor no resuelve el bug cuando el clasificador elige categorías incorrectas; más tools degradan accuracy en qwen3.5:9b |
| `per_cat = max(2, ...)` con mínimo de 2 | Sin mínimo | Garantiza que incluso con 8+ categorías, cada una tenga al menos 2 representantes |
| Meta-tool manejado inline (no en SkillRegistry) | Registrar como tool normal | El handler modifica el estado del loop (`tools` list) — no puede ser una tool normal que retorna solo un string |
| Meta-tool NO pasa por PolicyEngine | Evaluación de seguridad estándar | Es meta-infraestructura; solo redirige qué tools están disponibles, no ejecuta comandos externos |
| Categorías listadas en la descripción del schema | Prompt separado al LLM | qwen3.5:9b toma mejores decisiones cuando ve las opciones directamente en el schema de la tool |
| Tool-RAG embedding-based descartado (por ahora) | Búsqueda semántica de tools por embedding | Con ~50 tools actuales el beneficio no justifica la complejidad; documentado en PRD para cuando tools > 50 |

---

## Gotchas y edge cases

- **Una sola categoría**: `per_cat = max(2, 8//1) = 8` → comportamiento idéntico al pre-fix. Sin regresión.
- **Categoría con menos tools que `per_cat`**: toma todo lo que tiene. El budget sobrante NO se redistribuye a otras categorías en este ciclo (simplicidad > optimización).
- **LLM llama `request_more_tools` con categoría inexistente**: `select_tools(["nonexistent"], ...)` retorna `[]` → `added = []` → confirmación "No new tools added". No crash.
- **LLM llama `request_more_tools` con una categoría ya cargada**: la dedup por nombre evita duplicados. Confirmación "No new tools added". No crash.
- **`request_more_tools` consume una iteración**: el loop sigue siendo de MAX_TOOL_ITERATIONS=5. Un abuso del meta-tool podría reducir iteraciones disponibles para las tools reales. En práctica, qwen3.5:9b lo usa como máximo una vez por conversación.
- **Orden del resultado preservado**: si el LLM llama `request_more_tools` y una tool regular en el mismo turno, los resultados se appendean en el orden original de `response.tool_calls` via `tool_result_map[i]`.
- **`build_request_more_tools_schema` ordena las categorías**: `sorted(available_categories)` para output determinístico — útil para tests y reproducibilidad.

---

## Variables de configuración relevantes

| Variable (`config.py`) | Default | Efecto |
|---|---|---|
| `max_tools` (parámetro de `execute_tool_loop`) | `8` | Budget total de tools por iteración (sin contar el meta-tool) |
| No hay variable env específica | — | El comportamiento es siempre activo; no hay feature flag para deshabilitarlo |
