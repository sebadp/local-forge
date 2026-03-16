# Feature: Langfuse v3 — Observabilidad y Tracing

> **Versión**: v3.0
> **Fecha de implementación**: 2026-03-14
> **Estado**: ✅ Implementada (instrumentación base) | 🚧 Oportunidades de profundización

---

## ¿Qué hace?

Langfuse v3 es la plataforma de observabilidad que registra cada interacción del bot: trazas completas (input→spans→output), scores de calidad, uso de tokens, latencias y datasets de evaluación. Permite visualizar, filtrar y analizar el comportamiento del sistema desde un dashboard web en `http://localhost:3000`.

---

## Arquitectura del Stack (docker-compose)

```
┌─────────────────────────────────────────────────────────┐
│                    LocalForge App                        │
│  (TraceRecorder → Langfuse Python SDK v3)               │
│         │                                                │
│         ▼ OTLP / HTTP                                    │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  ┌──────────────┐     ┌──────────────────┐              │
│  │ langfuse-web  │────▶│  langfuse-worker │              │
│  │ (UI + API)    │     │ (async processor)│              │
│  │  :3000        │     └────────┬─────────┘              │
│  └──────┬───────┘              │                         │
│         │                      │                         │
│    ┌────┴────┐   ┌─────────────┴──┐   ┌──────────┐     │
│    │Postgres │   │  ClickHouse    │   │  Redis   │     │
│    │ :5433   │   │  (analytics)   │   │  :6379   │     │
│    └─────────┘   └────────────────┘   └──────────┘     │
│                                                          │
│                  ┌──────────┐                            │
│                  │  MinIO   │                            │
│                  │ (S3 blobs)│                            │
│                  │  :9090   │                            │
│                  └──────────┘                            │
└─────────────────────────────────────────────────────────┘
```

---

## Servicios del Stack

### 1. `langfuse-web` — UI + API Server

**Imagen**: `langfuse/langfuse:3`
**Puerto**: `3000`

El servidor principal. Sirve:
- **Dashboard web** — trazas, spans, scores, sessions, datasets, prompts
- **API REST** — endpoints para ingestión de datos (traces, scores, datasets)
- **OTLP endpoint** — `/api/public/otel/v1/traces` — recibe spans del SDK Python via OpenTelemetry

**Variables de entorno clave**:
| Variable | Valor | Descripción |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres:postgres@langfuse-db:5432/postgres` | Conexión a PostgreSQL |
| `NEXTAUTH_SECRET` | `mysecret` | Secret para auth de la UI (cambiar en producción) |
| `ENCRYPTION_KEY` | 64 hex chars | Encriptación at-rest de API keys (cambiar en producción) |
| `CLICKHOUSE_URL` | `http://langfuse-clickhouse:8123` | ClickHouse HTTP interface |
| `CLICKHOUSE_MIGRATION_URL` | `clickhouse://langfuse-clickhouse:9000` | ClickHouse native protocol (migrations) |
| `LANGFUSE_S3_EVENT_UPLOAD_*` | MinIO config | Blob storage para eventos grandes |
| `REDIS_HOST` / `REDIS_AUTH` | `langfuse-redis` / `myredissecret` | Cola de procesamiento |
| `TELEMETRY_ENABLED` | `false` | Desactivar telemetría a Langfuse Cloud |

### 2. `langfuse-worker` — Async Trace Processor

**Imagen**: `langfuse/langfuse-worker:3`

Worker asíncrono que procesa trazas ingestadas por el web server. Comparte las mismas variables de entorno (`<<: *langfuse-env`). Se encarga de:
- Procesar batches de spans/generations desde Redis
- Escribir datos analíticos a ClickHouse
- Subir blobs grandes a MinIO
- Ejecutar evaluaciones programadas (LLM-as-Judge)

Sin este worker, las trazas se acumulan en Redis y nunca se persisten. **Es esencial en producción.**

### 3. `langfuse-db` — PostgreSQL 17

