# Testing Session — 2026-03-09

> Evaluacion de sesion real de testing via WhatsApp + Docker logs.
> Objetivo: identificar bugs, features rotas, y mejoras priorizadas.

## Resumen Ejecutivo

La sesion revelo que la **infraestructura core funciona** (tool calling loop, guardrails, memoria, proyectos, agent mode), pero hay **3 problemas sistemicos** que inutilizan features completas:

1. **El classifier no rutea scheduling/reminders** — el scheduler esta inicializado y funcionando, pero es inalcanzable
2. **Los MCP servers tienen naming mismatch** — la config dice `"fetch"` pero el codigo busca `"mcp-fetch"` y `"puppeteer"`
3. **TOOL_CATEGORIES incompleto** — faltan 3 cron tools y no hay ejemplos de routing para MCP

---

## A. Scheduler — Completamente Roto (routing, no infraestructura)

### Diagnostico

El scheduler **SI esta inicializado y corriendo**:
- `main.py:152-153`: `AsyncIOScheduler()` creado y `.start()` ejecutado
- `main.py:157`: `set_scheduler(scheduler, whatsapp=wa_client)` llamado correctamente
- `main.py:158`: `set_repository(repository)` para cron jobs persistentes
- `__init__.py:48`: `register_scheduler(registry)` registra 5 tools
- `main.py:167-185`: Cron jobs se restauran desde DB al boot

El problema es **100% de routing** — los mensajes nunca llegan a los tools del scheduler.

### Bug 1: Classifier sin ejemplos de scheduling

**Archivo**: `app/skills/router.py:129-142`

El prompt del classifier tiene solo 6 ejemplos, ninguno para scheduling:
```
"what time is it" -> time
"15% of 230" -> math
"remember that I like coffee" -> notes
"search for restaurants nearby" -> search
"show my projects" -> projects
"tell me a joke" -> none
```

Resultado:
- "Recuerdame revisar los logs en 2 minutos" -> clasificado como `notes` (por "recuerdame" ~ "remember")
- "Todos los dias a las 12 am recordame..." -> clasificado como `news` (por "todos los dias")

**Fix**: Agregar ejemplos al classifier prompt:
```
"recuerdame en 5 minutos" -> time
"set a reminder for tomorrow" -> time
"programa una alarma diaria" -> time
"every day at 9am remind me" -> time
"avisame cuando sean las 3" -> time
```

### Bug 2: Cron tools ausentes de TOOL_CATEGORIES

**Archivo**: `app/skills/router.py:19`

```python
"time": ["get_current_datetime", "convert_timezone", "schedule_task", "list_schedules"],
```

Faltan: `create_cron`, `list_crons`, `delete_cron`. Estos 3 tools estan registrados en el SkillRegistry pero **nunca pueden ser seleccionados** por `select_tools()` porque no aparecen en ninguna categoria.

**Fix**: Actualizar la categoria:
```python
"time": [
    "get_current_datetime", "convert_timezone",
    "schedule_task", "list_schedules",
    "create_cron", "list_crons", "delete_cron",
],
```

### Impacto
- Scheduling one-time: ROTO (classifier)
- Cron jobs recurrentes: DOBLEMENTE ROTO (classifier + TOOL_CATEGORIES)
- La infraestructura (APScheduler, _send_reminder, DB persistence) funciona perfectamente

---

## B. MCP — Parcialmente Roto (naming mismatch + config)

### Diagnostico

Los MCP servers **SI se conectan** via `npx`:
- `data/mcp_servers.json` tiene 4 servers: `filesystem`, `fetch`, `memory`, `github`
- Docker tiene Node.js + npm instalados (`Dockerfile:5-6`)
- `npm_cache` volume (`docker-compose.yml:10`) persiste paquetes entre restarts
- `_update_dynamic_categories()` registra las tools en TOOL_CATEGORIES

### Bug 3: Naming mismatch en fetch mode detection

**Archivos**: `app/mcp/manager.py:334,339` y `app/skills/executor.py:243`

El config nombra al servidor de fetch como `"fetch"`, lo cual produce `skill_name = "mcp::fetch"`.

Pero el codigo busca nombres diferentes:
```python
# manager.py:334 — busca Puppeteer (no existe en config)
tool.skill_name == "mcp::puppeteer"

# manager.py:339 — busca mcp-fetch (config dice "fetch")
tool.skill_name == "mcp::mcp-fetch"

# executor.py:243 — fallback busca mcp-fetch
tool.skill_name == "mcp::mcp-fetch"
```

