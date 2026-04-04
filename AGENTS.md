# LocalForge — AGENTS.md: Mapa del Proyecto

> **Este archivo es el índice de navegación del proyecto, para agentes y humanos.**
> No contiene implementación: contiene dónde encontrar cada cosa, cómo trabajar, y qué crear al terminar una feature.
> Para convenciones técnicas detalladas → `CLAUDE.md`

---

## 1. Mapa de Documentación

| Qué buscás | Dónde está |
|---|---|
| Convenciones de código y patrones universales | `CLAUDE.md` |
| Patrones detallados por feature/subsistema | `docs/PATTERNS.md` |
| Visión del producto, fases completadas, roadmap | `PRODUCT_PLAN.md` |
| Setup inicial y configuración del entorno | `SETUP.md` |
| Guía de testing manual completa | `docs/testing/manual_testing_guide.md` |
| Planes de implementación (futuros y en curso) | `docs/exec-plans/` |
| Walkthroughs de features implementadas | `docs/features/` |
| Definiciones de skills (capabilities del agente) | `skills/*/SKILL.md` |
| Tests automatizados | `tests/` |

---

## 2. Mapa de Código: Quién Posee Qué

Antes de modificar un módulo, identificar su dominio. Las reglas de un dominio están en `CLAUDE.md`.

| Dominio | Archivos clave | No tocar sin leer |
|---|---|---|
| Pipeline principal de mensajes | `app/webhook/router.py` | Phase A+B via `ConversationContext.build()`, `_build_context()` con ContextBuilder, `_run_normal_flow()` |
| Context building | `app/context/` | `token_estimator.py` (chars/4), `context_builder.py` (XML tags), `conversation_context.py` (parallel build) |
| Tool calling loop | `app/skills/executor.py` | Cache `_cached_tools_map`, `_run_tool_call()` |
| Skills registry | `app/skills/registry.py`, `skills/*/SKILL.md` | Frontmatter parsing con regex (sin PyYAML) |
| MCP servers | `app/mcp/` | `_server_stacks` (por servidor), hot-reload |
| Memoria semántica | `app/memory/`, `app/embeddings/indexer.py` | Sync guard, best-effort pattern |
| Auto-Dream (consolidación) | `app/memory/dream.py`, `app/memory/consolidation_lock.py` | Best-effort, file-based lock, single LLM call con `think=False` |
| MicroCompact (context opt) | `app/formatting/microcompact.py` | Determinístico, solo `COMPACTABLE_TOOLS`, no muta original |
| Meta tools (discover) | `app/skills/tools/meta_tools.py` | `discover_tools` + `registry.search_tools()` keyword match |
| Session Memory Extraction | `app/memory/session_extractor.py` | Background LLM extraction cada N msgs, `think=False`, best-effort |
| Workspace Engine | `app/workspace/engine.py`, `app/workspace/templates.py`, `app/workspace/delivery.py` | Project workspaces, templates, delivery pipeline |
| Conversación + summarizer | `app/conversation/` | Pre-compaction flush antes de borrar msgs |
| Comandos `/` | `app/commands/` | `CommandContext` (no pasar `repository` directo) |
| Base de datos | `app/database/` | sqlite-vec, serialización de vectores |
| WhatsApp API | `app/whatsapp/` | HMAC validation, rate limiter |
| LLM client | `app/llm/client.py` | `think: True` incompatible con tools en qwen3 |
| Guardrails | `app/guardrails/` | Fail-open, single-shot remediation, langdetect umbral 30 chars, `check_code_security` (regex, Plan 60) |
| Trazabilidad | `app/tracing/` | contextvars propagation, best-effort recorder |
| Modo agéntico | `app/agent/` | loop.py (planner-orchestrator), planner.py, workers.py, models.py, hitl.py, task_memory.py, persistence.py |
| Subagent Fork & Plan Mode | `app/agent/subagent.py`, `app/agent/planner.py` (replan), `app/agent/loop.py` (HITL review) | `should_use_subagent()` heuristic, `run_subagent()` mini-loop, `replan_with_feedback()`, plan approval via HITL |
| Code navigation (Plan 58) | `app/skills/tools/glob_tools.py`, `app/skills/tools/grep_tools.py` | `glob_files` (pathlib.rglob), `grep_code` (rg/grep fallback), workspace-aware via WorkspaceEngine |
| Debug tools | `app/skills/tools/debug_tools.py` | 5 tools: review_interactions, get_tool_output_full, get_interaction_context, write_debug_report, get_conversation_transcript |
| Seguridad agéntica | `app/security/` | policy_engine.py (YAML regex), audit.py (hash chain SHA-256) |
| Credential scrubbing (Plan 60) | `app/skills/tools/shell_tools.py` | `_scrubbed_env()` — subprocesos no heredan tokens/secrets |
| Eval judge (Plan 60) | `app/eval/judge.py` | Multi-criteria LLM-as-judge: correctness, completeness, conciseness, tool_usage |

