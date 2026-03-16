# LocalForge — Convenciones del Proyecto

> **Mapa del proyecto** → `AGENTS.md` (dónde está cada cosa, workflow, skills activos)
> Este archivo documenta **convenciones de código y patrones arquitectónicos**.

## Protocolo de Documentación (OBLIGATORIO al terminar una feature)

### Documentos a crear/actualizar
1. Crear `docs/features/<nombre>.md` (template: `docs/features/TEMPLATE.md`)
2. Crear `docs/testing/<nombre>_testing.md` (template: `docs/testing/TEMPLATE.md`)
3. Actualizar `docs/features/README.md` y `docs/testing/README.md` con la nueva entrada
4. Actualizar `docs/PATTERNS.md` con patrones específicos del subsistema afectado
5. Actualizar `AGENTS.md` si se agrega un skill, módulo o comando nuevo

### Exec Plans (para features complejas)
- La planeación se divide estrictamente en dos documentos **antes** de codear si se afectan ≥3 archivos:
  - **PRD** (`docs/exec-plans/<nombre>_prd.md`): El "Qué" y "Por qué" (alcance, excepciones, reglas).
  - **PRP** (`docs/exec-plans/<nombre>_prp.md`): El "Cómo" (archivos a modificar, esquema, fases).
- El PRP es `stateful`: **OBLIGATORIO** incluir checkboxes markdown `[ ]` y marcarlos `[x]` a medida que se avanza en las fases de desarrollo iterativo.
- Ver convenciones detalladas y templates en: [`docs/exec-plans/README.md`](docs/exec-plans/README.md)

## Stack
- **Framework**: FastAPI (async, lifespan pattern)
- **LLM**: Ollama con **qwen3.5:9b** (chat) y **llava:7b** (vision)
- **Audio**: faster-whisper (transcripcion local)
- **DB**: SQLite via aiosqlite + sqlite-vec (vector search)
- **Embeddings**: nomic-embed-text via Ollama (768 dims)
- **Python**: 3.11+

## Modelos de Ollama
- Chat principal: `qwen3.5:9b` — NO usar qwen2.5
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

> Detalles de performance por optimización → [`docs/PATTERNS.md` § Performance Details](docs/PATTERNS.md#performance-details)

## Patrones Universales

- Todo async, nunca bloquear el event loop (usar `run_in_executor` / `asyncio.to_thread()` para sync code)
- Background tasks via `BackgroundTasks` de FastAPI, trackeados con `_track_task()` para graceful shutdown
- Dependencies via `app.state.*` + funciones `get_*()` en `dependencies.py`
- Mensajes se formatean (markdown→whatsapp/HTML) y splitean antes de enviar
- Tool calling loop: LLM → tools → resultados → LLM → repite hasta texto o max 5 iteraciones
- Dedup atomico: `processed_messages` tabla con INSERT OR IGNORE (sin race conditions)
- Reply context: si el usuario responde a un mensaje, se inyecta el texto citado en el prompt
- `OllamaClient.chat()` acepta `think: bool | None = None` — usar `think=False` OBLIGATORIO en todos los prompts binarios/JSON (guardrails, summarizer, consolidator, compaction, evolution, eval judge)
- Calculator: AST safe eval con whitelist estricta, NO eval() directo
- Docker: container corre como `appuser` (UID=1000), no root
- Embeddings best-effort: errores logueados, nunca propagados — la app funciona sin embeddings
- `_run_normal_flow()` inner function en `router.py` — permite toggle de tracing sin duplicar lógica

> **Patrones detallados por feature y subsistema → [`docs/PATTERNS.md`](docs/PATTERNS.md)**
> Subsistemas documentados: Skills & Tool Calling, Agent & Planner, Security, Guardrails, Tracing, Eval & Prompt Engineering, Context Engineering, Memory, Projects, Multi-Platform, Metrics, Ontology, Provenance, Deployment, Automation.
