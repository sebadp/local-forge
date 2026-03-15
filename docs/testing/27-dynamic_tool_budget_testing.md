# Testing: Dynamic Tool Budget & `request_more_tools`

> **Feature documentada**: [`docs/features/27-dynamic_tool_budget.md`](../features/27-dynamic_tool_budget.md)
> **Requisitos previos**: Container corriendo (`docker compose up -d`), modelos de Ollama disponibles (qwen3.5:9b).

---

## Tests automatizados

```bash
# Tests de distribución proporcional y schema del meta-tool
pytest tests/test_tool_router.py -v -k "distributes_budget or single_category_unchanged or request_more_tools"

# Test de expansión dinámica en el loop
pytest tests/test_tool_executor.py -v -k "request_more_tools"

# Suite completa para verificar ausencia de regresiones
make test
# Esperado: 441 passed, 23 skipped
```

---

## Tests manuales

### Test 1: Distribución de budget — dos categorías (bug fix regression)

**Setup**: ninguno especial.

```
Necesito crear una issue en GitHub para el proyecto "backend-api" que trackee el bug del login
```

**Esperado**:
- Logs: `Tool router: categories=['projects', 'github'], selected 8 tools: [...]`
- Ambas categorías representadas — al menos 1 tool de projects Y al menos 1 de github en la lista
- El LLM puede ejecutar acciones de ambas categorías sin pedir herramientas adicionales

**Verificar en logs**:
```bash
grep "Tool router: categories=\['projects', 'github'\]" data/localforge.log
# Esperado: "selected 8 tools: ['create_project'..., 'list_issues'...]"
# ANTES del fix solo veía tools de projects, nunca de github
```

---

### Test 2: Meta-tool presente en el inicio del loop

**Setup**: cualquier mensaje que active tools.

```
¿Qué hora es?
```

**Verificar en logs**:
```bash
grep "Tool router: categories\|selected.*tools" data/localforge.log | tail -5
```

**Esperado**: la lista de tools seleccionadas incluye `request_more_tools` como primera entrada.

```bash
# Alternativamente, via selfcode:
list_tool_categories  # via /agent o WhatsApp
```

---

### Test 3: `request_more_tools` — expansión dinámica cuando clasificador es incorrecto

**Setup**: enviar un mensaje deliberadamente ambiguo para que el clasificador lo enrute a una sola categoría, pero la tarea requiera otra.

```
Anotá en mis notas que tengo que revisar los PRs de hoy en el repo
```

*(El clasificador puede retornar solo `["notes"]` pero la tarea también requiere GitHub)*

**Esperado (si el clasificador falla)**:
- El LLM llama `request_more_tools(categories=["github"], reason="need PR tools")`
- Logs: `request_more_tools: cats=['github'], added=N: ['list_pull_requests', ...] (reason: 'need PR tools')`
- La siguiente iteración el LLM tiene acceso a las tools de github
- Respuesta final ejecuta ambas acciones

**Verificar en logs**:
```bash
grep "request_more_tools" data/localforge.log
# Esperado: "request_more_tools: cats=['github'], added=7: ['list_issues', 'create_issue', ...]"
```

---

### Test 4: `request_more_tools` con categoría ya cargada (dedup)

**Setup**: forzar una situación donde el LLM pida una categoría que ya está en el tool set.

```
Verificá mis notas y también guardá una nota nueva sobre el clima de hoy
```

*(Con suerte ambas acciones están en `["notes"]` ya cargado)*

Si el LLM llama `request_more_tools(categories=["notes"])`:

**Esperado**:
- Logs: `request_more_tools: cats=['notes'], added=0: [] (reason: ...)`
- Mensaje de tool: `"No new tools added (already available or unknown category)"`
- El loop continúa sin crash ni duplicados en el tool set

---

### Test 5: `request_more_tools` con categoría inválida

Difícil de forzar manualmente (el schema describe las categorías válidas). Si ocurre:

```
# Simular via logs: buscar intentos con categoría "foobar"
grep "request_more_tools.*added=0" data/localforge.log
```

