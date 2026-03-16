# LocalForge - Plan de Producto
## Asistente Personal vía WhatsApp con LLM Local (Ollama)

---

## 1. Visión del Producto

Un asistente personal inteligente al que le hablás por WhatsApp y te responde usando un LLM corriendo en tu máquina via Ollama. Conversaciones contextuales, memoria persistente, y cero costo de operación.

**Principios:**
- **Privacidad**: el LLM y toda la lógica corren local. Los mensajes pasan por Meta (inevitable con WhatsApp) pero el procesamiento es 100% tuyo
- **Costo cero**: WhatsApp Cloud API gratis para mensajes de servicio + Ollama local + ngrok free
- **Simplicidad**: se levanta con `docker compose up`, no requiere infra cloud
- **Conversacional**: mantiene contexto y memoria entre sesiones
- **Extensible**: sistema de skills/plugins inspirado en [OpenClaw](https://openclaw.ai), donde agregar capacidades nuevas es escribir un archivo markdown

**Inspiración — OpenClaw:**
LocalForge toma varios patrones de diseño de OpenClaw, el asistente personal open-source que usa markdown como capa de configuración y memoria. En particular:
- **Archivos markdown como fuente de verdad** para memorias y configuración de skills
- **Skills como carpetas con SKILL.md** que definen capacidades del agente
- **Memoria en dos capas**: hechos curados (MEMORY.md) + notas diarias (logs)
- **Carga progresiva**: solo se lee el detalle de un skill cuando el agente lo necesita

La diferencia clave: OpenClaw es un framework multi-canal multi-agente. LocalForge es un asistente personal single-user, single-channel (WhatsApp), optimizado para simplicidad.

---

## 2. Arquitectura

```
┌──────────────┐    internet     ┌─────────────────┐
│  Tu celular  │◄───────────────►│  Meta / WhatsApp │
│  (WhatsApp)  │                 │  Cloud API       │
└──────────────┘                 └────────┬─────────┘
                                          │ webhook HTTPS
                                          ▼
                                 ┌─────────────────┐
                                 │  ngrok tunnel    │
                                 │  (free tier)     │
                                 └────────┬─────────┘
                                          │ localhost:8000
                                          ▼
                                 ┌─── Docker Compose ────────────────────┐
                                 │                                      │
                                 │  ┌─────────────────────────┐        │
                                 │  │  LocalForge Server (Python)   │        │
                                 │  │                         │        │
                                 │  │  FastAPI                │        │
                                 │  │  ├─ Webhook receiver    │        │
                                 │  │  ├─ Command router      │        │
                                 │  │  ├─ Skill engine        │        │
                                 │  │  ├─ Conversation mgr    │        │
                                 │  │  ├─ Memory manager      │        │
                                 │  │  └─ WhatsApp sender     │        │
                                 │  └───┬─────────┬───────────┘        │
                                 │      │         │                    │
                                 │ ┌────┴───┐ ┌───┴──────┐            │
                                 │ │ Ollama │ │  SQLite  │            │
                                 │ │ (LLM)  │ │ (volume) │            │
                                 │ └────────┘ └──────────┘            │
                                 └──────────────────────────────────────┘
```

### Flujo de un mensaje

1. Escribís un mensaje en WhatsApp
2. Meta lo envía como webhook POST a tu dominio ngrok
3. ngrok lo tuneliza a tu `localhost:8000`
4. FastAPI recibe el webhook, valida la firma, extrae el mensaje
5. Si es un `/comando` → se ejecuta directamente sin pasar por el LLM
6. Si es texto normal:
   - Se guarda en SQLite
   - Se cargan memorias activas + resumen previo + historial reciente
   - Se arma el contexto (system prompt + memorias + summary + historial)
   - Se envía a Ollama API local
   - La respuesta se guarda y se envía por WhatsApp
   - Si el historial supera el threshold, se lanza un resumen en background

---

## 3. Stack Tecnológico

| Componente | Tecnología | Por qué |
|---|---|---|
| Servidor | **Python 3.11+ / FastAPI** | Async, rápido, ideal para webhooks |
| WhatsApp | **WhatsApp Cloud API** (oficial) | Gratis para servicio, sin riesgo de ban |
| Túnel | **ngrok** (free tier) | Dominio estático gratis, sin timeout, 1GB/mes sobra |
| LLM | **Ollama** | Local, gratuito, múltiples modelos, tool calling |
| Base de datos | **SQLite** (WAL mode) + **aiosqlite** | Sin servidor, async, suficiente para 1 usuario |
| Config | **archivo .env** + **markdown** | Simple, estándar, human-readable |
| Contenedores | **Docker + Docker Compose** | Setup reproducible, un solo comando para levantar todo |

### Por qué NO Baileys
- Riesgo creciente de ban permanente en 2026
- Viola ToS de WhatsApp
- Meta está detectando activamente clientes no oficiales
- Para un asistente personal de largo plazo, la estabilidad es clave

### Por qué WhatsApp Cloud API + ngrok
- **Oficial**: cero riesgo de ban
- **Gratis**: mensajes de servicio (respuesta dentro de 24h) no tienen costo
- **ngrok free**: dominio estático, sin timeout, 20k requests/mes
- **Trade-off aceptable**: el túnel pasa por ngrok pero el procesamiento es local

---

## 4. Funcionalidades por Fase

### Fase 1: MVP - Chat funcional ✅
> Poder mandar un mensaje por WhatsApp y recibir respuesta del LLM local.

- Webhook receiver con validación de firma de Meta
- Envío de respuestas via Cloud API
- Conexión a Ollama API (`/api/chat`)
- System prompt configurable
- Historial de conversación en memoria (últimos N mensajes)
- Whitelist de números (solo responde a tu número)
- Indicador de "visto" / acuse de recibo
- Health check endpoint
- Guía de setup completa (SETUP.md)
- **Dockerizado**: Dockerfile + docker-compose.yml (localforge + ollama + ngrok)
- Volume para modelos de Ollama (persistencia entre reinicios)
- Tests unitarios completos

### Fase 2: Persistencia y Memoria ✅
> El asistente recuerda conversaciones anteriores y datos que le pedís guardar.

- Persistencia en SQLite (WAL mode, aiosqlite)
- Cargar últimos N mensajes como contexto al recibir mensaje nuevo
- Resumen automático de conversaciones largas en background
- Sistema de memorias globales (SQLite source of truth + `data/MEMORY.md` mirror)
- Sistema de comandos extensible (registry pattern):
  - `/remember <dato>` — guardar información importante
  - `/forget <dato>` — olvidar un recuerdo guardado
  - `/memories` — listar recuerdos guardados
  - `/clear` — borrar historial de conversación
  - `/help` — mostrar comandos disponibles
- Memorias inyectadas automáticamente en el contexto del LLM
- Mensajes de error claros para token expirado, permisos faltantes, modelo no descargado
- Tests: 70+ tests cubriendo repository, commands, summarizer, memory mirror

### Fase 3: UX y Multimedia ✅
> El asistente se siente más natural y maneja más que texto.

- **Audio entrante**: descarga de WhatsApp → transcripción con faster-whisper local (async via run_in_executor)
- **Imágenes entrantes**: descarga → descripción con llava:7b → respuesta contextual con qwen3.5:9b
- **Formato WhatsApp**: conversión markdown→WhatsApp (*bold*, _italic_, ```code```, listas)
- **Mensajes largos**: split automático (>4096 chars) en múltiples mensajes
- **Indicador de typing**: emoji ⏳ como reacción durante procesamiento, removido en finally
- **Rate limiting**: in-memory, por número de teléfono
- **Logging estructurado**: JSON con python-json-logger

### Fase 4: Skills y Herramientas ✅
> El asistente puede hacer cosas, no solo conversar.

Inspirado en el [sistema de skills de OpenClaw](https://docs.openclaw.ai/tools/skills), donde cada skill es una carpeta con un archivo markdown que define qué puede hacer el agente.

#### Arquitectura de Skills

```
skills/
├── datetime/
│   └── SKILL.md
├── calculator/
│   └── SKILL.md
├── weather/
│   └── SKILL.md
└── notes/
    └── SKILL.md
```

Cada `SKILL.md` tiene frontmatter YAML (parseado con regex, sin PyYAML) + instrucciones en prosa:

```yaml
---
name: weather
description: Consultar el clima actual y pronóstico
version: 1
tools:
  - get_weather
---

Cuando el usuario pregunte por el clima, usá la tool `get_weather` con la ciudad.
Si no dice ciudad, preguntale cuál.
Respondé en el idioma del usuario.
```

#### Carga progresiva (patrón OpenClaw)

1. **Al iniciar**: se lee solo `name` y `description` de cada SKILL.md (~30 tokens por skill)
2. **Al usar una tool**: se carga el cuerpo del SKILL.md correspondiente (lazy, una vez por skill)
3. **Ejecución**: tool calling loop con max 5 iteraciones como safety cap

#### Tool calling via Ollama

`think: True` es incompatible con tools en qwen3. Cuando hay tools en el payload, se desactiva automáticamente. El flujo:

1. El mensaje del usuario llega con la lista de tools disponibles
2. Ollama responde con tool_calls en vez de texto
3. LocalForge ejecuta cada tool, appendea resultados como role="tool"
4. Se reenvía a Ollama → puede llamar más tools o responder con texto
5. Después de MAX_TOOL_ITERATIONS (5), se fuerza respuesta sin tools

#### Skills implementados

| Skill | Tools | Mecanismo |
|-------|-------|-----------|
| `datetime` | `get_current_datetime`, `convert_timezone` | zoneinfo stdlib |
| `calculator` | `calculate` | AST safe eval (whitelist estricta, NO eval()) |
| `weather` | `get_weather` | wttr.in API (gratis, sin API key) |
| `notes` | `save_note`, `list_notes`, `search_notes`, `delete_note` | SQLite |

#### Reliability (incluido en Fase 4)

- **Dedup atómico**: tabla `processed_messages` con INSERT OR IGNORE (sin race conditions entre webhooks concurrentes)
- **Reply context**: si el usuario responde a un mensaje específico, se inyecta `[Replying to: "..."]` en el prompt
- **Graceful shutdown**: tracking de background tasks, wait con timeout de 30s antes de cerrar DB/HTTP

#### Extensibilidad

- Agregar un skill = crear carpeta con SKILL.md + registrar handlers en Python
- El directorio de skills es configurable via `SKILLS_DIR` (default: `skills/`)
- Sin skills disponibles, el sistema se comporta exactamente como antes (backward compatible)

### Fase 5: Memoria Avanzada ✅
> Sistema de memoria en capas con triggers automáticos, inspirado en [OpenClaw](https://docs.openclaw.ai/concepts/memory) y Context Engineering (Google, Nov 2025).

#### Taxonomía de memoria (3 capas)

| Tipo | Qué guarda | Dónde | Ejemplo |
|------|-----------|-------|---------|
| **Semántica** | Hechos estables, preferencias | `data/MEMORY.md` + tabla `memories` | "El usuario prefiere respuestas en español" |
| **Episódica Reciente** | Contexto temporal (del día) | `data/memory/YYYY-MM-DD.md` | "Hoy discutimos migrar la DB a Postgres" |
| **Episódica Histórica** | Conversaciones pasadas (crudo) | `data/memory/snapshots/*.md` | Últimos 15 msgs antes de /clear |

#### 5A: Daily Logs + Bootstrap Loading

- **Daily logs**: archivos append-only `data/memory/YYYY-MM-DD.md` con entries timestamped
- **Bootstrap loading**: `ConversationManager.get_context()` inyecta daily logs (hoy + ayer) como system message
- Los daily logs se escriben durante el flush (5B) y snapshots (5C), no en cada mensaje
- Configurable: `memory_dir`, `daily_log_days` en Settings

#### 5B: Pre-Compaction Flush (Write-Ahead Log)

- Antes de que el summarizer borre mensajes, `flush_to_memory()` los analiza con el LLM
- Extrae **facts** → `repository.add_memory()` + sync MEMORY.md
- Extrae **events** → `daily_log.append()`
- **Dedup**: `difflib.SequenceMatcher(ratio > 0.8)` contra memorias existentes
- JSON output format, con fallback para code-fenced responses
- Configurable: `memory_flush_enabled` en Settings

#### 5C: Session Snapshots

- **Trigger**: comando `/clear`
- Antes de borrar, guarda los últimos 15 mensajes (user + assistant) como snapshot
- Slug descriptivo generado por LLM (`think=False`), fallback a timestamp
- Snapshot guardado en `data/memory/snapshots/YYYY-MM-DD-slug.md`
- Entry en daily log: "Session cleared: topic (N messages saved)"

#### 5D: Memory Consolidation

- Después del flush, si se agregaron memorias nuevas, el LLM revisa todas las memorias
- Identifica duplicados y contradicciones → soft-delete los redundantes
- Mínimo 8 memorias para activar consolidación
- Solo se ejecuta cuando el flush agrega ≥1 memoria nueva

### Fase 6: Búsqueda Semántica y RAG ✅
> Embeddings locales, búsqueda semántica y MEMORY.md bidireccional.

#### Búsqueda semántica

- **Embeddings locales** via Ollama (`nomic-embed-text`, 768 dims)
- **sqlite-vec** para KNN search nativo en SQLite (tablas virtuales `vec_memories`, `vec_notes`)
- Solo las memorias y notas **relevantes** al mensaje se inyectan en el contexto del LLM
- Query embedding se computa una sola vez y se reutiliza para memorias + notas
- Fallback graceful: si sqlite-vec no carga o embed() falla → comportamiento anterior (todas las memorias)
- Master switch: `SEMANTIC_SEARCH_ENABLED` (default `true`)

#### Auto-indexing

- `/remember` → embede la nueva memoria automáticamente
- `/forget` → borra el embedding
- Pre-compaction flush → embede cada fact auto-extraído
- Consolidador → borra embeddings de memorias removidas
- Startup → backfill de todas las memorias y notas sin embedding

#### MEMORY.md bidireccional

- Editar MEMORY.md a mano → watchdog (inotify en Linux) detecta el cambio → sync a SQLite
- Sync guard (`threading.Event`) previene loops de retroalimentación
- Maneja `on_created` además de `on_modified` para editores con atomic rename (vim, etc.)

#### Notas semánticas

- `search_notes` usa búsqueda semántica con fallback a LIKE
- `save_note` auto-embede; `delete_note` limpia el embedding
- Notas relevantes inyectadas como contexto del LLM (via `get_context(relevant_notes=...)`)

### Fase 7: Performance Optimization ✅
> Optimizaciones quirúrgicas para reducir latencia sin cambios de arquitectura.

#### Problema
El critical path para un mensaje de texto tomaba 5-12s con todas las operaciones secuenciales:
- DB queries, embed, file I/O y `classify_intent` (LLM, 1-3s) todos en serie
- File I/O en `daily_log` y `markdown.sync()` bloqueando el event loop
- `get_or_create_conversation()` llamado 3-5 veces por mensaje para el mismo número
- `_build_tools_map()` reconstruido en cada mensaje aunque nunca cambia en runtime
- Tool calls dentro de una iteración ejecutados secuencialmente

#### Optimizaciones implementadas

| Cambio | Archivo | Ganancia |
|--------|---------|---------|
| Critical path parallelizado en fases A/B/C | `webhook/router.py` | ~3-5s |
| `classify_intent` kickeado como task paralelo | `webhook/router.py` | solapado con I/O |
| WA calls iniciales en paralelo (mark_as_read + reaction) | `webhook/router.py` | ~200ms |
| `_build_context()` helper (sin DB calls) | `webhook/router.py` | código limpio |
| `pre_classified_categories` evita segundo classify | `skills/executor.py` | ~1-3s |
| Module-level cache de tools_map | `skills/executor.py` | ~5ms/msg |
| Tool calls paralelos por iteración | `skills/executor.py` | variable |
| Blocking I/O → `asyncio.to_thread()` | `daily_log.py`, `markdown.py` | evita stall |
| Cache de conv_id en `ConversationManager` | `conversation/manager.py` | ~4 DB hits/msg |
| `get_active_memories(limit=...)` | `database/repository.py` | fallback limitado |
| SQLite PRAGMA tuning (synchronous, cache, temp) | `database/db.py` | I/O más rápido |
| Model warmup en startup | `app/main.py` | cold-start eliminado |

#### Arquitectura del nuevo critical path
```
0. reply_context (solo si hay reply, sequential)
1. get_conversation_id (una sola DB hit, cacheable)
   + asyncio.create_task(classify_intent(...))  ← LLM call en background

Phase A (asyncio.gather):
   embed(user_text) ‖ save_message(conv_id) ‖ load_daily_logs()

Phase B (asyncio.gather):
   search_memories ‖ search_notes ‖ get_latest_summary ‖ get_recent_messages

Phase C: categories = await classify_task  ← ya casi listo

Phase D: _build_context() → chat_with_tools (LLM principal, ~3-8s)
```

### Fase 8: Evaluación y Mejora Continua ✅
> Arquitectura de evaluación estructurada para medir y mejorar sistemáticamente la calidad del asistente.

- **Guardrails** (`app/guardrails/`): Validación pre-entrega — checks determinísticos (idioma, longitud, PII, tool JSON expuesto) + LLM checks opcionales (coherencia de tool, alucinación). Fail-open. Remediation single-shot con prompt bilingüe.
- **Trazabilidad Estructurada** (`app/tracing/`): Spans jerárquicos de todo el pipeline — `TraceContext` via `ContextVar`, `TraceRecorder` best-effort con Langfuse + SQLite simultáneamente. Spans: `guardrails`, `guardrails:remediation`, `llm:iteration_N`, `tool:*`, `planner:*`.
- **Evaluación en 3 Capas**:
  - *Capa 1* (Implícita): Reacciones de WhatsApp (👍→1.0, 👎→0.0, etc.) → scores en `trace_scores`. Correcciones del usuario detectadas por alta confianza → `add_correction_pair()`.
  - *Capa 2* (Automática): LLM-as-judge (`run_quick_eval`, `scripts/run_eval.py`) — prompt binario yes/no, `think=False`.
  - *Capa 3* (Explícita): `/feedback <comentario>` (análisis de sentimiento → score) + `/rate <1-5>`.
- **Dataset vivo** (`app/eval/`): Curación 3-tier automática — failure (guardrail < 0.3 o señal negativa) > golden confirmado (sistema OK + señal positiva) > golden candidato. Tags `guardrail:{check_name}` para filtrar por causa.
- **Auto-evolución de prompts** (`app/eval/evolution.py` + `prompt_manager.py`): `propose_prompt_change()` genera versión modificada. `/approve-prompt` + `activate_with_eval()` (advisory LLM-as-judge sobre dataset) → activación humana explícita.
- **Prompt Registry & Versioning** (`app/eval/prompt_registry.py`): 9 prompts en catálogo centralizado, versionado en DB, cache en memoria, `/prompts` para inspeccionar.
- **Benchmark offline** (`scripts/run_eval.py`): CLI sin FastAPI — `init_db()` + `OllamaClient` directo. Exit 0/1/2 según accuracy vs threshold. Integrable en CI.

---

### Fase 9: Tool Router & Capacidades Dinámicas ✅
> Router de 2 etapas para escalar a 60+ tools sin degradar la fiabilidad del LLM.

#### Problema
qwen3.5:9b maneja de forma fiable ~5-6 tools en el payload de Ollama. Con 60+ tools el modelo ignora todas las tools.

#### Solución: Router de 2 etapas

1. **`classify_intent()`**: LLM call rápido (`think=False`, sin tools) — clasifica el mensaje en categorías (`["time", "math", "notes", "projects", ...]`). Se lanza como `asyncio.create_task` en paralelo con el I/O de la Fase A del critical path.
2. **`select_tools()`**: Mapea categorías → tool names, distribuye el budget proporcionalmente (`per_cat = max(2, max_tools // len(categories))`). Cap configurable: `max_tools_per_call=8`.

#### MCP Integration & Hot-Reload

- **`McpManager`** (`app/mcp/`): conecta a servidores MCP stdio/HTTP, timeout 30s. Stack por servidor (`_server_stacks`) → `hot_add_server()` / `hot_remove_server()` sin restart.
- **Smithery**: `GET https://registry.smithery.ai/servers?q=<query>` para descubrir servidores. HTTP transport via `streamable_http_client`.
- **`expand` skill**: instala servidores desde Smithery o URLs, llama `reset_tools_cache()` + `register_dynamic_category()` en runtime.
- **Fetch mode tracking**: Puppeteer primero, mcp-fetch como fallback, runtime fallback en executor si el tool falla.

#### Dynamic Tool Budget

- Meta-tool `request_more_tools`: siempre disponible, permite al LLM pedir tools de otra categoría mid-conversation sin llamar `classify_intent` nuevamente.

---

### Fase 10: Modo Agéntico ✅
> Loop autónomo Planner-Orchestrator para tareas complejas multi-step.

#### Arquitectura (3 fases)

```
UNDERSTAND → planner crea JSON plan (AgentPlan + TaskStep[])
EXECUTE    → workers ejecutan tareas con tool sets especializados
SYNTHESIZE → planner revisa, re-planea si necesario (max 3 re-plans)
```

- **Planner** (`app/agent/planner.py`): `think=False` para output JSON estructurado. Fallback a reactive loop si falla el parse.
- **Workers** (`app/agent/workers.py`): `WORKER_TOOL_SETS` mapea `worker_type` → lista de categorías. Cada worker usa `execute_tool_loop` con `pre_classified_categories`.
- **Reactive fallback** (`_run_reactive_session`): loop simple 15 rounds × 8 tools para tareas no estructuradas.
- **HitL** (`app/agent/hitl.py`): `request_user_approval()` envía mensaje WhatsApp y espera respuesta async antes de ejecutar tools sensibles.
- **Scratchpad**: `AgentSession.scratchpad` persiste entre rounds reactivos via tags `<scratchpad>...</scratchpad>`.
- **Persistencia** (`app/agent/persistence.py`): append-only JSONL en `data/agent_sessions/<phone>_<session_id>.jsonl`. Best-effort.
- **`/agent <objetivo>`**: inicia sesión en background. `/cancel` interrumpe. `/agent-resume` retoma desde disco.

#### Projects Skill

- 4 tablas SQLite: `projects`, `project_tasks`, `project_activity`, `project_notes` + `vec_project_notes`.
- Proyectos por `phone_number` (no FK a conversations) — sobreviven `/clear`.
- `UNIQUE(phone_number, name)` — identificados por nombre, COLLATE NOCASE.
- Semántica: `add_project_note` embede la nota; `search_project_notes` usa KNN con fallback a list all.
- Auto-suggest: si todas las tareas están done → sugiere `update_project_status`. Al completar/archivar → log final automático.

---

### Fase 11: Seguridad Agéntica ✅
> Defensa en profundidad para tool execution autónoma.

#### PolicyEngine (`app/security/policy_engine.py`)

- Evalúa argumentos de tools contra reglas YAML **antes** de ejecutar.
- Tres decisiones: `ALLOW`, `DENY`, `FLAG` (HitL).
- Regex deterministas — sin LLM, latencia negligible.

#### AuditTrail (`app/security/audit.py`)

- Log append-only con hash SHA-256 secuencial (cada registro incluye el hash del anterior).
- Garantiza integridad y no-repudio del historial de tool calls agénticos.

#### Dev Tools

| Tool | Descripción |
|------|-------------|
| `shell_tools` | Denylist (`rm`, `sudo`, etc.) + allowlist configurable. Shell operators → HitL. `asyncio.create_subprocess_exec` con `shell=False`. |
| `git_tools` | `git_commit`, `git_push`, `git_create_branch` local + PR via GitHub REST API v2022-11-28. |
| `workspace_tools` | `switch_workspace(name)` cambia `_PROJECT_ROOT` dinámicamente. `settings.projects_root` para multi-project. |
| `debug_tools` | 5 tools de introspección — `review_interactions`, `diagnose_trace`, `write_debug_report`, etc. |

#### Comandos

- `/dev-review [teléfono]`: lanza sesión Planner-Orchestrator con objetivo de análisis de interacciones.
- `/debug [on|off]`: toggle de modo debug con logs detallados de ejecución.

---

### Fase 12: Context Engineering v2 ✅
> Token budget tracking, ContextBuilder unificado, windowed history y prompt versioning.

#### Token Budget

- `token_estimator.py`: proxy chars/4, `log_context_budget()` — WARNING >80%, ERROR >100% de 32K.
- Budget guardado en `ctx.token_estimate`, visible en logs estructurados.

#### ContextBuilder

- `context_builder.py`: consolida N secciones en 1 system message con XML tags (`<user_memories>`, `<active_projects>`, `<relevant_notes>`, `<recent_activity>`, `<capabilities>`, `<conversation_summary>`).
- Secciones vacías omitidas. Reemplaza el system message plano anterior.

#### Windowed History

- `get_windowed_history(verbatim_count=8)` en `ConversationManager` → `(últimos_N, resumen_de_anteriores)`.
- Zero-latency adicional: usa el resumen existente en DB.
- Setting: `history_verbatim_count=8`.

#### Capabilities Filtering

- Capabilities se construyen **después** de `classify_intent` — skip si `["none"]`, filtradas por categoría activa.
- `_build_capabilities_for_categories()` separa skills/MCP por categorías.

#### Memory Similarity Threshold

- `search_similar_memories_with_distance()` → `(content, L2_distance)`. Filtrado por `memory_similarity_threshold=1.0`. Fallback a top-3.

#### Sticky Categories

- `conversation_state` tabla persiste categorías del turno anterior.
- Si `classify_intent` retorna `"none"` → se usan sticky categories como fallback.

#### Prompt Versioning

- `app/eval/prompt_registry.py`: 9 prompts en catálogo centralizado, fallback chain cache → DB → registry → default.
- `seed_default_prompts()` idempotente en startup. `/approve-prompt` activa versiones con eval advisory.

---

## 5. Modelo de Datos (SQLite)

### Actual (Fase 1-6)

```
conversations
├── id            INTEGER PRIMARY KEY
├── phone_number  TEXT UNIQUE
├── created_at    TEXT
└── updated_at    TEXT

messages
├── id              INTEGER PRIMARY KEY
├── conversation_id INTEGER FK
├── role            TEXT (user/assistant/system)
├── content         TEXT
├── wa_message_id   TEXT UNIQUE
└── created_at      TEXT

summaries
├── id              INTEGER PRIMARY KEY
├── conversation_id INTEGER FK
├── content         TEXT
├── message_count   INTEGER
└── created_at      TEXT

memories
├── id         INTEGER PRIMARY KEY
├── content    TEXT
├── category   TEXT (nullable)
├── active     INTEGER (soft delete)
└── created_at TEXT

notes
├── id         INTEGER PRIMARY KEY
├── title      TEXT
├── content    TEXT
└── created_at TEXT

processed_messages
└── wa_message_id  TEXT PRIMARY KEY  (dedup atómico)
└── processed_at   TEXT

vec_memories (sqlite-vec virtual table)
├── memory_id  INTEGER PRIMARY KEY
└── embedding  float[768]

vec_notes (sqlite-vec virtual table)
├── note_id    INTEGER PRIMARY KEY
└── embedding  float[768]
```

---

## 6. Modelos Recomendados (Ollama)

| Modelo | Params | RAM Mín. | Caso de uso | Español | Tools |
|---|---|---|---|---|---|
| `qwen3.5:9b` | 9B | 8GB | **Recomendado — chat + tools** | Excelente | Sí |
| `llava:7b` | 7B | 8GB | Multimodal — imágenes | Limitado | No |
| `nomic-embed-text` | 137M | 1GB | Embeddings (768 dims) | Sí | — |
| `llama3.2:8b` | 8B | 8GB | Alternativa chat | Bueno | Sí |
| `llama3.2:3b` | 3B | 4GB | Hardware limitado | Aceptable | Limitado |

**Recomendación**: usar `qwen3.5:9b` para chat + tool calling, `llava:7b` para visión, y `nomic-embed-text` para embeddings.

---

## 7. Configuración Necesaria (Meta Developer)

Para usar WhatsApp Cloud API necesitás:

1. **Cuenta de Meta Developer** (gratis) → developers.facebook.com
2. **Crear una App** tipo "Business"
3. **Agregar producto WhatsApp** a la app
4. **Número de prueba**: Meta te da un número de test gratuito
5. **Webhook URL**: tu dominio ngrok estático
6. **Verify token**: string secreto que elegís vos
7. **Access token**: lo genera la plataforma (permanente con System User)

Todo gratis. El número de test tiene limitación de 5 destinatarios en modo desarrollo, pero para uso personal (vos mismo) es suficiente.

---

## 8. Seguridad

- **Validación de firma**: verificar `X-Hub-Signature-256` en cada webhook (evita requests falsos)
- **Whitelist**: solo procesar mensajes de tu número
- **Variables de entorno**: tokens y secrets en `.env`, nunca hardcodeados
- **ngrok**: el túnel usa HTTPS automáticamente
- **SQLite**: opcionalmente cifrar con SQLCipher
- **Rate limiting**: máximo N mensajes por minuto (Fase 3)
- **No logging de tokens**: cuidado con los logs
- **Skills sandboxing**: los skills no pueden ejecutar código arbitrario, solo tools pre-definidas (Fase 4)

---

## 9. Requisitos del Sistema

- **Docker** y **Docker Compose**
- **ngrok** instalado (CLI) o como container
- **RAM**: 8GB mínimo (16GB recomendado)
- **GPU**: opcional pero 5-20x más rápido (pass-through con `nvidia-container-toolkit` si usás NVIDIA)
- **Disco**: ~5GB por modelo 7B + ~500MB imagen Docker + SQLite negligible
- **Internet**: necesaria (WhatsApp Cloud API + ngrok)
- **Cuenta Meta Developer** (gratis)

---

## 10. Métricas de Éxito

- Responde en <10s con GPU, <30s sin GPU
- No pierde mensajes (dedup por `wa_message_id`)
- Mantiene conversación coherente de 20+ mensajes
- Memorias persisten entre reinicios
- Se levanta con `docker compose up`
- Cero costos de operación mensuales
- Sin bans ni warnings de WhatsApp

---

## 11. Riesgos y Mitigaciones

| Riesgo | Prob. | Impacto | Mitigación |
|---|---|---|---|
| ngrok free tiene downtime | Baja | Medio | Los mensajes quedan en cola de Meta, llegan al reconectar |
| Calidad LLM local | Media | Medio | System prompt bien diseñado, probar varios modelos |
| Latencia sin GPU | Alta | Medio | Modelos más chicos (3B), o invertir en GPU |
| Context window overflow | Media | Bajo | Resumen automático, truncar historial (implementado Fase 2) |
| Meta cambia pricing de Cloud API | Baja | Bajo | Monitorear, los mensajes de servicio son gratis desde 2023 |
| ngrok cambia free tier | Baja | Medio | Alternativas: cloudflare tunnel, localhost.run |
| Tool calling unreliable en modelos chicos | Media | Medio | Fallback a regex parsing, limitar tools por modelo |

---

## 12. Fuera de Alcance (No-Goals)

- Grupos de WhatsApp (solo chat 1:1)
- Interfaz web de admin
- Multi-usuario (es un asistente personal single-user)
- Fine-tuning de modelos
- Deploy en cloud/VPS
- Mensajes proactivos (el bot no inicia conversación, solo responde)
- Multi-canal (solo WhatsApp — a diferencia de OpenClaw que soporta Telegram, Slack, etc.)

---

## 13. Estrategia Docker

### Servicios en `docker-compose.yml`

| Servicio | Imagen | Propósito |
|---|---|---|
| `localforge` | Build local (Dockerfile) | App FastAPI |
| `ollama` | `ollama/ollama` | LLM server |
| `ngrok` | `ngrok/ngrok` (opcional) | Túnel al webhook |

### Volumes persistentes
- `ollama_data` → modelos descargados (no re-descargar en cada restart)
- `./data` → SQLite DB + MEMORY.md + documentos

### GPU pass-through
- Para NVIDIA: `nvidia-container-toolkit` + `deploy.resources.reservations.devices` en compose
- Sin GPU: Ollama corre en CPU automáticamente, no requiere config extra

### Workflow
```bash
# Primer uso
cp .env.example .env        # Configurar tokens
docker compose up -d         # Levanta todo
docker compose exec ollama ollama pull qwen3.5:9b   # Descargar modelo

# Uso diario
docker compose up -d         # Listo

# Ver logs
docker compose logs -f localforge
```

---

## 14. Comparación con OpenClaw

LocalForge se inspira en varios patrones de OpenClaw pero con un enfoque más simple:

| Aspecto | OpenClaw | LocalForge |
|---------|----------|-------|
| **Canales** | 12+ (WhatsApp, Telegram, Slack, Discord...) | Solo WhatsApp |
| **Usuarios** | Multi-agente, multi-usuario | Single-user |
| **LLM** | Cloud (Claude, GPT) + local | Solo local (Ollama) |
| **Skills** | 3000+ en ClawHub, carga en 3 tiers | Directorio local, carga progresiva |
| **Memoria** | MEMORY.md + daily notes + semantic search | MEMORY.md mirror + SQLite + semantic search (Fase 5) |
| **Identidad** | SOUL.md + AGENTS.md + USER.md | System prompt en .env |
| **Hosting** | Gateway WS local | FastAPI + ngrok |
| **Costo** | API keys de LLM cloud | Cero (todo local) |
| **Setup** | CLI install + gateway | `docker compose up` |

**Lo que tomamos de OpenClaw:**
- SKILL.md como definición declarativa de capacidades
- MEMORY.md como fuente de verdad human-readable
- Carga progresiva de skills (solo metadata al inicio, detalle on-demand)
- Memoria en dos capas (curada + diaria)
- Búsqueda híbrida (vector + full-text)

**Lo que NO tomamos:**
- Gateway WebSocket (innecesario para single-channel)
- Multi-agente (somos single-user)
- Marketplace de skills (comunidad, ClawHub)
- Node system para dispositivos (cámara, pantalla, etc.)