**Puerto**: `5433` (externo, mapeado desde 5432 interno)

Almacena metadata relacional:
- Proyectos, usuarios, API keys
- Configuración de prompts y versiones
- Definiciones de datasets y evaluaciones
- Configuración de dashboards y annotation queues

**No almacena traces/spans** — eso va a ClickHouse en v3.

### 4. `langfuse-clickhouse` — ClickHouse (Analytics Engine)

Almacena datos de alta cardinalidad optimizados para queries analíticas:
- **Traces y spans** — el grueso de la data de observabilidad
- **Scores** — guardrail results, LLM-as-judge, user signals
- **Token usage** — input/output por generation
- **Latencias** — p50/p95/p99 por span

ClickHouse es la razón principal por la que Langfuse v3 es significativamente más rápido que v2 para dashboards con millones de traces.

### 5. `langfuse-redis` — Redis 7

Cola de mensajes y cache:
- Buffer de ingestión entre web server y worker
- Cache de queries frecuentes del dashboard
- Configurado con `--maxmemory-policy noeviction` (nunca pierde datos de cola)

### 6. `langfuse-minio` — MinIO (S3-compatible Blob Store)

**Puerto**: `9090` (API), `9001` (Console)

Almacena:
- Eventos de trace que exceden el tamaño límite de ClickHouse
- Media uploads (screenshots, audio transcriptions si se envían)
- Exportaciones batch de datasets

El bucket `langfuse` se crea automáticamente al iniciar (`mkdir -p /data/langfuse`).

---

## Instrumentación Actual

### Qué se trackea hoy

| Concepto | Dónde se genera | Span/Score name |
|---|---|---|
| **Trace completo** | `TraceContext.__aenter__` → `recorder.start_trace()` | Root span `"interaction"` |
| **Fases de contexto** | `_run_normal_flow()` en `router.py` | `"phase_ab"`, `"phase_cd"` |
| **LLM principal** | `execute_tool_loop()` → cada iteración | `"llm:iteration_N"` (kind=generation) |
| **Tool calls** | `_run_tool_call()` en `executor.py` | `"tool:<name>"` (kind=tool) |
| **Classify intent** | `classify_intent()` en `router.py` | `"llm:classify_intent"` (kind=generation) |
| **Compaction** | `compact_tool_output()` en `compaction.py` | `"llm:compact_output"` (kind=generation) |
| **Guardrails** | `run_guardrails()` pipeline | Span `"guardrails"` + metadata `failed_checks` |
| **Guardrail remediation** | `_handle_guardrail_failure()` | `"guardrails:remediation"` (kind=generation) |
| **Planner** | `create_plan()` / `replan()` / `synthesize()` | `"planner:*"` spans |
| **Worker tasks** | `execute_worker()` | `"worker:task_N"` |
| **Agent sessions** | `run_agent_session()` | `TraceContext(message_type="agent")` |

### Scores que se emiten

| Score name | Rango | Source | Descripción |
|---|---|---|---|
| `guardrail_*` | 0.0 / 1.0 | system | Resultado por check (not_empty, language_match, no_pii, etc.) |
| `context_fill_rate` | 0.0–1.0 | system | tokens_usados / token_limit |
| `classify_upgrade` | 0.0 / 1.0 | system | 1.0 si classify base retornó "none" y se re-corrió |
| `hitl_escalation` | 0.0 / 1.0 | system | 1.0=approved, 0.0=rejected por HITL |
| `goal_completion` | 0.0 / 1.0 | system | LLM-as-judge al final de sesión agéntica |
| `planner_downgrade` | 1.0 | system | Se emite cuando planner falla y cae a reactive |

### Metadata OTel en generations

```python
# Extraído del JSON de Ollama y enviado como usage_details:
"gen_ai.usage.input_tokens"   → usage_details["input"]
"gen_ai.usage.output_tokens"  → usage_details["output"]
"gen_ai.request.model"        → model
```

### Sesiones y Tags