**Esperado**: added=0, sin crash, loop continúa.

---

### Test 6: Una sola categoría — retrocompatibilidad

```
¿Cuánto es la raíz cuadrada de 144?
```

**Esperado**:
- Logs: `Tool router: categories=['math'], selected 1 tools: ['calculate']`
- `per_cat = max(2, 8//1) = 8` — sin cambio respecto al comportamiento previo
- El LLM calcula directamente sin pedir más tools

---

### Test 7: Muchas categorías — cap final

```
Dame la hora, el clima, busca noticias de tecnología, anota "reunión 3pm", y calcula 25 * 4
```

*(Debería clasificar como múltiples categorías: time, weather, news, notes, math)*

**Esperado**:
- Logs: `Tool router: categories=['time', 'weather', 'news', 'notes', 'math'], selected N tools`
- Con 5 categorías: `per_cat = max(2, 8//5) = 2` → hasta 2 tools por categoría → max 10 → capped a 8
- `len(selected tools) <= 8` — siempre

---

## Verificación en logs

```bash
# Budget distribution — buscar el nuevo patrón de selección
grep "Tool router: categories" data/localforge.log | tail -10

# Expansiones dinámicas
grep "request_more_tools" data/localforge.log

# Sin regresiones — verificar que no hay "selected 0 tools" para categorías válidas
grep "selected 0 tools" data/localforge.log  # No debe aparecer para categorías conocidas

# Combinado: ver todas las selecciones recientes
grep "Tool router\|request_more_tools" data/localforge.log | tail -20
```

---

## Queries de DB (N/A)

Esta feature opera completamente en memoria durante el loop de ejecución. No persiste datos en SQLite.

---

## Verificar graceful degradation

### Categoría desconocida

Si `TOOL_CATEGORIES` no tiene una categoría que el LLM pide:

1. `select_tools(["unknown_cat"], all_tools_map)` retorna `[]`
2. `request_more_tools` handler genera `added=[]`
3. Tool result: `"No new tools added (already available or unknown category)"`
4. Loop continúa con las tools anteriores — sin crash

### LLM agota iteraciones haciendo `request_more_tools` en loop

- `MAX_TOOL_ITERATIONS = 5` actúa como safety net
- Si el LLM llama `request_more_tools` en 5 iteraciones consecutivas sin responder:
  - La iteración final (`tools=None`) fuerza texto plano
  - Logs: `Max tool iterations (5) reached, forcing text response`

---

## Troubleshooting

| Problema | Causa probable | Solución |
|---|---|---|
| `Tool router: selected 8 tools: [solo tools de una categoría]` | Bug pre-fix (rara vez, si se revirtió el cambio) | Verificar que `select_tools()` usa `per_cat` y no el early-return viejo |
| `request_more_tools` no aparece en los logs aunque el LLM dice que necesita tools | qwen3.5:9b no llamó el meta-tool (decidió responder en texto) | Normal — el LLM es libre de responder directo. El meta-tool es un mecanismo de escape, no obligatorio |
| `request_more_tools: added=0` para una categoría existente | La categoría ya estaba en el tool set inicial | El comportamiento es correcto — dedup funcionando |
| Error en `_run_tool_call` para `request_more_tools` | El meta-tool llegó al handler normal en lugar del inline handler | Verificar que `REQUEST_MORE_TOOLS_NAME` está correctamente comparado en `executor.py` |
| Tests fallan con `ImportError: cannot import REQUEST_MORE_TOOLS_NAME` | La constante no fue exportada desde `router.py` | Verificar que `REQUEST_MORE_TOOLS_NAME` existe en `app/skills/router.py` al nivel de módulo |

---

## Variables relevantes para testing

| Variable (`.env`) | Valor de test | Efecto |
|---|---|---|
| No hay variable específica para esta feature | — | La distribución proporcional y el meta-tool son siempre activos |
| `OLLAMA_MODEL` | `qwen3.5:9b` | El modelo afecta si el LLM decide llamar `request_more_tools` o no |