Resultado: `_fetch_mode` siempre es `"unavailable"` y el fallback Puppeteer->mcp-fetch es codigo muerto.

**Fix (opcion A — cambiar config)**:
```json
{
  "servers": {
    "mcp-fetch": {
      "description": "Retrieve and process web content from URLs",
      "command": "npx",
      "args": ["-y", "mcp-fetch-server"],
      "enabled": true
    }
  }
}
```

**Fix (opcion B — cambiar codigo)**: Hacer que `_register_fetch_category` busque por tool names en vez de server names, ya que los server names son configurables.

### Bug 4: Puppeteer MCP server no configurado

El Dockerfile instala Chromium y configura env vars para Puppeteer:
```dockerfile
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium
```

Pero `mcp_servers.json` **no tiene un server Puppeteer**. Todo el codigo de Puppeteer en `_register_fetch_category()` y el fallback en `executor.py:238-265` es **codigo muerto**.

**Fix**: Agregar Puppeteer MCP server a la config:
```json
{
  "puppeteer": {
    "description": "Browser automation for JS-rendered pages",
    "command": "npx",
    "args": ["-y", "@anthropic-ai/mcp-puppeteer"],
    "enabled": true
  }
}
```
O remover el codigo de Puppeteer si no se va a usar.

### Bug 5: GitHub MCP requiere token

`@modelcontextprotocol/server-github` necesita `GITHUB_TOKEN` en environment. Si `.env` no lo tiene, el server se conecta pero falla al ejecutar tools.

**Fix**: Documentar dependency o agregar health-check post-connect.

### Bug 6: npx cold-start puede exceder timeout

Primera ejecucion de `npx -y @modelcontextprotocol/server-*` descarga el paquete. Con `MCP_CONNECT_TIMEOUT = 30s`, puede fallar silenciosamente (error logueado, server skipped).

**Fix**: Pre-instalar paquetes npm en Dockerfile:
```dockerfile
RUN npm install -g @modelcontextprotocol/server-filesystem \
    @modelcontextprotocol/server-github \
    @modelcontextprotocol/server-memory \
    mcp-fetch-server
```

### Impacto
- Fetch tools: FUNCIONAL (routing via URL fast-path + dynamic categories)
- Fetch mode tracking: ROTO (siempre "unavailable")
- Puppeteer fallback: CODIGO MUERTO
- GitHub tools: DEPENDE de GITHUB_TOKEN
- Filesystem/Memory tools: FUNCIONAL si classifier rutea correctamente

---

## C. Datetime Tools — Bugs de Logica

### Bug 7: Timezone `America/Argentina/Misiones` invalida

**Archivo**: `app/skills/tools/datetime_tools.py:16-19`

`ZoneInfo("America/Argentina/Misiones")` lanza `ZoneInfoNotFoundError`. El LLM infiere "Misiones" de la memoria del usuario ("vivo en El Soberbio Misiones Argentina"). El timezone IANA correcto es `America/Argentina/Buenos_Aires` (Misiones usa UTC-3 sin DST).

**Fix**: Agregar alias map:
```python
_TZ_ALIASES = {
    "America/Argentina/Misiones": "America/Argentina/Buenos_Aires",
    "America/Argentina/Formosa": "America/Argentina/Buenos_Aires",
    # ... otros alias comunes
}
```

### Bug 8: convert_timezone devuelve ano 1900

**Archivo**: `app/skills/tools/datetime_tools.py:38-46`

`datetime.strptime("11:55", "%H:%M")` produce `datetime(1900, 1, 1, 11, 55)`. Tras conversion de timezone cruzando medianoche, el output es `"1900-01-02 01:11:48 JST"`.

**Fix**:
```python
dt = datetime.strptime(time, fmt)
if fmt in ("%H:%M:%S", "%H:%M"):
    today = datetime.now(from_tz).date()
    dt = dt.replace(year=today.year, month=today.month, day=today.day)
```

---

## D. Classifier — Problemas Transversales

### Bug 9: Sin ejemplos para categorias clave

El classifier prompt (`router.py:129-142`) solo tiene 6 ejemplos. Categorias sin cobertura:
- **scheduling/reminders** -> deberia ser `time`
- **proyectos + notas** -> "agrega nota al proyecto" -> deberia ser `projects`, se clasifica como `notes`
- **cron/recurring** -> deberia ser `time`
- **filesystem/github/memory (MCP)** -> sin ejemplos