- `session_id = phone_number` — agrupa conversaciones por usuario
- `user_id = phone_number` — identifica al usuario
- `tags` — categorías de intent + platform tag (actualizados post-classify)
- `metadata.platform` — `"whatsapp"` o `"telegram"`

### Dataset sync

`sync_dataset_to_langfuse()` sincroniza entries `golden` y `correction` del eval dataset local a Langfuse Datasets (failures excluidos por ruidosos).

---

## Oportunidades de Instrumentación Avanzada

### 1. Prompt Management via Langfuse API

**Estado actual**: Los prompts se versionan en SQLite local (`prompt_versions` tabla). El flujo es: crear prompt → evaluar con `activate_with_eval()` → aprobar con `/approve-prompt`.

**Oportunidad**: Langfuse v3 tiene un sistema de Prompt Management nativo con:
- Versionado con diff visual entre versiones
- Variables de template (`{{variable}}`)
- Labels (`production`, `staging`, `latest`)
- Playground para probar prompts contra modelos
- API: `langfuse.get_prompt(name, version=None, label=None)`

**Cómo implementar**:
```python
# En prompt_manager.py, agregar fallback a Langfuse:
async def get_active_prompt(name: str, ...) -> str:
    # 1. Cache local (ya existe)
    # 2. DB local (ya existe)
    # 3. NEW: Langfuse prompt management
    if self.langfuse:
        try:
            prompt = self.langfuse.get_prompt(name, label="production")
            return prompt.compile()  # con variables
        except Exception:
            pass
    # 4. Registry default (ya existe)
```

**Beneficio**: Editar prompts desde el dashboard web sin tocar código ni reiniciar la app. Historial visual de cambios. Útil para iteración rápida del system prompt.

### 2. Evaluaciones LLM-as-Judge en Langfuse

**Estado actual**: `run_quick_eval` ejecuta evaluación local (binary yes/no). `activate_with_eval()` corre eval antes de aprobar un prompt.

**Oportunidad**: Langfuse v3 soporta evaluaciones server-side:
- **Evaluators** configurables (LLM-as-Judge, regex, Python scripts)
- **Ejecución automática** sobre nuevas traces
- **Comparación A/B** entre versiones de prompts
- **Score agregación** con dashboards nativos

**Cómo implementar**: Configurar evaluators desde la UI de Langfuse (`/evaluators`). Cada evaluator define: qué traces evaluar (filtro por tags), qué score producir, qué modelo usar.

Ejemplo de evaluator para detectar hallucinations:
```
Name: hallucination_check
Model: qwen3.5:9b (via Ollama endpoint)
Template: "Does the output contain information not grounded in the input? Reply 'yes' or 'no'."
Score: hallucination (0.0 = no hallucination, 1.0 = hallucination)
Apply to: traces with tag "tools"
```

**Beneficio**: Evaluaciones continuas sin código adicional. Descubrir degradaciones antes de que los usuarios las reporten.

### 3. Annotation Queues

**Estado actual**: No hay mecanismo para revisión humana sistemática de respuestas del bot.

**Oportunidad**: Langfuse v3 tiene Annotation Queues — colas de revisión donde humanos pueden:
- Calificar respuestas (thumbs up/down, escala 1-5, o etiquetas custom)
- Agregar comentarios
- Marcar para re-entrenamiento
- Asignar a revisores específicos

**Cómo implementar**: Crear una queue desde la UI de Langfuse filtrando por:
- Traces con `guardrail_*` scores bajos (candidatos a corrección)
- Traces con `goal_completion = 0.0` (sesiones agénticas fallidas)
- Traces de ciertos usuarios (beta testers)

**Beneficio**: Pipeline de feedback humano estructurado. Complementa las señales automáticas (guardrails, LLM-as-judge) con revisión manual.

### 4. Experiments & Datasets

**Estado actual**: `eval_dataset` local con 3 tiers (golden, correction, failure). `sync_dataset_to_langfuse()` empuja entries a Langfuse.

