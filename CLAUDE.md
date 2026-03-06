# LocalForge — Convenciones del Proyecto

> **Mapa del proyecto** → `AGENTS.md` (dónde está cada cosa, workflow, skills activos)
> Este archivo documenta **convenciones de código y patrones arquitectónicos**.

## Protocolo de Documentación (OBLIGATORIO al terminar una feature)

### Documentos a crear/actualizar
1. Crear `docs/features/<nombre>.md` (template: `docs/features/TEMPLATE.md`)
2. Crear `docs/testing/<nombre>_testing.md` (template: `docs/testing/TEMPLATE.md`)
3. Actualizar `docs/features/README.md` y `docs/testing/README.md` con la nueva entrada
4. Actualizar `CLAUDE.md` con patrones que deben preservarse
5. Actualizar `AGENTS.md` si se agrega un skill, módulo o comando nuevo

### Exec Plans (para features complejas)
- La planeación se divide estrictamente en dos documentos **antes** de codear si se afectan ≥3 archivos:
  - **PRD** (`docs/exec-plans/<nombre>_prd.md`): El "Qué" y "Por qué" (alcance, excepciones, reglas).
  - **PRP** (`docs/exec-plans/<nombre>_prp.md`): El "Cómo" (archivos a modificar, esquema, fases).
- El PRP es `stateful`: **OBLIGATORIO** incluir checkboxes markdown `[ ]` y marcarlos `[x]` a medida que se avanza en las fases de desarrollo iterativo.
- Ver convenciones detalladas y templates en: [`docs/exec-plans/README.md`](docs/exec-plans/README.md)

## Stack
- **Framework**: FastAPI (async, lifespan pattern)
- **LLM**: Ollama con **qwen3:8b** (chat) y **llava:7b** (vision)
- **Audio**: faster-whisper (transcripcion local)
- **DB**: SQLite via aiosqlite + sqlite-vec (vector search)
- **Embeddings**: nomic-embed-text via Ollama (768 dims)
- **Python**: 3.11+

## Modelos de Ollama
- Chat principal: `qwen3:8b` — NO usar qwen2.5
- Vision: `llava:7b`
- Los defaults estan en `app/config.py`, overrideables via env vars
- `think: True` solo para qwen3 sin tools. Cuando hay tools en el payload, NO se usa `think`