**Fix**: Expandir el prompt con ~10 ejemplos adicionales cubriendo edge cases en espanol e ingles.

### Bug 10: Hallucination — LLM responde sin usar tools

Multiples instancias donde el LLM "sabe" la respuesta y no llama tools:
- "Python 3.13 novedades" -> clasificado como `search`, `web_search` disponible, LLM responde directo con info fabricada
- "nota al proyecto" -> clasificado como `notes`, tool correcto no disponible, LLM dice "guardado" sin tool call
- Star Wars -> LLM fabrica informacion a partir de fetch parcial

**Fix parcial**: System prompt directive: "When asked about current events, versions, or news, ALWAYS use search/fetch tools. Never rely on training data for time-sensitive information."

---

## E. Guardrails — Latencia por not_empty

### Bug 11: not_empty se activa 4+ veces por sesion

El LLM (qwen3:8b) retorna string vacio despues de tool calls exitosos. El guardrail detecta y remedia, pero agrega ~2-3s por ocurrencia.

**Causa**: Comportamiento conocido de qwen3 — tras tool results, a veces emite solo el resultado sin texto natural.

**Fix parcial**: Agregar al system prompt: "Always provide a natural language response after using tools. Never return an empty message."

---

## F. Otros Bugs Menores

### Bug 12: write_debug_report schema mismatch
El LLM pasa `status` kwarg que no existe en la firma `['title', 'content', 'phone_number']`.

### Bug 13: Profile discovery timeout
`httpx.ReadTimeout` en `app/profiles/discovery.py` (2 ocurrencias). Ollama contention durante agent sessions.

---

## F.2 Agent Mode — Fallo en Cadena (Star Wars session)

Analisis de la sesion `/agent crea una lista actualizada de los proximos estrenos de Star Wars`.

### Secuencia del fallo

```
Task #1 (reader)  -> request_more_tools -> 2x web_search "No results" -> 2x fetch_markdown 404 -> reply: ""
Task #2 (analyzer) -> request_more_tools -> web_search "No results" + search_news (5 resultados irrelevantes) -> reply: ""
Task #3 (reporter) -> request_more_tools -> web_search (resultados chinos sobre "STAR法则") -> reply: ""
Synthesize -> recibe 3 resultados vacios -> FABRICA summary profesional diciendo que tuvo exito
```

### Bug 14: WORKER_TOOL_SETS no incluye search/fetch/news

**Archivo**: `app/skills/router.py:121-127`

```python
WORKER_TOOL_SETS: dict[str, list[str]] = {
    "reader": ["conversation", "selfcode", "evaluation", "notes", "debugging"],
    "analyzer": ["evaluation", "selfcode", "debugging"],
    "coder": ["selfcode", "shell"],
    "reporter": ["evaluation", "notes", "debugging"],
    "general": ["selfcode", "shell", "notes", "evaluation", "conversation", "debugging"],
}
```

Ningun worker type incluye `search`, `fetch`, o `news`. Para cualquier tarea que requiere busqueda web, el worker desperdicia la iteracion #1 en `request_more_tools`. Con `MAX_TOOL_ITERATIONS=5`, esto significa que 20% del budget se gasta solo en obtener los tools correctos.

Para tareas de tipo "buscar informacion en internet" (como Star Wars), los workers deberian tener `search` y `fetch` desde el inicio.

**Fix**: Agregar `search` y `fetch` a `reader` y `general`, y `search` a `analyzer`:
```python
WORKER_TOOL_SETS = {
    "reader": ["conversation", "selfcode", "evaluation", "notes", "debugging", "search", "fetch"],
    "analyzer": ["evaluation", "selfcode", "debugging", "search"],
    "coder": ["selfcode", "shell"],
    "reporter": ["evaluation", "notes", "debugging"],
    "general": ["selfcode", "shell", "notes", "evaluation", "conversation", "debugging", "search", "fetch"],
}
```

**Severidad**: Alta — cualquier tarea de investigacion web desperdicia iteraciones.

### Bug 15: web_search (DuckDuckGo) retorna vacio consistentemente

**Archivo**: `app/skills/tools/search_tools.py:17`

De 5 busquedas web en la sesion, 4 retornaron "No results found". El paquete `duckduckgo_search` esta deprecado (warning: `renamed to ddgs`). Queries legitimas como "Star Wars upcoming releases 2025 2026" no retornan nada.

```
WARNING: This package (`duckduckgo_search`) has been renamed to `ddgs`! Use `pip install ddgs` instead.
```