---

## 3. Workflow de Desarrollo (Ciclo Humano → Agente)

### Roles

- **Humano (Arquitecto)**: define la intención, revisa el plan, aprueba el merge.
- **Agente (Ejecutor)**: implementa, testea, documenta.
- Si el agente falla → no se fuerza el código: se mejoran las restricciones o herramientas.

### Ciclo por Feature

```
1. PLAN        →  Crear docs/exec-plans/<feature>.md con el plan
2. IMPLEMENTAR →  Código en la rama correspondiente
3. TESTS       →  .venv/bin/python -m pytest tests/ -v (todos pasan)
4. DOCUMENTAR  →  [VER SECCIÓN 4 — OBLIGATORIO]
5. PR          →  Short-lived, auto-revisado, merge rápido
```

### Regla de Oro

> **Una feature no está terminada si no tiene documentación.**
> Si no está en el repositorio, no existe para el próximo agente.

---

## 4. Protocolo de Documentación (Obligatorio al Terminar)

Después de implementar cualquier feature o cambio arquitectónico significativo:

### 4.1 Siempre crear

| Artefacto | Dónde | Template |
|---|---|---|
| Walkthrough de la feature | `docs/features/<nombre>.md` | `docs/features/TEMPLATE.md` |
| Guía de testing manual | `docs/testing/<nombre>_testing.md` | `docs/testing/TEMPLATE.md` |

### 4.2 Siempre actualizar

| Documento | Cuándo |
|---|---|
| `PRODUCT_PLAN.md` | Al completar una Fase nueva |
| `CLAUDE.md` | Al establecer un patrón arquitectónico nuevo que debe preservarse |
| `AGENTS.md` (este archivo) | Al agregar un skill, comando, o módulo nuevo |
| `skills/<name>/SKILL.md` | Al cambiar las tools o instrucciones de un skill |

### 4.3 Exec Plans (antes de implementar)

Para features complejas (>3 archivos afectados), crear primero:

```
docs/exec-plans/<feature>.md
```

El exec plan contiene: objetivo, archivos a modificar, schema de datos si aplica, orden de implementación. Ver `docs/exec-plans/11-eval_implementation_plan.md` como ejemplo.

---

## 5. Sistema de Skills: Scope y Contención

Los skills son la **unidad de extensión del sistema**. Mantienen el scope de los agentes acotado: cada skill posee un dominio claro, el LLM sabe qué herramientas usar para qué problema.

### 5.1 Skills activos y sus dominios

| Skill | Dominio | Tools clave |
|---|---|---|
| `calculator` | Evaluaciones matemáticas (AST safe eval) | `calculate` |
| `datetime` | Fecha/hora y conversión de timezones | `get_current_datetime`, `convert_timezone` |
| `weather` | Clima actual y pronóstico (wttr.in) | `get_weather` |
| `notes` | CRUD de notas personales (SQLite) | `save_note`, `list_notes`, `search_notes`, `delete_note` |
| `projects` | Proyectos, tareas y seguimiento | `create_project`, `add_task`, `update_task`, ... |
| `search` | Búsqueda web (ddgs) + deep research (fetch+chunk+embed+rank) | `web_search`, `web_research` |
| `news` | Noticias con preferencias guardadas | `search_news`, `add_news_preference` |
| `scheduler` | Recordatorios y crons vía APScheduler | `schedule_task`, `list_schedules`, `create_cron`, `list_crons`, `delete_cron` |
| `selfcode` | Auto-inspección del sistema | `get_version_info`, `get_runtime_config`, `search_source_code`, `get_file_outline`, `read_lines`, `write_source_file`, `apply_patch`, `preview_patch` |
| `code` | Navegación y búsqueda de código (Plan 58) | `glob_files`, `grep_code` (+ selfcode, shell, git, workspace tools) |
| `expand` | Auto-expansión: MCP + skills dinámicos | `search_mcp_registry`, `install_mcp_server`, `install_skill_from_url` |
| `shell` | Ejecución de comandos (gated por `AGENT_WRITE_ENABLED`) | `run_command`, `manage_process` |
| `workspace` | Multi-project workspace navigation | `list_workspaces`, `switch_workspace`, `get_workspace_info` |
| `docs` | Documentación de features y arquitectura | `create_feature_docs`, `update_architecture_rules`, `update_agent_docs` |
| `eval` | Evaluación y mejora continua del sistema | `get_eval_summary`, `list_recent_failures`, `diagnose_trace`, ... |
| `conversation` | Acceso al historial reciente | `get_recent_messages` |

