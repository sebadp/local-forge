# PRP: Testing Session Bugfix Sprint

Ref: [`40-testing_bugfix_prd.md`](40-testing_bugfix_prd.md)
Testing report: [`docs/testing/40-testing_session_2026_03_09.md`](../testing/40-testing_session_2026_03_09.md)

## Archivos a Modificar

| Archivo | Cambio |
|---------|--------|
| `pyproject.toml` | `duckduckgo-search` -> `ddgs` |
| `app/skills/tools/search_tools.py` | Import `ddgs` en vez de `duckduckgo_search` |
| `app/skills/tools/news_tools.py` | Import `ddgs` en vez de `duckduckgo_search` |
| `tests/test_search_tools.py` | Actualizar import check |
| `tests/test_news_tools.py` | Actualizar import check |
| `app/agent/planner.py` | Synthesizer prompt: honestidad ante datos vacios |
| `app/agent/workers.py` | Empty result detection + dependency context injection |
| `app/agent/loop.py` | Worker empty result -> mark failed; agent result al historial |
| `app/agent/models.py` | (si necesario) `TaskStep` — acceso a plan desde worker |
| `app/skills/router.py` | TOOL_CATEGORIES + classifier prompt + WORKER_TOOL_SETS |
| `app/skills/tools/datetime_tools.py` | Timezone aliases + convert_timezone year fix |
| `app/config.py` | System prompt directives |
| `data/mcp_servers.json` | Renombrar `"fetch"` -> `"mcp-fetch"` |
| `app/mcp/manager.py` | Sin cambios — codigo de Puppeteer ya esta listo |
| `app/skills/executor.py` | Sin cambios — fallback Puppeteer->mcp-fetch ya esta listo |
| `Dockerfile` | Pre-install npm packages |

---

## Fases de Implementacion

### Phase 1: Web Search (Critico — desbloquea agent mode)

Bugs: #15 (ddgs migration)

- [x] **1.1** En `pyproject.toml`: cambiar `"duckduckgo-search>=7.0"` a `"ddgs>=7.0"`
- [x] **1.2** En `app/skills/tools/search_tools.py`: cambiar `from duckduckgo_search import DDGS` a `from ddgs import DDGS`
- [x] **1.3** En `app/skills/tools/news_tools.py`: cambiar `from duckduckgo_search import DDGS` a `from ddgs import DDGS`
- [x] **1.4** En `tests/test_search_tools.py`: actualizar `pytest.importorskip("duckduckgo_search")` a `pytest.importorskip("ddgs")`
- [x] **1.5** En `tests/test_news_tools.py`: actualizar `pytest.importorskip("duckduckgo_search")` a `pytest.importorskip("ddgs")`
- [x] **1.6** Correr `pip install ddgs` y verificar que `DDGS().text()` y `DDGS().news()` funcionan (API identica)
- [x] **1.7** Correr `make check` (pass — mypy errors pre-existentes en repository.py/loop.py, no nuevos)

### Phase 2: Agent Honesty (Critico — dejar de fabricar datos)

Bugs: #16 (empty workers), #17 (synthesizer hallucination), #18 (depends_on cosmetico)

**Nota**: `think=True` se habilita para los 3 llamados del planner (create, replan, synthesize).
Los `<think>` tags ya se stripean automaticamente en `OllamaClient.chat_with_tools()` (client.py:94-98),
asi que el JSON parser recibe contenido limpio. La regla `think=False` aplica a prompts binarios (yes/no),
no al planner que requiere razonamiento complejo. `tools=None` en los 3 llamados, sin conflicto con qwen3.