**Oportunidad**: Langfuse v3 Experiments permiten:
- Ejecutar un dataset completo contra una función/prompt
- Comparar resultados entre versiones
- Tracking automático de métricas por versión

**Cómo implementar**:
```python
# En scripts/run_eval.py, usar Langfuse Experiments:
dataset = langfuse.get_dataset("golden_v1")
for item in dataset.items:
    trace = langfuse.trace(name="experiment_run")
    result = await ollama_client.chat(
        messages=[ChatMessage(role="user", content=item.input["text"])],
        model=model,
    )
    # El score se linkea automáticamente al experiment
    langfuse.create_score(
        trace_id=trace.id,
        name="correctness",
        value=1.0 if is_correct(result, item.expected_output) else 0.0,
    )
```

**Beneficio**: Tracking riguroso de accuracy por versión de prompt/modelo. Detección de regresiones.

### 5. Cost Tracking

**Estado actual**: Se envían `input_tokens` y `output_tokens` como `usage_details` en generations, pero no hay costos asociados.

**Oportunidad**: Langfuse v3 puede calcular costos automáticamente si se registra el modelo. Para Ollama (auto-hosted, costo $0), el valor está en:
- **Token efficiency tracking** — cuántos tokens consume cada feature
- **Budget alerts** — si se migra a una API paga (OpenAI, Claude)
- **Cost per conversation** — útil para planning de escalamiento

**Cómo implementar**: Definir un modelo custom en Langfuse UI con pricing $0 (Ollama). Los tokens ya se envían — Langfuse calculará automáticamente.

### 6. Session Replay & User Journey

**Estado actual**: `session_id = phone_number` agrupa traces por usuario. Se puede ver la secuencia de interacciones.

**Oportunidad**: Langfuse v3 Sessions permiten:
- Ver toda la conversación de un usuario como timeline
- Identificar patrones de uso (qué tools usa más, cuándo abandona)
- Detectar usuarios insatisfechos (secuencia de scores bajos)
- Filtrar sessions por duración, score promedio, o tools usados

**Ya funciona** — solo requiere explorar la UI en `Sessions` tab.

### 7. Webhooks para CI/CD

**Estado actual**: CI/CD corre tests y lint. No hay integración con observabilidad en producción.

**Oportunidad**: Langfuse v3 soporta webhooks que disparan en eventos como:
- Score bajo persistente → alerta
- Nuevo prompt activado → trigger CI eval
- Dataset actualizado → re-correr benchmark

**Cómo implementar**: Configurar webhooks desde la UI de Langfuse (`Settings → Webhooks`). El webhook puede apuntar a un endpoint de la app o a un GitHub Actions workflow.

### 8. Spans Faltantes (Quick Wins)

Algunos flujos no tienen spans y podrían beneficiarse:

| Flujo | Span sugerido | Beneficio |
|---|---|---|
| `flush_to_memory()` | `"memory:flush"` | Medir latencia de extracción de facts |
| `consolidate_memories()` | `"memory:consolidate"` | Detectar consolidaciones lentas |
| `embed_memory()` / `embed_note()` | `"embedding:index"` | Tracking de embedding backlog |
| `seed_builtin_rules()` | `"automation:seed"` | Verificar que seeds son rápidos |
| `enrich_context()` | `"ontology:enrich"` | Medir overhead del grafo |
| `process_telegram_update()` | Trace con `platform="telegram"` | Ya existe para WhatsApp, verificar Telegram |

**Implementación**: Cada uno es ~5 líneas usando el pattern existente:
```python
trace = get_current_trace()
if trace:
    async with trace.span("memory:flush", kind="span") as span:
        span.set_input({"message_count": len(messages)})
        result = await _do_flush(...)
        span.set_output({"facts_extracted": len(facts)})
```

---

## Variables de Configuración