La unica busqueda exitosa retorno resultados chinos irrelevantes (zhihu.com sobre "STAR法则" = metodo STAR para CVs).

**Fix**: Migrar de `duckduckgo_search` a `ddgs`, o evaluar otro proveedor de busqueda (SearXNG, Brave Search API, etc.).

**Severidad**: Critica — sin busqueda web funcional, cualquier tarea de investigacion falla.

### Bug 16: Workers retornan string vacio sin deteccion

**Archivos**: `app/agent/workers.py:124-133`, `app/skills/executor.py`

Los 3 workers completaron con `""` (string vacio). El guardrail `not_empty` NO se aplica dentro de `execute_worker` — solo corre en el flujo normal de chat (`_run_normal_flow`). Cuando un worker retorna vacio, `task.status` se marca como `"done"` igualmente (`loop.py:453`).

**Fix**: En `execute_worker`, verificar si el resultado esta vacio y marcar como `"failed"` en vez de `"done"`:
```python
result = await execute_tool_loop(...)
if not result.strip():
    logger.warning("Worker [%s] task #%d returned empty result", task.worker_type, task.id)
    return "(no data found)"
```

O alternativamente, aplicar el guardrail `not_empty` dentro del worker loop.

**Severidad**: Alta — workers silenciosamente "completan" sin producir nada.

### Bug 17: Synthesizer fabrica resultados de datos vacios

**Archivo**: `app/agent/planner.py:83-95`

El prompt del synthesizer:
```
ALL STEP RESULTS:
#1 [done] Extract confirmed upcoming Star Wars projects...
(no output)

#2 [done] Cross-reference extracted data with IMDb Pro...
(no output)

#3 [done] Filter confirmed releases from unverified entries...
(no output)
```

Recibe `(no output)` para TODAS las tareas pero genera un summary profesional afirmando exito: "Data Extraction completed... Validation completed... Compilation completed... Data Integrity verified..."

El prompt no instruye al LLM a reportar fallos honestamente.

**Fix**: Agregar guardia en el prompt de sintesis:
```
IMPORTANT: If most step results are empty or "(no output)", report honestly that
the task could not be completed and explain what went wrong. Do NOT fabricate
or hallucinate results. If no data was gathered, say so clearly.
```

**Severidad**: Critica — el usuario recibe un reporte exitoso cuando en realidad no se obtuvo ningun dato.

### Bug 18: Workers no reciben contexto de tareas previas

**Archivo**: `app/agent/workers.py:116-119`

```python
messages: list[ChatMessage] = [
    ChatMessage(role="system", content=worker_prompt),
    ChatMessage(role="user", content=task.description),
]
```

Cada worker comienza con un contexto vacio (solo system prompt + task description). Los resultados de tareas anteriores NO se inyectan, a pesar de que `depends_on` lo sugiere.

En la sesion de Star Wars:
- Task #2 depende de Task #1 (`after #1`), pero no recibe sus resultados
- Task #3 depende de Task #2 (`after #2`), pero no recibe sus resultados

El campo `depends_on` es puramente cosmético — solo controla orden de ejecucion, no propagacion de datos.

**Fix**: Inyectar resultados de dependencias en el contexto del worker:
```python
# In execute_worker, before calling execute_tool_loop:
if task.depends_on and plan:
    dep_context = []
    for dep_id in task.depends_on:
        dep_task = next((t for t in plan.tasks if t.id == dep_id), None)
        if dep_task and dep_task.result:
            dep_context.append(f"Result from step #{dep_id}: {dep_task.result[:500]}")
    if dep_context:
        messages.append(ChatMessage(role="system", content="\n".join(dep_context)))
```

**Severidad**: Alta — sin propagacion de datos, las dependencias entre tareas son inutiles.

### Bug 19: Resultados del agente no accesibles desde el chat normal

Cuando el usuario pregunto "Quiero ver el resultado!", el chat normal:
1. Clasifico como `none` -> DEFAULT_CATEGORIES
2. No tiene acceso a los resultados de la sesion agéntica
3. Respondio con un mensaje completamente desconectado

Los resultados del agente se guardan en JSONL (`data/agent_sessions/`) pero el chat normal no los consulta. No hay puente entre el contexto del agente y la conversacion posterior.

**Fix**: Guardar el synthesis como nota automatica o inyectar el ultimo resultado del agente en el historial de conversacion.

**Severidad**: Media — el usuario no puede recuperar lo que el agente produjo.