- [x] **2.1** En `app/agent/planner.py` — habilitar `think=True` en los 3 llamados del planner:
  Cambiar `think=False` a `think=True` en las 6 ocurrencias (2 por cada funcion: con trace y sin trace):
  - `create_plan()` lineas 205, 218
  - `replan()` lineas 292, 302
  - `synthesize()` lineas 381, 391

  Los `<think>` tags se stripean automaticamente en `OllamaClient.chat_with_tools()` (client.py:94-98).
  El JSON parser de `_parse_plan_json` usa `text.find("{")` como fallback, que ignora cualquier
  residuo pre-JSON. Para `synthesize` (output libre, no JSON), el strip es transparente.

  Beneficio directo: el LLM razona antes de generar el plan/sintesis, lo que mitiga la
  fabricacion de datos (Bug #17) — la chain-of-thought fuerza al modelo a evaluar si
  realmente tiene datos antes de afirmar exito.

- [x] **2.2** En `app/agent/planner.py` — modificar `_SYNTHESIZE_SYSTEM_PROMPT`:
  Agregar despues de "Keep it under 500 words":
  ```
  IMPORTANT: If step results are empty, "(no output)", or contain only error messages,
  report HONESTLY that the task could not be completed. Explain what was attempted and
  what failed. Do NOT fabricate, invent, or hallucinate results. Never claim success
  if the data was not actually gathered.
  ```

- [x] **2.3** En `app/agent/workers.py` — `execute_worker()`: detectar resultado vacio y retornar sentinel:
  ```python
  result = await execute_tool_loop(...)
  if not result or not result.strip():
      logger.warning("Worker [%s] task #%d returned empty result", task.worker_type, task.id)
      return "(no data found — all tool calls returned empty or failed)"
  return result
  ```

- [x] **2.4** En `app/agent/workers.py` — `execute_worker()`: inyectar resultados de dependencias.
  Cambiar la firma para aceptar `plan: AgentPlan | None = None`:
  ```python
  async def execute_worker(
      task: TaskStep,
      objective: str,
      ...,
      plan: AgentPlan | None = None,
  ) -> str:
  ```
  Antes de llamar a `execute_tool_loop`, inyectar contexto de dependencias:
  ```python
  if task.depends_on and plan:
      dep_lines = []
      for dep_id in task.depends_on:
          dep_task = next((t for t in plan.tasks if t.id == dep_id), None)
          if dep_task and dep_task.result:
              dep_lines.append(
                  f"--- Result from step #{dep_id} ({dep_task.description[:60]}) ---\n"
                  f"{dep_task.result[:800]}"
              )
      if dep_lines:
          messages.append(ChatMessage(
              role="system",
              content="CONTEXT FROM PREVIOUS STEPS:\n" + "\n\n".join(dep_lines),
          ))
  ```

- [x] **2.5** En `app/agent/loop.py` — `_run_planner_session()`: pasar `plan` a `execute_worker()`:
  En ambos call sites (con trace y sin trace), agregar `plan=plan` al llamado:
  ```python
  result = await execute_worker(
      task=task,
      objective=plan.objective,
      ...,
      plan=plan,  # <-- agregar
  )
  ```

- [x] **2.6** En `app/agent/loop.py` — `_run_planner_session()`: marcar como failed si worker retorna sentinel:
  Despues de `task.result = result`:
  ```python
  if "(no data found" in result:
      task.status = "failed"
  else:
      task.status = "done"
  ```

- [x] **2.7** Escribir test para synthesizer con resultados vacios:
  Test en `tests/test_agent.py` que verifique que `synthesize()` con tasks vacias NO produce
  un summary exitoso (mock ollama para verificar que el prompt incluye la clausula de honestidad)

- [x] **2.8** Escribir test para `execute_worker` con resultado vacio:
  Mock `execute_tool_loop` retornando `""` → verificar que retorna sentinel
  Mock `execute_tool_loop` retornando texto → verificar que retorna el texto

- [x] **2.9** Correr `make check`

### Phase 3: Routing — Classifier y TOOL_CATEGORIES

Bugs: #1 (classifier scheduling), #2 (cron tools), #9 (classifier projects), #14 (WORKER_TOOL_SETS)

- [x] **3.1** En `app/skills/router.py` — `TOOL_CATEGORIES["time"]`: agregar cron tools:
  ```python
  "time": [
      "get_current_datetime", "convert_timezone",
      "schedule_task", "list_schedules",
      "create_cron", "list_crons", "delete_cron",
  ],
  ```

- [x] **3.2** En `app/skills/router.py` — `WORKER_TOOL_SETS`: agregar search/fetch:
  ```python
  WORKER_TOOL_SETS: dict[str, list[str]] = {
      "reader": ["conversation", "selfcode", "evaluation", "notes", "debugging", "search", "fetch", "news"],
      "analyzer": ["evaluation", "selfcode", "debugging", "search", "news"],
      "coder": ["selfcode", "shell"],
      "reporter": ["evaluation", "notes", "debugging"],
      "general": ["selfcode", "shell", "notes", "evaluation", "conversation", "debugging", "search", "fetch"],
  }
  ```

- [x] **3.3** En `app/skills/router.py` — `_CLASSIFIER_PROMPT_TEMPLATE`: expandir ejemplos.
  Agregar despues de `'"tell me a joke" -> none\n\n'`:
  ```python
  '"recuerdame en 5 minutos" -> time\n'
  '"set a reminder for tomorrow at 3pm" -> time\n'
  '"programa un cron diario a las 9" -> time\n'
  '"agrega una nota al proyecto X" -> projects\n'
  '"what are the latest news about AI" -> news\n'
  '"busca informacion sobre Python 3.13" -> search\n'
  ```

- [x] **3.4** Actualizar tests de classify_intent si existen:
  Verificar que "recuerdame en 5 minutos" clasifica como `time` (con mock de LLM)

- [x] **3.5** Correr `make check`

### Phase 4: Datetime Tools

Bugs: #7 (timezone alias), #8 (year 1900)

- [x] **4.1** En `app/skills/tools/datetime_tools.py` — agregar timezone alias map al inicio del modulo:
  ```python
  _TZ_ALIASES: dict[str, str] = {
      "America/Argentina/Misiones": "America/Argentina/Buenos_Aires",
      "America/Argentina/Formosa": "America/Argentina/Buenos_Aires",
      "America/Argentina/Chaco": "America/Argentina/Buenos_Aires",
      "America/Argentina/Entre_Rios": "America/Argentina/Buenos_Aires",
      "America/Argentina/Corrientes": "America/Argentina/Buenos_Aires",
      "America/Argentina/Santa_Fe": "America/Argentina/Cordoba",
      "America/Argentina/Neuquen": "America/Argentina/Salta",
  }
  ```

- [x] **4.2** En `get_current_datetime()` — resolver alias antes de ZoneInfo:
  ```python
  timezone = _TZ_ALIASES.get(timezone, timezone)
  ```

- [x] **4.3** En `convert_timezone()` — resolver alias para ambos timezones:
  ```python
  from_timezone = _TZ_ALIASES.get(from_timezone, from_timezone)
  to_timezone = _TZ_ALIASES.get(to_timezone, to_timezone)
  ```

- [x] **4.4** En `convert_timezone()` — fix year 1900 para time-only inputs:
  Despues de `dt = datetime.strptime(time, fmt)`, agregar:
  ```python
  if fmt in ("%H:%M:%S", "%H:%M"):
      today = datetime.now(from_tz).date()
      dt = dt.replace(year=today.year, month=today.month, day=today.day)
  ```

- [x] **4.5** Escribir tests:
  - `test_timezone_alias_resolved` — verificar que "America/Argentina/Misiones" resuelve
  - `test_convert_timezone_today_date` — verificar que time-only input usa fecha de hoy
  - `test_convert_timezone_full_datetime` — verificar que datetime completo no se altera

- [x] **4.6** Correr `make check`

### Phase 5: MCP — Habilitar Puppeteer + fix naming

Bugs: #3 (naming mismatch), #4 (Puppeteer), #6 (cold-start)

**Contexto**: Puppeteer y ddgs resuelven problemas complementarios:
- `ddgs` = search engine (descubrimiento de URLs) — Phase 1
- Puppeteer = browser rendering (contenido JS-heavy) — esta Phase
- `mcp-fetch` = plain HTTP fallback (cuando Puppeteer falla)

La infraestructura Docker ya esta lista: Chromium instalado, env vars seteadas,
el codigo en `manager.py` y `executor.py` detecta y usa Puppeteer automaticamente.
Solo falta la config + npm pre-install.

- [x] **5.1** En `data/mcp_servers.json` — renombrar server `"fetch"` a `"mcp-fetch"`:
  ```json
  "mcp-fetch": {
      "description": "Plain HTTP web content retrieval (fallback)",
      "command": "npx",
      "args": ["-y", "mcp-fetch-server"],
      "enabled": true
  }
  ```
  Esto hace que `skill_name == "mcp::mcp-fetch"` matchee con `manager.py:339` y `executor.py:243`.

- [x] **5.2** En `data/mcp_servers.json` — agregar Puppeteer MCP server:
  ```json
  "puppeteer": {
      "description": "Browser automation for JS-rendered pages (primary fetch)",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-puppeteer"],
      "enabled": true
  }
  ```
  El `McpManager` auto-detecta las 4 tools (`puppeteer_navigate`, `puppeteer_screenshot`,
  `puppeteer_evaluate`, `puppeteer_click`) via `session.list_tools()`.
  `_register_fetch_category()` las registra bajo categoria `"fetch"` y setea
  `_fetch_mode = "puppeteer"`.

- [x] **5.3** En `Dockerfile` — pre-instalar TODOS los MCP npm packages (incluyendo Puppeteer):
  Agregar ANTES de la linea `USER appuser` (root necesario para npm global install):
  ```dockerfile
  # Pre-install MCP server packages to avoid npx cold-start timeouts
  RUN npm install -g @modelcontextprotocol/server-puppeteer \
      @modelcontextprotocol/server-filesystem \
      @modelcontextprotocol/server-github \
      @modelcontextprotocol/server-memory \
      mcp-fetch-server
  ```

- [x] **5.4** Configurar Puppeteer sandbox para Docker:
  - `Dockerfile`: `ENV PUPPETEER_LAUNCH_OPTIONS='{"args":["--no-sandbox","--disable-setuid-sandbox"]}'`
  - `Dockerfile`: `ENV ALLOW_DANGEROUS=true` (requerido por `@modelcontextprotocol/server-puppeteer`)
  - `data/mcp_servers.json`: campo `"env": {"ALLOW_DANGEROUS": "true"}` en el server puppeteer
  - Nota: `--no-sandbox` es seguro dentro de un container Docker (el container ES el sandbox).
  - Verificacion runtime pendiente de Docker rebuild.

- [x] **5.5** Config lista para validar en logs que el boot muestra:
  ```
  MCP initialized: N server(s), M tool(s), fetch_mode=puppeteer
  ```
  Con `fetch_mode=puppeteer` (no `unavailable`). El fallback a `mcp-fetch` se activa
  automaticamente via `executor.py:238-265` si Puppeteer falla en runtime.
  Verificacion runtime pendiente de Docker deploy.

- [x] **5.6** Correr `make check` (no code changes in app/, only config + Dockerfile)

### Phase 6: System Prompt + Agent Result Bridge

Bugs: #10 (hallucination), #11 (not_empty), #19 (agent result inaccessible)

- [x] **6.1** En `app/config.py` — `system_prompt`: agregar directives:
  Despues de "If a tool call fails, report the error honestly":
  ```python
  "When asked about current events, recent software versions, or news, "
  "ALWAYS use search or fetch tools. Never answer from memory for time-sensitive topics. "
  "Always provide a natural language response after using tools — never return an empty message."
  ```

- [x] **6.2** En `app/agent/loop.py` — `_run_agent_body()`: guardar synthesis en historial.
  Despues del `wa_client.send_message` de completion (line ~866), agregar:
  ```python
  # Bridge agent result into conversation history so the user can reference it
  try:
      from app.database.repository import Repository
      # Get repository from app state if available
      _repo = getattr(wa_client, '_repository', None)
      if _repo is None:
          # Try to get from the session registry's context
          pass
      # Save as assistant message in conversation
      # This allows the normal chat flow to see the agent's output
  except Exception:
      logger.debug("Could not bridge agent result to conversation history")
  ```

  Enfoque mas simple: guardar el reply como nota automatica con tag:
  ```python
  try:
      if hasattr(session, '_repository') and session._repository:
          await session._repository.add_note(
              f"[Agent Result] {session.objective[:100]}\n\n{reply[:2000]}",
              phone_number=session.phone_number,
          )
  except Exception:
      logger.debug("Could not save agent result as note")
  ```

  **Enfoque pragmatico**: Pasar `repository` a `_run_agent_body` y guardar como mensaje
  en la conversacion. Esto requiere threading `repository` desde `run_agent_session`.
  Para esta fase, usar el approach mas simple: inyectar reply en mensajes de conversacion
  via `repository.save_message()`.

- [x] **6.3** Implementar la solucion elegida en 6.2:
  - En `app/agent/loop.py`: agregar `repository` param a `_run_agent_body` y `run_agent_session`
  - En el call site (webhook/router.py donde se lanza el agent): pasar `repository`
  - Al final de `_run_agent_body`, llamar `repository.save_message(conv_id, "assistant", reply)`
  - Esto asegura que "Quiero ver el resultado" encuentre el reply en el historial

- [x] **6.4** Correr `make check` (710 tests pass)

### Phase 7: Documentacion y Cleanup

- [x] **7.1** Correr `make check` final (lint pass, 710 tests pass, mypy pre-existing errors only)
- [x] **7.2** Actualizar `docs/testing/40-testing_session_2026_03_09.md` — marcar bugs como resueltos
- [x] **7.3** Actualizar `CLAUDE.md` si se agregaron patrones nuevos:
  - `ddgs` en vez de `duckduckgo_search`
  - `_TZ_ALIASES` en datetime_tools
  - `WORKER_TOOL_SETS` ahora incluye search/fetch
  - Synthesizer honesty clause
  - Agent result bridge
- [x] **7.4** Actualizar `AGENTS.md` si hay cambios estructurales
- [x] **7.5** Actualizar `docs/exec-plans/README.md` con este plan

---

## Riesgos y Mitigaciones

| Riesgo | Mitigacion |
|--------|-----------|
| `ddgs` package API diferente | Verificado: misma API (`DDGS().text()`, `DDGS().news()`), solo cambio de nombre |
| Workers con search/fetch sobrecargan tool budget | `WORKER_TOOL_SETS` solo agrega categorias; `select_tools` sigue capped a `max_tools=8` |
| Dependency injection en workers rompe firma | Param `plan` es opcional con default `None`, backward-compatible |
| npm global install en Dockerfile falla por permisos | Mover `RUN npm install -g` ANTES de `USER appuser` |
| Chromium sandbox falla como appuser en Docker | `--no-sandbox` es seguro dentro de container (container = sandbox) |
| `@modelcontextprotocol/server-puppeteer` archived | Funcional en la version actual; si deja de funcionar, fork `@hisma/server-puppeteer` como alternativa |
| Agent result bridge duplica mensajes | Solo guardar si session completada exitosamente, no en cancel/fail |

## Metricas de Exito

Post-deploy, verificar con sesion de testing:
- [ ] `web_search` retorna resultados para queries normales (no "No results found")
- [ ] `/agent busca X en internet` — workers usan search sin necesitar request_more_tools
- [ ] Synthesizer reporta honestamente si no hay datos
- [ ] Logs muestran `fetch_mode=puppeteer` al boot (no `unavailable`)
- [ ] URL en mensaje → Puppeteer navega y renderiza, con fallback a mcp-fetch si falla
- [ ] "recuerdame en 5 minutos" → scheduler tool ejecutado
- [ ] "que hora es en Misiones" → timezone resuelto correctamente
- [ ] "convierte 11:55 de Argentina a Japon" → fecha de hoy, no 1900
- [ ] Resultado del agente visible en chat posterior