| Variable (`.env`) | Default | Efecto |
|---|---|---|
| `TRACING_ENABLED` | `true` | Habilita todo el sistema de tracing |
| `TRACING_SAMPLE_RATE` | `1.0` | Fracción de requests traceadas (0.0–1.0) |
| `TRACE_RETENTION_DAYS` | `90` | Días antes de cleanup automático en SQLite |
| `LANGFUSE_PUBLIC_KEY` | (vacío) | API key pública — obtener de Langfuse UI |
| `LANGFUSE_SECRET_KEY` | (vacío) | API key secreta — obtener de Langfuse UI |
| `LANGFUSE_HOST` | `http://localhost:3000` | URL del servidor Langfuse |

### Setup inicial de Langfuse

1. Levantar el stack: `docker compose --profile dev up -d`
2. Ir a `http://localhost:3000` — crear cuenta admin
3. Crear un proyecto → copiar Public Key y Secret Key
4. Agregar las keys al `.env`:
   ```
   LANGFUSE_PUBLIC_KEY=pk-lf-...
   LANGFUSE_SECRET_KEY=sk-lf-...
   ```
5. Reiniciar la app — los traces empiezan a aparecer en el dashboard

---

## Archivos clave

| Archivo | Rol |
|---|---|
| `app/tracing/context.py` | `TraceContext` + `SpanData` — context manager con `contextvars` |
| `app/tracing/recorder.py` | `TraceRecorder` — singleton, dual persistence (SQLite + Langfuse) |
| `app/webhook/router.py` | Entry point de tracing (`_run_normal_flow`) + scores |
| `app/skills/executor.py` | Spans de tool calls + LLM iterations |
| `app/agent/loop.py` | Tracing de sesiones agénticas + goal_completion score |
| `app/eval/dataset.py` | `sync_dataset_to_langfuse()` |
| `docker-compose.yml` | Stack completo de Langfuse v3 (6 servicios) |

---

## Guías de Configuración Avanzada

### Setup de Annotation Queues

Las Annotation Queues permiten revisión humana estructurada de respuestas del bot.

**Crear queues desde la UI** (`Langfuse → Annotation Queues → New Queue`):

1. **Guardrail Failures**
   - Filtro: scores con `guardrail_*` < 0.5
   - Propósito: revisar respuestas que fallaron checks de calidad
   - Acción: calificar (pass/fail), agregar corrección si aplica

2. **Agent Review**
   - Filtro: tag `agent`, score `goal_completion` < 1.0
   - Propósito: revisar sesiones agénticas que no completaron su objetivo
   - Acción: calificar completitud (1-5), identificar punto de fallo

3. **New Users**
   - Filtro: traces de sesiones con < 5 traces previos del mismo `user_id`
   - Propósito: mejorar primera impresión y onboarding
   - Acción: calificar calidad (1-5), marcar para mejora

**Workflow de anotación**:
1. Abrir la queue desde el dashboard
2. Revisar cada trace: input del usuario → output del bot → spans intermedios
3. Calificar usando la escala definida (1-5 o pass/fail)
4. Opcionalmente agregar comentario textual
5. Marcar como "done" para avanzar al siguiente

**Integración con dataset** (opcional):
- Los scores de annotation (`source="human"`) se consideran en `maybe_curate_to_dataset()`
- Un score humano >= 0.8 convierte un candidate en golden confirmed
- Un score humano < 0.3 lo convierte en failure

### Setup de Custom Models para Cost Tracking

Langfuse calcula costos automáticamente si el modelo está registrado.

**Registrar modelos custom** (`Langfuse → Settings → Models → Add Custom Model`):

| Campo | qwen3.5:9b | llava:7b | nomic-embed-text |
|---|---|---|---|
| Model Name | `qwen3.5:9b` | `llava:7b` | `nomic-embed-text` |
| Match Pattern | `qwen3*` | `llava*` | `nomic*` |
| Input Price | $0.00 | $0.00 | $0.00 |
| Output Price | $0.00 | $0.00 | $0.00 |
| Unit | per 1M tokens | per 1M tokens | per 1M tokens |

> **Nota**: Con precios $0 (Ollama self-hosted), el valor está en tracking de **token efficiency** por feature. Si se migra a una API paga, solo cambiar los precios aquí.