## Estructura
```
app/
  main.py              # FastAPI app + lifespan + scheduler jobs
  guardrails/          # Validación pre-entrega (Eval Fase 1)
    models.py          # GuardrailResult, GuardrailReport
    checks.py          # check_not_empty, check_language_match, check_no_pii, etc.
    pipeline.py        # run_guardrails() — orquesta checks, fail-open
  tracing/             # Trazabilidad estructurada (Eval Fase 2)
    context.py         # TraceContext (async ctx mgr), SpanData, get_current_trace()
    recorder.py        # TraceRecorder — persistencia SQLite best-effort
  context/             # Context engineering (Fase 5)
    fact_extractor.py  # Extracción de user_facts con regex (sin LLM)
    conversation_context.py  # ConversationContext dataclass + build()
  config.py            # Settings (pydantic-settings, .env)
  models.py            # Pydantic models
  dependencies.py      # FastAPI dependency injection
  logging_config.py    # JSON structured logging
  embeddings/          # Embedding indexer
    indexer.py         # embed_memory, backfill_embeddings (best-effort)
  llm/client.py        # OllamaClient (chat + tool calling + embeddings)
  whatsapp/client.py   # WhatsApp Cloud API client
  webhook/router.py    # Webhook endpoints + _handle_message + graceful shutdown
  webhook/parser.py    # Extrae mensajes del payload (text, audio, image, reply context)
  webhook/security.py  # HMAC signature validation
  webhook/rate_limiter.py
  audio/transcriber.py # faster-whisper wrapper
  formatting/
    markdown_to_wa.py  # Markdown → WhatsApp
    splitter.py        # Split mensajes largos
    compaction.py      # JSON-aware compaction (3 niveles: JSON → LLM → truncate)
  skills/              # Sistema de skills y tool calling
    models.py          # ToolDefinition, ToolCall, ToolResult, SkillMetadata
    loader.py          # Parser de SKILL.md (frontmatter con regex, sin PyYAML)
    registry.py        # SkillRegistry — registro, schemas Ollama, ejecución
    executor.py        # Tool calling loop + _clear_old_tool_results
    router.py          # classify_intent, select_tools, TOOL_CATEGORIES
    tools/             # Handlers de tools builtin
      datetime_tools.py
      calculator_tools.py
      weather_tools.py
      notes_tools.py
      selfcode_tools.py
      expand_tools.py
      project_tools.py
  agent/               # Modo agéntico
    loop.py            # Outer agent loop (rounds × tool calls), task plan injection
    models.py          # AgentSession, AgentStatus
    hitl.py            # Human-in-the-loop (request_user_approval)
    task_memory.py     # create_task_plan, update_task_status, get_task_plan
    persistence.py     # Append-only JSONL: data/agent_sessions/<phone>_<session_id>.jsonl
  security/            # Defensa en profundidad para tool execution agéntica
    policy_engine.py   # PolicyEngine — evalúa regex YAML antes de ejecutar tools
    audit.py           # AuditTrail — log append-only con hash SHA-256 secuencial
    exceptions.py      # Excepciones de seguridad
    models.py          # PolicyDecision, AuditRecord
  commands/            # Sistema de comandos (/remember, /forget, etc)
  conversation/        # ConversationManager + Summarizer
  database/            # SQLite init + sqlite-vec + Repository
  memory/              # Sistema de memoria
    markdown.py        # Sync bidireccional SQLite ↔ MEMORY.md
    watcher.py         # File watcher (watchdog) para edición manual de MEMORY.md
    daily_log.py       # Daily logs append-only + session snapshots
    consolidator.py    # Dedup/merge de memorias via LLM
  eval/                # Dataset vivo + curación automática
    dataset.py         # maybe_curate_to_dataset() (3-tier), add_correction_pair()
    exporter.py        # export_to_jsonl() para tests offline
  mcp/                 # MCP server integration
skills/                # SKILL.md definitions (configurable via skills_dir)
tests/
```

## Tests
- Correr: `make test` o `.venv/bin/python -m pytest tests/ -v`
- `asyncio_mode = "auto"` — no hace falta `@pytest.mark.asyncio`
- `TestClient` (sync) para integration tests del webhook
- Async fixtures para unit tests
- Mockear siempre Ollama y WhatsApp API en tests

## Calidad de código
- **Linter**: `ruff` — `make lint` / `make format`
- **Type checking**: `mypy app/` — `make typecheck` (solo `app/`, no `tests/`)
- **Pre-commit hooks**: ruff → mypy → pytest — instalar con `make dev`
- **CI**: GitHub Actions en `.github/workflows/ci.yml` — 3 jobs: lint → typecheck → test
- **mypy lenient**: `ignore_missing_imports = true` porque faster-whisper, sqlite-vec, mcp, watchdog no tienen stubs
- **ruff ignores**: `E501` (lineas largas), `B008` (FastAPI usa `Depends(...)` como default)
- Antes de pushear: `make check` (lint + typecheck + tests)

## Performance — Critical Path en `_handle_message`

El procesamiento de cada mensaje está paralelizado en fases:

| Fase | Qué corre en paralelo (asyncio.gather) | Bloqueante |
|------|----------------------------------------|-----------|
| **Phase A** | embed(query) ‖ save_message \| load_daily_logs | Sí |
| **Phase B** | search_memories ‖ search_notes ‖ get_summary ‖ get_recent_messages ‖ get_projects_summary | Sí |
| **Phase C** | await classify_task + load sticky_categories + extract user_facts | Sí |
| **Phase D** | `_build_context()` (sync) → LLM principal | Sí |