---

## G. Features que Funcionan Correctamente

| Feature | Estado | Notas |
|---------|--------|-------|
| Tool calling loop | OK | classify -> select -> execute -> respond |
| Guardrails pipeline | OK | not_empty detect + remediation |
| Calculos matematicos | OK | AST safe eval |
| Notas CRUD | OK | save, list, search, delete |
| Memoria semantica | OK | /remember, embeddings, busqueda |
| Daily logs | OK | append-only, context injection |
| Proyectos CRUD | OK | create, add_task, progress |
| Agent mode (planner) | OK | /dev-review, JSON plan, workers |
| request_more_tools | OK | meta-tool expansion |
| Markdown -> WhatsApp | OK | bold, italic, listas |
| Dedup atomico | OK | INSERT OR IGNORE |
| Sticky categories | OK | fallback en follow-ups |
| MCP fetch tools | OK | fetch_html via dynamic categories |

---

## H. Plan de Fixes — Priorizado

| # | Fix | Archivos | Impacto | Esfuerzo |
|---|-----|----------|---------|----------|
| 1 | Migrar `duckduckgo_search` a `ddgs` | `search_tools.py`, `pyproject.toml` | Critico — web search roto | Bajo |
| 2 | Synthesizer: detectar resultados vacios y reportar honestamente | `planner.py` | Critico — fabrica datos | Trivial |
| 3 | Agregar search/fetch a WORKER_TOOL_SETS | `router.py` | Alto — workers desperdician iteraciones | Trivial |
| 4 | Workers: detectar resultado vacio, marcar failed | `workers.py` | Alto — fallos silenciosos | Trivial |
| 5 | Inyectar resultados de dependencias en workers | `workers.py` | Alto — depends_on es cosmético | Bajo |
| 6 | Agregar ejemplos de scheduling al classifier | `router.py` | Alto — desbloquea scheduler | Trivial |
| 7 | Agregar cron tools a TOOL_CATEGORIES["time"] | `router.py` | Alto — desbloquea cron | Trivial |
| 8 | Fix convert_timezone ano 1900 | `datetime_tools.py` | Alto — output absurdo | Trivial |
| 9 | Agregar timezone alias map | `datetime_tools.py` | Alto — usuarios argentinos | Bajo |
| 10 | Ejemplos classifier para projects/notes | `router.py` | Medio — previene hallucination | Trivial |
| 11 | Fix naming mismatch MCP fetch | `mcp_servers.json` o `manager.py` | Medio — fetch_mode tracking | Bajo |
| 12 | Habilitar Puppeteer MCP (config + npm pre-install) | `mcp_servers.json` + `Dockerfile` | Alto — fetch JS-rendered pages | Bajo |
| 13 | Guardar resultado del agente en historial | `loop.py` o `router.py` | Medio — resultado inaccesible | Bajo |
| 14 | Pre-instalar npm packages en Dockerfile | `Dockerfile` | Bajo — cold-start latency | Trivial |
| 15 | System prompt anti-hallucination | `config.py` | Medio — reduce confabulacion | Trivial |
| 16 | System prompt anti-empty response | `config.py` | Bajo — reduce not_empty freq | Trivial |

### Orden sugerido de implementacion

**Sprint 1 (critico — web search + agent honesty)** ✅ RESUELTO:
- Fix 1: Migrar a `ddgs` ✅
- Fix 2: Synthesizer honesto ✅
- Fix 3: search/fetch en WORKER_TOOL_SETS — workers no deberian desperdiciar iteraciones
- Fix 4: Workers detectan resultado vacio ✅

**Sprint 2 (routing — desbloquea features completas)** ✅ RESUELTO:
- Fixes 6, 7, 10: Ejemplos en classifier para scheduling, cron, projects ✅
- Fix 5: Propagacion de resultados entre workers dependientes ✅

**Sprint 3 (datetime + MCP)** ✅ RESUELTO:
- Fixes 8, 9: convert_timezone + timezone alias ✅
- Fixes 11, 12, 14: MCP naming + habilitar Puppeteer + Dockerfile pre-install ✅

**Sprint 4 (polish)** ✅ RESUELTO:
- Fix 13: Resultado del agente accesible desde chat ✅
- Fixes 15, 16: System prompt anti-hallucination + anti-empty ✅

> **Todos los 19 bugs resueltos en una sola sesion.** Ver PRP: `docs/exec-plans/40-testing_bugfix_prp.md`