**Verificación**: Ir a cualquier generation en Langfuse → debe mostrar el modelo (`qwen3.5:9b`) con usage details (input/output tokens). El modelo fallback se aplica automáticamente si no se envía en metadata.

### Configuración de Webhooks para CI/CD

Langfuse v3 soporta webhooks que disparan en eventos del sistema.

**Configurar desde la UI** (`Langfuse → Settings → Webhooks → Add Webhook`):

1. **Score Alert — Guardrail Degradation**
   - Evento: Score created
   - Filtro: `name = "guardrail_pass_rate"` AND `value < 0.7`
   - URL: Endpoint de alerta (Slack webhook, PagerDuty, etc.)
   - Propósito: Detectar degradación de calidad en tiempo real

2. **Dataset Updated — Trigger Re-eval**
   - Evento: Dataset item created
   - Filtro: `dataset_name = "localforge-eval"`
   - URL: GitHub Actions webhook o endpoint custom
   - Propósito: Re-correr benchmark cuando se agregan nuevos golden/correction entries

3. **Prompt Activated — Notify Deployment**
   - Evento: Prompt updated
   - Filtro: label includes `"production"`
   - URL: Slack channel de deploy
   - Propósito: Notificar al equipo cuando un prompt cambia de versión

**Endpoint receptor futuro** (opcional):
```
POST /webhook/langfuse
Content-Type: application/json

{
  "event": "score.created",
  "data": { "traceId": "...", "name": "guardrail_pass_rate", "value": 0.5 }
}
```

Se podría agregar en `app/health/router.py` para disparar automation rules basadas en eventos de Langfuse.

---

## Decisiones de diseño

| Decisión | Alternativa descartada | Motivo |
|---|---|---|
| Langfuse SDK v3 (OTLP) | SDK v2 (REST directo) | v3 usa OpenTelemetry nativo, más eficiente y estándar |
| Dual persistence (SQLite + Langfuse) | Solo Langfuse | SQLite permite queries locales sin dependencia de red |
| Best-effort (nunca propaga errores) | Fail-fast | La app debe funcionar si Langfuse está caído |
| `session_id = phone_number` | UUID por conversación | Agrupa toda la historia del usuario en una session |
| Server v3 completo (6 servicios) | Solo web + postgres (v2) | ClickHouse necesario para performance; Redis/MinIO para worker |
| YAML anchors (`*langfuse-env`) | Duplicar variables | DRY — 30+ variables compartidas entre web y worker |

---

## Gotchas y Edge Cases

- **OTLP 404**: Si ves `Failed to export span batch code: 404`, el server Langfuse no es v3 o no está corriendo. Verificar que `langfuse-web` y `langfuse-worker` están up.
- **API keys vacías**: Si `LANGFUSE_PUBLIC_KEY` o `LANGFUSE_SECRET_KEY` están vacías, el SDK se inicializa como no-op. Los traces se guardan solo en SQLite.
- **Flush en shutdown**: `langfuse.flush()` se llama en `finish_trace()` y en el lifespan shutdown. Si la app crashea, los últimos spans pueden perderse.
- **ClickHouse startup**: ClickHouse puede tardar 10-30s en iniciar. Las healthchecks con `depends_on: condition: service_healthy` previenen que web/worker arranquen antes.
- **MinIO bucket**: Se crea automáticamente con `mkdir -p /data/langfuse` en el entrypoint. Si falla, los eventos grandes no se persisten.
- **Secrets en compose**: Los valores default (`mysecret`, `postgres`, `clickhouse`, `minio`) son para desarrollo. En producción, usar secrets reales.
- **Puerto 5433**: PostgreSQL de Langfuse expone en 5433 (no 5432) para no colisionar con otros PostgreSQL del host.
- **Trace IDs**: Langfuse v3 requiere 32 hex chars. Se usa `Langfuse.create_trace_id(seed=our_uuid)` para convertir UUIDs estándar.