- `classify_intent` se lanza como `asyncio.create_task` antes de Phase A — corre en paralelo con las fases I/O-bound. Si retorna `"none"`, se re-clasifica con contexto (historial + sticky categories).
- `_build_context()` en router.py — construye el contexto LLM a partir de datos pre-fetched, sin DB calls.
- `pre_classified_categories` en `execute_tool_loop` — evita segunda llamada a `classify_intent`.
- Cache module-level `_cached_tools_map` en executor.py — construye el map de tools una vez.
- Tool calls en paralelo dentro de una iteración: `asyncio.gather(*[_run_tool_call(...)])` .
- WA calls iniciales (mark_as_read + send_reaction) paralelizadas con `asyncio.gather`.
- Blocking I/O en `daily_log.py` y `markdown.py` → `asyncio.to_thread()` (stdlib Python 3.9+).
- Cache de conv_id en `ConversationManager._conv_id_cache` (dict phone→id, permanente durante runtime).
- `get_active_memories(limit=...)` — fallback con límite (`settings.semantic_search_top_k`).
- SQLite PRAGMA tuning en `db.py`: `synchronous=NORMAL`, `cache_size=-32000` (32MB), `temp_store=MEMORY`.
- Model warmup en `main.py` startup: `embed(["warmup"]) ‖ chat_with_tools([...])` — non-critical, wrapped en try/except.