### 5.2 Cuándo crear un nuevo skill

Crear un skill cuando:
- La funcionalidad forma un dominio coherente (no una tool suelta)
- El LLM necesita instrucciones específicas para usarla bien
- La funcionalidad no encaja en ningún skill existente

No crear un skill cuando:
- Es una pequeña extensión de un skill existente → agregar tool al SKILL.md existente
- Es lógica interna que el usuario no interactúa directamente

### 5.3 Anatomía de un skill

```
skills/<name>/
  SKILL.md          ← Frontmatter YAML (name, description, version, tools) + instrucciones en prosa
app/skills/tools/
  <name>_tools.py   ← Handlers Python de las tools del skill
```

El `SKILL.md` define la "personalidad" del skill: cuándo usarlo, cómo responder, qué no hacer.

---

## 6. Estado Actual y Próximos Pasos

### Fases completadas (7/7)

| Fase | Descripción |
|---|---|
| 1 | MVP — Chat funcional via WhatsApp + Ollama |
| 2 | Persistencia y memoria (SQLite + MEMORY.md) |
| 3 | UX y multimedia (audio, imagen, formato) |
| 4 | Skills y herramientas (tool calling loop) |
| 5 | Memoria avanzada (daily logs, flush, snapshots, consolidation) |
| 6 | Búsqueda semántica y RAG (sqlite-vec, embeddings) |
| 7 | Performance optimization (parallelismo, caches) |

### Eval — Arquitectura de Evaluación y Mejora Continua

Plan completo: `docs/exec-plans/11-eval_implementation_plan.md`

| Iteración | Estado | Descripción |
|---|---|---|
| 0 | ✅ | `WhatsAppClient.send_message()` retorna `wa_message_id` |
| 1 | ✅ | Guardrails determinísticos + Trazabilidad estructurada (SQLite) |
| 2 | ✅ | Señales de usuario: reacciones WA, `/feedback`, `/rate`, detección de correcciones |
| 3 | ✅ | Dataset vivo: `eval_dataset` + curación automática 3-tier + exportador JSONL |
| 4 | ✅ | Eval skill (`skills/eval/`) + 9 tools + evaluadores offline con Ollama |
| 5 | ✅ | Auto-evolución: memorias de auto-corrección + prompt versioning + `/approve-prompt` |
| 6 | ✅ | LLM guardrails, span instrumentation de tools, cleanup job, dashboard queries |

Docs: `docs/features/12-eval_guardrails_tracing.md`, `docs/features/14-eval_user_signals.md`, `docs/features/13-eval_dataset.md`, `docs/features/16-eval_skill.md`, `docs/features/15-eval_auto_evolution.md`, `docs/features/17-eval_maduracion.md`, `docs/features/49-regression_eval_suite.md`

**Regression Eval Suite** (`scripts/`):
- `scripts/seed_eval_dataset.py` — Seed 82 golden test cases (`make eval-seed`)
- `scripts/run_eval.py` — 3 modos: `classify` (intent), `tools` (selection), `e2e` (LLM-as-judge)
- `make eval` → seed + classify + e2e (CI-compatible, exit codes 0/1/2)

---

## 7. Principios del Proyecto

1. **Local-first**: LLM corre local via Ollama, cero costo operativo
2. **Skills como unidad de extensión**: agregar capacidad = `SKILL.md` + handler Python
3. **Best-effort para I/O no crítico**: embeddings, trazas, daily logs — nunca bloquean el pipeline
4. **Paralelismo en el critical path**: fases A/B/C en `router.py` con `asyncio.gather`
5. **Documentación como parte de la implementación**: una feature sin docs no está terminada
6. **Scope acotado por skills**: el agente sabe qué puede hacer porque está declarado en `skills/*/SKILL.md`
7. **Restricciones mecánicas sobre voluntad**: los linters, tests y patrones de código previenen la entropía