## Patrones
- Todo async, nunca bloquear el event loop (usar `run_in_executor` para sync code como Whisper)
- Background tasks via `BackgroundTasks` de FastAPI, trackeados con `_track_task()` para graceful shutdown
- Dependencies via `app.state.*` + funciones `get_*()` en `dependencies.py`
- Mensajes de WhatsApp se formatean (markdown->whatsapp) y splitean antes de enviar
- Tool calling loop: LLM llama tools → se ejecutan → resultados vuelven al LLM → repite hasta texto o max 5 iteraciones
- Dedup atomico: `processed_messages` tabla con INSERT OR IGNORE (sin race conditions)
- Reply context: si el usuario responde a un mensaje, se inyecta el texto citado en el prompt
- `_build_capabilities_section()` en router.py construye sección estructurada de capacidades (commands + skills + MCP) para el contexto LLM — reemplaza el summary plano anterior. Se auto-actualiza al agregar skills/commands/MCP servers
- SKILL.md: frontmatter parseado con regex (sin PyYAML), instrucciones se cargan lazy en primer uso
- `selfcode` skill: `register()` recibe `settings` (no `repository`). `_PROJECT_ROOT` resuelto una sola vez al importar. `_is_safe_path()` previene path traversal + bloquea archivos sensibles. `_SENSITIVE` hardcodeado oculta tokens de WhatsApp en `get_runtime_config`. `register_builtin_tools` acepta `settings=None` — selfcode solo se registra si `settings` no es None
- Hot-reload: `McpManager` usa `_server_stacks: dict[str, AsyncExitStack]` (uno por servidor) en lugar de un stack global — permite `hot_add_server()` / `hot_remove_server()` sin restart. `hot_add_server` persiste config + llama `reset_tools_cache()` + `register_dynamic_category()`. `SkillRegistry.reload()` re-escanea `skills/` y limpia `_loaded_instructions`. `reset_tools_cache()` en executor.py pone `_cached_tools_map = None`
- MCP HTTP transport: `McpManager._connect_server()` detecta `cfg["type"]` — `"http"` usa `streamable_http_client(url)` (3-tuple read/write/session_id), `"stdio"` usa `stdio_client` (2-tuple). Servidores Smithery son siempre tipo `"http"`
- `expand` skill: `register()` recibe `mcp_manager` (no `repository`). MCP manager se inicializa ANTES que skills en `main.py` para que `expand_tools` pueda referenciar el manager. Smithery API: `GET https://registry.smithery.ai/servers?q=<query>` — no requiere auth
- Guardrails LLM checks (Iteración 6): `check_tool_coherence` y `check_hallucination` en `checks.py` — async, prompt binario (yes/no), fail open. Integrados via `_run_async_check(timeout=settings.guardrails_llm_timeout)` en `pipeline.py`. Timeout configurable, default 3.0s (antes 0.5s — demasiado bajo para qwen3:8b). Gated por `guardrails_llm_checks=False` (opt-in). Call site en `router.py` pasa `ollama_client` a `run_guardrails`.
- **Guardrail remediation** (`_handle_guardrail_failure` en `router.py`): acepta `trace_ctx=None` — si se provee, crea span hijo `"guardrails:remediation"` (kind=generation) con `{check, lang_code}` → visible en Langfuse. Prompt bilingüe para `language_match`: target language first, English fallback (qwen3 entiende ambos). Span `"guardrails"` incluye `failed_checks: list[str]` en metadata.
- **`OllamaClient.chat()`** acepta `think: bool | None = None` — propaga a `chat_with_tools()`. Usar `think=False` para prompts binarios (ej. LLM-as-judge, clasificación rápida) donde no se quiere chain-of-thought.
- **`maybe_curate_to_dataset()`** (`app/eval/dataset.py`): acepta `failed_check_names: list[str] | None = None` — en el tier "failure" inserta tags `"guardrail:{check_name}"` en `eval_dataset_tags` → filtrable por causa. Call site en `router.py` propaga `failed_checks_for_curation`.
- **`run_quick_eval`** usa LLM-as-judge binario (yes/no, `think=False`) en lugar de word overlap. Prompt: `"Does the actual answer correctly answer the question? Reply ONLY 'yes' or 'no'."`. Output: `"Correct: X/Y (Z%)"` + ✅/❌ por entrada.
- **`scripts/run_eval.py`**: benchmark offline — `init_db()` + `OllamaClient` sin FastAPI. Args: `--db`, `--ollama`, `--model`, `--entry-type`, `--limit`, `--threshold`. Exit 0 si accuracy >= threshold, 1 si below, 2 si sin entradas evaluables.
- Span instrumentation de tools: `_run_tool_call` en `executor.py` llama `get_current_trace()` — si hay traza activa, wrappea ejecución en `trace.span(f"tool:{name}", kind="tool")` con input/output. Sin traza → ejecución directa sin overhead.
- **TraceRecorder singleton** (`app/tracing/recorder.py`): `TraceRecorder.create(repository)` classmethod inicializa Langfuse una sola vez (evita leak de background threads). Stored en `app.state.trace_recorder` durante lifespan. Shutdown hace `langfuse.flush()` para garantizar envío final. Inyectado via `get_trace_recorder()` en `dependencies.py`.
- **ChatResponse con métricas Ollama** (`app/llm/client.py`): `ChatResponse` tiene campos opcionales `input_tokens`, `output_tokens`, `model`, `total_duration_ms`. Extraídos de `prompt_eval_count`, `eval_count`, `total_duration` (ns→ms) en el JSON de respuesta de Ollama. Usados en spans de generación para OTel tags (`gen_ai.usage.input_tokens`, etc.).
- **Spans jerárquicos en executor** (`app/skills/executor.py`): `execute_tool_loop` y `_run_tool_call` aceptan `parent_span_id: str | None = None`. Por cada iteración se crea span `llm:iteration_N` (kind=generation) con `parent_id=parent_span_id`. Tools son hijos del span de iteración via `iteration_span_id`. Tool output capturado hasta 1000 chars (antes 200).
- **Agent loop tracing** (`app/agent/loop.py`): `run_agent_session` acepta `recorder: TraceRecorder | None = None`. Si se pasa recorder, crea `TraceContext(message_type="agent")` que envuelve `_run_agent_body` (inner function). contextvar propaga el trace a todas las sub-coroutines. Spans: `planner:create_plan`, `worker:task_N`, `planner:replan`, `planner:synthesize`, `reactive:round_N`. `execute_worker` propaga `parent_span_id` a `execute_tool_loop`. `CommandContext.trace_recorder` field conecta el recorder desde el webhook handler hasta `run_agent_session`.
- Trace cleanup job: APScheduler cron 03:00 UTC registrado en `main.py` gated por `tracing_enabled`. Closure sobre `repository` + `settings`. Llama `repository.cleanup_old_traces(days=settings.trace_retention_days)`.
- Dashboard queries eval: `repository.get_failure_trend(days)` (tendencia diaria) + `repository.get_score_distribution()` (stats por check). Tool `get_dashboard_stats` en eval skill.
- Calculator: AST safe eval con whitelist estricta, NO eval() directo
- Docker: container corre como `appuser` (UID=1000), no root
- Memoria en 3 capas: semántica (MEMORY.md), episódica reciente (daily logs), episódica histórica (snapshots)
- Pre-compaction flush: antes de borrar mensajes, el LLM extrae facts→memories + events→daily log
- Dedup de facts: `difflib.SequenceMatcher(ratio > 0.8)` contra memorias existentes
- Session snapshots: `/clear` guarda últimos 15 msgs con slug LLM-generated en `data/memory/snapshots/`
- Memory consolidation: LLM revisa memorias para duplicados/contradicciones después del flush
- `/review-skill` command: sin args lista skills + MCP servers; con nombre muestra detalle (tools, estado, instrucciones para skills; tipo, estado, tools para MCP)
- `CommandContext` tiene `ollama_client`, `daily_log` y `embed_model` para snapshot generation y auto-indexing
- Búsqueda semántica: `_get_query_embedding()` se computa una vez y se reutiliza para memorias + notas
- `init_db()` retorna `(conn, vec_available)` — fallback graceful si sqlite-vec no disponible
- sqlite-vec: `check_same_thread=False` + `conn._connection.enable_load_extension()` durante init
- Vectores serializados con `struct.pack(f"{len(v)}f", *v)` para blob storage
- Embeddings best-effort: errores logueados, nunca propagados — la app funciona sin embeddings
- MEMORY.md watcher: watchdog con sync guard (`threading.Event`) para prevenir loops
- `MemoryFile.set_watcher()` conecta el guard; `on_created` maneja editores con atomic rename
- `projects` skill: 4 tablas (`projects`, `project_tasks`, `project_activity`, `project_notes`) + `vec_project_notes` (sqlite-vec). `phone_number` en `projects` (no FK a `conversations`) — proyectos sobreviven `/clear`. `UNIQUE(phone_number, name)` — el LLM identifica proyectos por nombre. `project_tools.register()` recibe `repository`, `daily_log`, `ollama_client`, `embed_model`, `vec_available`. `set_current_user(phone)` module-level seteado per-request en `_handle_message`. `_resolve_project(name)` helper interno que busca COLLATE NOCASE. `update_task` a "done" loguea al `daily_log`. Si todas las tareas están done → sugiere `update_project_status`. `update_project_status` a archived/completed → registra resumen final automático. `_get_active_projects_summary()` en webhook/router.py — inyectada en Phase B (`asyncio.gather` con memorias/notas/summary/history). `register_builtin_tools` acepta `daily_log=None` — pasado desde `main.py`. Embeddings de project notes via `embed_project_note()` en indexer.py (best-effort). Búsqueda semántica en `search_project_notes` con fallback a list all.
- **Guardrails** (`app/guardrails/`): pipeline de validación pre-entrega. Checks determinísticos (sin LLM): `not_empty`, `language_match` (solo si ≥30 chars — `langdetect` falla en textos cortos), `no_pii`/`redact_pii` (regex), `excessive_length` (>8000 chars), `no_raw_tool_json`. Fail-open: errores en checks → pasan. Remediation single-shot en `_handle_guardrail_failure` (sin recursión). Scores de guardrails → `trace_scores` (value=1.0/0.0, source="system"). Integrado en `_run_normal_flow()` dentro de `_handle_message`.
- **Trazabilidad** (`app/tracing/`): `TraceContext` usa `contextvars.ContextVar` — sub-tasks de asyncio heredan el trace automáticamente sin cambiar firmas. `TraceRecorder` best-effort para Langfuse CLI SDK + SQLite simultáneamente (excepciones capturadas, nunca propagadas). Emite OTel GenAI tags (`gen_ai.usage.input_tokens`) directo a trace properties. Flujo normal refactorizado en `_run_normal_flow()` inner function en `router.py` — permite toggle de tracing sin duplicar lógica. `tracing_sample_rate` check con `random.random()`.
- **Dataset vivo** (`app/eval/`): `DATASET_SCHEMA` en `db.py` — tablas `eval_dataset` + `eval_dataset_tags` (tags como tabla join separada, no JSON array — permite índices eficientes). Curación 3-tier en `maybe_curate_to_dataset()`: failure (guardrail<0.3 o usuario negativo) > golden confirmado (sistema OK + usuario positivo) > golden candidato (sistema OK, sin señal de usuario, `metadata.confirmed=False`). Se llama como background task al final de `_run_normal_flow()` cuando `eval_auto_curate=True`. Correction pairs: `add_correction_pair()` guarda `entry_type="correction"` con `expected_output=correction_text` al detectar corrección high-confidence (score==0.0). FK de `eval_dataset.trace_id` → `traces(id)` enforced con `PRAGMA foreign_keys=ON`. `exporter.py`: `export_to_jsonl()` exporta a JSONL para tests offline.
- **Auto-evolución** (`app/eval/prompt_manager.py` + `app/eval/evolution.py`): `PROMPT_SCHEMA` en `db.py` — tabla `prompt_versions` (unique index por `prompt_name+version`, constraint `is_active` enforced a nivel app en `activate_prompt_version()` — SQLite no soporta partial unique index). Cache en memoria `_active_prompts` dict en `prompt_manager.py` — se invalida vía `invalidate_prompt_cache()`. `get_active_prompt()` lazy-loads desde DB, fallback al default de `config.py`. Integrado en `_run_normal_flow()` reemplazando `settings.system_prompt`. Memorias de auto-corrección: guardrail failure → `_save_self_correction_memory()` background task → `add_memory(category="self_correction")` + cooldown 2h por tipo de check + TTL 24h. `propose_prompt_change()` en `evolution.py`: LLM genera prompt modificado → `save_prompt_version(created_by="agent")`. Comando `/approve-prompt <nombre> <versión>` → `activate_prompt_version()` + `invalidate_prompt_cache()`.
- **Prompt Engineering & Versioning** (`app/eval/prompt_registry.py`, `app/eval/prompt_manager.py`): `PROMPT_DEFAULTS` catálogo centralizado con 9 prompts inline — evita circular imports. `get_active_prompt()` usa fallback chain: cache → DB → `prompt_registry.get_default()` → `default` param → `ValueError`. `seed_default_prompts()` idempotente en startup (gated por `prompt_versioning_enabled`). `activate_with_eval()`: LLM-as-judge binario (`think=False`) sobre `eval_dataset` — retorna `{"score", "passed", "details", "activated": False}` sin activar nunca; el humano decide vía `/approve-prompt`. `/approve-prompt` muestra ✅/⚠️ score (advisory) y activa igual. `run_quick_eval` acepta `prompt_name`/`prompt_version` opcionales — inyecta como system message en la inferencia. `SkillRegistry.get_tool(name)` método público para acceder a tools por nombre. `/prompts [nombre] [versión]` — sin args lista todos; con nombre muestra contenido activo (600 chars) + historial; con versión muestra esa versión específica (800 chars). Para mockear `activate_with_eval` en tests usar `patch("app.commands.builtins.activate_with_eval", ...)` — requiere import a nivel de módulo en `builtins.py`. `think=False` OBLIGATORIO en todos los prompts binarios/JSON (guardrails, summarizer, consolidator, compaction, evolution, eval judge). Ver: [`docs/features/32-prompt_engineering.md`](docs/features/32-prompt_engineering.md)
- **Eval skill** (`skills/eval/SKILL.md` + `app/skills/tools/eval_tools.py`): 8 tools — `get_eval_summary` (métricas agregadas de scores), `list_recent_failures` (trazas con score<0.5), `diagnose_trace` (deep-dive con spans y scores), `propose_correction` (correction pair vía LLM), `add_to_dataset` (curación manual), `get_dataset_stats` (composición del dataset), `run_quick_eval` (eval offline usando `ollama_client.chat()` directo — SIN tool loop para evitar recursión). Registrado en `register_builtin_tools()` con guard `settings.tracing_enabled`. Categoría `"evaluation"` agregada a `TOOL_CATEGORIES` en `app/skills/router.py` — el classifier dinámicamente incluye la categoría (usa `TOOL_CATEGORIES.keys()`). Patrón: closures sobre `repository` + `ollama_client` dentro de `register()`, igual que `selfcode_tools.py`.
- **Context Engineering** (`app/context/`, `app/formatting/compaction.py`, `app/webhook/router.py`, `app/agent/loop.py`): sticky categories persisten en tabla `conversation_state` — categorías del turno anterior se usan como fallback si `classify_intent` retorna `"none"`. `user_facts` (github_username, name, etc.) se extraen de memorias con regex en `fact_extractor.py` e inyectan como system message al tool loop. `_clear_old_tool_results(keep_last_n=2)` en executor.py resume tool results viejos. `compact_tool_output()` usa JSON-aware extraction antes del LLM — preserva nombres/IDs exactos. `self_correction` category solo en DB, excluida de MEMORY.md sync. Agente con loop externo en `agent/loop.py` (15 rounds × 8 tools), task plan re-inyectado entre rounds, completion via `[ ]` pendientes en task plan. Ver: [`docs/features/08-context_engineering.md`](docs/features/08-context_engineering.md)
- **Context Engineering v2** (`app/context/token_estimator.py`, `app/context/context_builder.py`, `app/context/conversation_context.py`): Token budget tracking con `log_context_budget()` (chars/4 proxy, WARNING >80%, ERROR >100% de 32K). `ContextBuilder` consolida N secciones en 1 system message con XML tags (`<user_memories>`, `<active_projects>`, `<relevant_notes>`, `<recent_activity>`, `<capabilities>`, `<conversation_summary>`) — secciones vacías omitidas. History windowing: `get_windowed_history(verbatim_count=8)` en `ConversationManager` retorna `(last_N, summary_of_older)` sin latencia adicional (usa summary existente de DB). Setting `history_verbatim_count=8` en config. Capabilities filtering: capabilities se construyen DESPUÉS de `classify_intent` — skip si `["none"]`, filtradas por categoría si hay tools. `_build_capabilities_for_categories()` separa skills/MCP por categorías activas. Memory threshold: `search_similar_memories_with_distance()` en Repository retorna `(content, L2_distance)` — filtrado por `memory_similarity_threshold` (default 1.0), fallback a top-3. `ConversationContext.build()` extendido con `ollama_client`, `settings`, `daily_log`, `vec_available` — centraliza fases A+B de `_run_normal_flow()`. Agent scratchpad: `AgentSession.scratchpad` persiste entre rounds reactivos — `_inject_scratchpad()` / `_extract_scratchpad()` en `loop.py` usando `<scratchpad>...</scratchpad>` tags. Ver: [`docs/features/31-context_engineering_v2.md`](docs/features/31-context_engineering_v2.md)
- **Agentic Security** (`app/security/`): Capa de Defensa en Profundidad con `PolicyEngine` (evalúa regex YAML deterministas sobre argumentos de tools antes de ejecutar) y `AuditTrail` (registro append-only con hash SHA-256 secuencial). Si un tool es evaluado como FLAG (HitL), el `executor` pausa asíncronamente invocando `request_user_approval` vía un `hitl_callback` inyectado desde el loop principal para recabar confirmación humana por WhatsApp.
- **shell_tools** (`app/skills/tools/shell_tools.py`): `CommandDecision` (ALLOW/DENY/ASK). Denylist hardcodeado (`rm`, `sudo`, `chmod`, etc.) + allowlist configurable vía `settings.agent_shell_allowlist`. Shell operators (`|`, `&&`, `$(`, etc.) → ASK (HITL). Allowlist match → ALLOW directo. Comando desconocido → ASK. Gated por `settings.agent_write_enabled`. Ejecución: `asyncio.create_subprocess_exec(*tokens)` con `shell=False`, `stdin=DEVNULL`, `cwd=_PROJECT_ROOT`. Background: `process_id` via `manage_process`. GC automático de procesos zombie (>30min). `_validate_command()` es una función pura (no async) — testeable sin mocks.
- **workspace_tools** (`app/skills/tools/workspace_tools.py`): `_PROJECT_ROOT` mutable compartido con `selfcode_tools` y `shell_tools` via `set_project_root()`. `switch_workspace(name)` cambia el root dinámicamente. `settings.projects_root` define el directorio base para multi-project.
- **git_tools** (`app/skills/tools/git_tools.py`): Requiere `settings.github_token` + `settings.github_repo` para crear PRs. PR creation usa GitHub REST API v2022-11-28 (`POST /repos/{owner}/{repo}/pulls`). Operaciones locales (`git_commit`, `git_push`, `git_create_branch`) usan `asyncio.create_subprocess_exec` con `shell=False`.
- **persistence.py** (`app/agent/persistence.py`): Append-only JSONL en `data/agent_sessions/<phone>_<session_id>.jsonl`. Best-effort: errores de I/O logueados, nunca propagados. Cada línea: `{"round": N, "tool_calls": [...], "reply": "...", "task_plan": "..."}`.
- **Dynamic Tool Budget** (`app/skills/router.py` + `app/skills/executor.py`): `select_tools()` distribuye el budget proporcionalmente entre categorías (`per_cat = max(2, max_tools // len(categories))`) para evitar que la primera categoría consuma todas las slots. Meta-tool `request_more_tools` siempre prepended en `execute_tool_loop()` — manejado inline (NO pasa por `PolicyEngine` ni `AuditTrail`). Handler separa meta-calls de regular-calls usando índices, ejecuta regulares en `asyncio.gather`, y appende resultados en orden original. Constante `REQUEST_MORE_TOOLS_NAME` + `build_request_more_tools_schema()` en `router.py`.
- **Planner-Orchestrator** (`app/agent/planner.py` + `app/agent/workers.py` + `app/agent/loop.py`): 3-phase agent loop — UNDERSTAND (planner creates JSON plan) → EXECUTE (workers run tasks) → SYNTHESIZE (planner reviews, replans if needed). `AgentPlan` and `TaskStep` in `models.py`. `WORKER_TOOL_SETS` in `router.py` maps `worker_type` → category list. Workers use `execute_tool_loop` with `pre_classified_categories`. Planner uses `think=False` for structured JSON output. Fallback to reactive loop (`_run_reactive_session`) if planner JSON parse fails. `max_replans=3` hard cap. `session.plan` (structured) coexists with `session.task_plan` (markdown). `/dev-review [phone]` command triggers planner session with debugging objective.
- **Debug tools** (`app/skills/tools/debug_tools.py`): 5 tools for interaction introspection — `review_interactions`, `get_tool_output_full`, `get_interaction_context`, `write_debug_report`, `get_conversation_transcript`. Gated by `tracing_enabled`. Repository methods: `get_traces_by_phone()`, `get_trace_tool_calls()`, `get_conversation_transcript()`. Reports saved to `data/debug_reports/`. Category `"debugging"` in `TOOL_CATEGORIES`.
- **Fetch mode tracking** (`app/mcp/manager.py`): `McpManager._fetch_mode` (`"puppeteer"` | `"mcp-fetch"` | `"unavailable"`). `_register_fetch_category()` se llama al final de `initialize()` y en `hot_add_server()` — registra la categoría `"fetch"` con tools del servidor disponible (Puppeteer primero, mcp-fetch como fallback). `get_fetch_mode()` expone el modo activo. Runtime fallback en `executor.py`: si tool puppeteer falla (`result.success=False`) → busca equivalente en `mcp::mcp-fetch` → re-ejecuta con prefijo `"[⚠️ Fallback a mcp-fetch...]"`. Notificación en `router.py`: si URL en mensaje y fetch_mode es `"mcp-fetch"` → inyecta nota de sistema para que el LLM informe al usuario.
- **Multi-platform (Exec Plan 35)** (`app/platforms/`, `app/telegram/`, `app/webhook/router.py`): `PlatformClient` Protocol (`runtime_checkable`) en `app/platforms/base.py` — interfaz común para WhatsApp y Telegram (7 métodos: `send_message`, `download_media`, `mark_as_read`, `send_typing_indicator`, `remove_typing_indicator`, `format_text`, `platform_name`). `IncomingMessage` Pydantic model + `Platform` StrEnum en `app/platforms/models.py`. `WhatsAppPlatformAdapter` adapta `WhatsAppClient` → `PlatformClient` (en `webhook/router.py`). `process_message_generic(msg: IncomingMessage, platform_client: PlatformClient, ...)` es la función compartida por ambas plataformas; `process_message(msg: WhatsAppMessage, ...)` es thin wrapper backward-compatible. `TelegramClient` en `app/telegram/client.py` implementa `PlatformClient` via httpx. Identificación de usuarios Telegram: `tg_<chat_id>` como `phone_number` — zero schema migration. `_send_reminder` en `scheduler_tools.py` enruta por prefijo `tg_`. `CommandContext.wa_client` field es `Any` — acepta cualquier `PlatformClient`. Agent loop usa `markdown_to_whatsapp()` directamente (OK para MVP — mensajes de estado sin formateo HTML en Telegram). Typing indicator en Telegram: `sendChatAction(typing)` auto-expira en ~5s, `remove_typing_indicator` es no-op. Ver: [`docs/features/35-telegram.md`](docs/features/35-telegram.md)
