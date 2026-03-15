# Plan de Implementación — Arquitectura de Evaluación y Mejora Continua

> Documento técnico que baja los 6 pilares conceptuales a cambios concretos en el codebase de LocalForge.
>
> **Revisión 2** — Corregido contra el codebase real. Incluye errores detectados en la v1,
> prerequisitos faltantes, y detalles de integración verificados contra el código fuente.

---

## Resumen de Fases

| Fase | Pilar | Dependencias | Archivos principales |
|------|-------|-------------|---------------------|
| 1 | Guardrails (pre-entrega) | Ninguna | `app/guardrails/`, `app/webhook/router.py` |
| 2 | Trazabilidad estructurada | Fase 1 | `app/tracing/`, `app/webhook/router.py`, `app/skills/executor.py`, `app/whatsapp/client.py` |
| 3 | Evaluación en 3 capas | Fase 2 | `app/eval/`, `app/webhook/parser.py`, `app/commands/builtins.py` |
| 4 | Dataset vivo | Fases 2+3 | `app/eval/dataset.py`, `app/database/db.py` |
| 5 | Auto-evolución | Fases 3+4 | `app/eval/evolution.py`, `app/skills/tools/eval_tools.py` |
| 6 | Self-evaluation skill | Fases 2+3+4 | `skills/eval/SKILL.md`, `app/skills/tools/eval_tools.py` |

### Prerequisitos transversales

Antes de empezar cualquier fase, hay cambios de infraestructura que afectan a varias fases:

**1. `WhatsAppClient.send_message()` debe retornar el `wamid` del mensaje saliente.**

Actualmente `_send_single_message()` (`app/whatsapp/client.py:60`) hace POST y no captura
el `messages[0].id` que retorna la Graph API. Sin este ID:
- Las reacciones del usuario (Fase 3) no se pueden vincular a trazas
- Las trazas (Fase 2) no se pueden vincular a mensajes de WA

Cambio requerido en `app/whatsapp/client.py`:
```python
async def _send_single_message(self, to: str, text: str) -> str | None:
    """Send a message. Returns the wa_message_id from Graph API, or None."""
    # ... existing code ...
    resp = await self._http.post(url, json=payload, headers=self._headers)
    # ... existing error handling ...
    # NUEVO: capturar outgoing message ID
    try:
        return resp.json()["messages"][0]["id"]
    except (KeyError, IndexError):
        return None

async def send_message(self, to: str, text: str) -> str | None:
    """Send message (splitting if needed). Returns wa_message_id of FIRST chunk."""
    chunks = split_message(text)
    first_id = None
    for i, chunk in enumerate(chunks):
        msg_id = await self._send_single_message(to, chunk)
        if i == 0:
            first_id = msg_id
    return first_id
```

**2. `contextvars` para propagar `TraceContext` sin cambiar firmas.**

El tracing necesita fluir a través de `_handle_message` → `execute_tool_loop` → `_run_tool_call`
sin modificar las firmas de estas funciones. La solución estándar en Python async:

```python
# app/tracing/context.py
import contextvars
_current_trace: contextvars.ContextVar[TraceContext | None] = contextvars.ContextVar(
    "current_trace", default=None,
)

def get_current_trace() -> TraceContext | None:
    return _current_trace.get()
```

Esto permite que `executor.py` acceda al trace sin recibirlo como parámetro.

---

## Fase 1: Guardrails — Validación Pre-Entrega

### Objetivo
Interceptar la respuesta del LLM **antes** de enviarla al usuario. Atrapar errores obvios (idioma incorrecto, respuesta vacía, datos sensibles, tool results ignorados) con latencia <200ms para checks determinísticos.

### Modelo de datos

```python
# app/guardrails/models.py
from pydantic import BaseModel

class GuardrailResult(BaseModel):
    passed: bool
    check_name: str           # "language_match", "not_empty", "no_pii", etc.
    details: str = ""         # Motivo del fallo si passed=False
    latency_ms: float = 0.0

class GuardrailReport(BaseModel):
    passed: bool              # ALL checks passed
    results: list[GuardrailResult]
    total_latency_ms: float
```

### Checks a implementar

#### 1.1 Checks determinísticos (sin LLM)

| Check | Lógica | Implementación |
|-------|--------|---------------|
| `not_empty` | `len(reply.strip()) > 0` | Inline |
| `language_match` | Detectar idioma del input y output, comparar | `langdetect` (ver nota abajo) |
| `no_pii` | Regex para DNI argentino, tokens (Bearer/sk-/whsec_), emails, phones en el output que no estaban en el input del usuario | Regex pipeline |
| `excessive_length` | Respuesta > 8000 chars (posible generación descontrolada) | Inline — **NO** 4096, porque `split_message()` ya maneja el chunking |
| `no_raw_tool_json` | La respuesta no contiene JSON crudo de tool results | Regex `\{.*"tool_call"` |

> **Nota sobre `language_match`**: `langdetect` es poco confiable con textos cortos (<20 chars).
> Aplicar SOLO cuando `len(user_text) >= 30 AND len(reply) >= 30`. Para mensajes cortos, skip.
> Muchos mensajes de WhatsApp son de 2-5 palabras — forzar detección ahí genera falsos positivos
> masivos.

#### 1.2 Checks con LLM (solo si los determinísticos pasan)

| Check | Prompt | Cuándo |
|-------|--------|--------|
| `tool_coherence` | "¿La respuesta integra los resultados de las tools o los ignora?" | Solo si hubo tool calls |
| `hallucination_check` | "¿La respuesta afirma datos que no aparecen en el contexto ni tool results?" | Solo en respuestas factuales |

> **Decisión de diseño**: Los LLM checks son opcionales y configurables. En la primera iteración, solo se implementan los determinísticos. Los LLM checks se activan cuando la trazabilidad (Fase 2) esté funcionando para poder medir su impacto.

### Integración en el pipeline

**Archivo**: `app/webhook/router.py` — `_handle_message()`

El guardrail se inserta **entre** la generación del LLM y el envío por WhatsApp. Punto de inserción exacto: líneas 560-577 actuales.

```python
# Después de execute_tool_loop / ollama_client.chat (línea ~572 actual)
# El flujo actual es:
#   if has_tools:
#       reply = await execute_tool_loop(...)
#   else:
#       reply = await ollama_client.chat(context)

# NUEVO: determinar si se usaron tools (para el check de tool_coherence)
tools_were_used = has_tools and pre_classified is not None and pre_classified != ["none"]

# NUEVO: Guardrail pipeline (solo en el flujo normal de texto, no commands/image/onboarding)
if settings.guardrails_enabled:
    from app.guardrails.pipeline import run_guardrails
    guardrail_report = await run_guardrails(
        user_text=user_text,
        reply=reply,
        tool_calls_used=tools_were_used,
        settings=settings,
    )

    if not guardrail_report.passed:
        logger.warning(
            "Guardrails failed: %s",
            [r.check_name for r in guardrail_report.results if not r.passed],
        )
        # UN solo intento de remediación — sin recursión
        reply = await _handle_guardrail_failure(
            guardrail_report, context, ollama_client, reply,
        )

await repository.save_message(conv_id, "assistant", reply)
wa_message_id = await wa_client.send_message(msg.from_number, markdown_to_whatsapp(reply))
# wa_message_id se usa en Fase 2 para vincular traza con mensaje WA
```

> **Scope**: Los guardrails aplican SOLO al flujo normal de texto (líneas 490-608 de
> `_handle_message`). No aplican a:
> - Comandos (`/remember`, `/help`, etc.) — output determinístico
> - Respuestas de error hardcodeadas ("Sorry, I couldn't process...")
> - Flujo de onboarding — maneja su propia validación
> - Flujo de imagen — apply here too would be ideal but deferred to v2

**Estrategia de fallo** (`_handle_guardrail_failure`):

Se ejecuta **una sola vez**, sin recursión. Si el retry también falla, se envía el reply original.

```python
async def _handle_guardrail_failure(
    report: GuardrailReport,
    context: list[ChatMessage],
    ollama_client: OllamaClient,
    original_reply: str,
) -> str:
    """Attempt one remediation. Returns fixed reply or original."""
    failed = [r for r in report.results if not r.passed]
    failed_names = {r.check_name for r in failed}

    # PII: redact in-place, no re-prompt needed
    if "no_pii" in failed_names:
        from app.guardrails.checks import redact_pii
        return redact_pii(original_reply)

    # Empty: try once more
    if "not_empty" in failed_names:
        retry = await ollama_client.chat(context)
        return retry if retry.strip() else "Disculpa, no pude generar una respuesta."

    # Language: re-prompt with explicit instruction
    if "language_match" in failed_names:
        lang_result = next(r for r in failed if r.check_name == "language_match")
        hint_msg = ChatMessage(
            role="user",
            content=f"IMPORTANT: Respond in {lang_result.details}. Repeat your previous answer in that language.",
        )
        return await ollama_client.chat(context + [hint_msg])

    # Everything else (tool_coherence, hallucination, excessive_length): log + pass through
    return original_reply
```

### Archivos nuevos

```
app/guardrails/
  __init__.py
  models.py        # GuardrailResult, GuardrailReport
  checks.py        # Funciones individuales: check_not_empty, check_language_match, etc.
  pipeline.py      # run_guardrails() — orquesta todos los checks
```

### Config (`app/config.py`)

```python
# Guardrails
guardrails_enabled: bool = True
guardrails_language_check: bool = True
guardrails_pii_check: bool = True
guardrails_llm_checks: bool = False  # Activar en Fase 2
```

### Dependencias nuevas

```
langdetect>=1.0.9    # Detección de idioma (~100KB, puro Python)
```

> **Alternativa**: `fasttext` con lid.176.ftz es más preciso pero requiere descargar el modelo (~900KB). Empezar con `langdetect`, migrar si la precisión es insuficiente.

### Tests

```
tests/guardrails/
  test_checks.py       # Unit tests para cada check
  test_pipeline.py     # Integration: pipeline completa con mocks
  test_integration.py  # E2E: webhook → guardrails → respuesta
```

---

## Fase 2: Trazabilidad Estructurada

### Objetivo
Reemplazar logs planos con **trazas jerárquicas** que capturen el árbol completo de decisiones de cada interacción: desde la recepción del mensaje hasta la entrega.

### Decisión de herramienta

**Opción A: Langfuse self-hosted** (Docker, MIT license)
- Pro: dashboard web, scoring, datasets, prompt management integrados
- Contra: requiere PostgreSQL + Docker adicional, overhead de red

**Opción B: Trazas internas en SQLite**
- Pro: zero dependencies, funciona offline, se integra con el stack actual
- Contra: hay que construir el dashboard y las queries desde cero

**Recomendación**: Empezar con **Opción B** (SQLite) para no agregar infraestructura. El schema es compatible para migrar a Langfuse después exportando las trazas. La estructura de datos es la misma.

### Schema de base de datos

```sql
-- app/database/db.py — agregar a SCHEMA
-- Nota: CREATE TABLE IF NOT EXISTS asegura compatibilidad con DBs existentes
-- sin necesidad de un sistema de migraciones.

CREATE TABLE IF NOT EXISTS traces (
    id            TEXT PRIMARY KEY,  -- uuid.uuid4().hex (stdlib)
    phone_number  TEXT NOT NULL,
    input_text    TEXT NOT NULL,
    output_text   TEXT,
    wa_message_id TEXT,             -- ID del mensaje saliente de WA (para vincular reacciones)
    message_type  TEXT NOT NULL DEFAULT 'text'
                  CHECK (message_type IN ('text', 'audio', 'image')),
    status        TEXT NOT NULL DEFAULT 'started'
                  CHECK (status IN ('started', 'completed', 'failed')),
    started_at    TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at  TEXT,
    metadata      TEXT NOT NULL DEFAULT '{}'  -- JSON: model, tokens, categories, etc.
);
CREATE INDEX IF NOT EXISTS idx_traces_phone ON traces(phone_number, started_at);
CREATE INDEX IF NOT EXISTS idx_traces_status ON traces(status);
CREATE INDEX IF NOT EXISTS idx_traces_wa_msg ON traces(wa_message_id);

CREATE TABLE IF NOT EXISTS trace_spans (
    id          TEXT PRIMARY KEY,  -- uuid.uuid4().hex
    trace_id    TEXT NOT NULL REFERENCES traces(id),
    parent_id   TEXT REFERENCES trace_spans(id),  -- NULL = root span
    name        TEXT NOT NULL,     -- "classify_intent", "tool_loop", "guardrails", etc.
    kind        TEXT NOT NULL DEFAULT 'span'
                CHECK (kind IN ('span', 'generation', 'tool', 'guardrail')),
    input       TEXT,              -- JSON
    output      TEXT,              -- JSON
    status      TEXT NOT NULL DEFAULT 'started'
                CHECK (status IN ('started', 'completed', 'failed')),
    started_at  TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    latency_ms  REAL,
    metadata    TEXT NOT NULL DEFAULT '{}'  -- JSON: model, tokens, tool_name, etc.
);
CREATE INDEX IF NOT EXISTS idx_spans_trace ON trace_spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_spans_kind ON trace_spans(kind);

CREATE TABLE IF NOT EXISTS trace_scores (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id   TEXT NOT NULL REFERENCES traces(id),
    span_id    TEXT REFERENCES trace_spans(id),  -- NULL = score de la traza completa
    name       TEXT NOT NULL,     -- "helpfulness", "language_match", "tool_use_correct"
    value      REAL NOT NULL,     -- 0.0 - 1.0 (o booleano 0/1)
    source     TEXT NOT NULL DEFAULT 'system'
               CHECK (source IN ('system', 'user', 'llm_judge', 'human')),
    comment    TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_scores_trace ON trace_scores(trace_id);
CREATE INDEX IF NOT EXISTS idx_scores_name ON trace_scores(name, value);
```

> **Cambio vs v1**: Se agregó `wa_message_id` y `message_type` a `traces`.
> Se agregó CHECK constraint a `trace_spans.status`. Se agregó índice sobre `wa_message_id`
> para poder vincular reacciones de WA a trazas (O(1) lookup).
> IDs son `uuid.uuid4().hex` (32 chars hex, sin guiones).

### Módulo de tracing

```
app/tracing/
  __init__.py
  models.py      # Trace, Span, Score (Pydantic)
  context.py     # TraceContext: context manager que auto-mide latencia
  recorder.py    # TraceRecorder: persiste en SQLite (async, best-effort)
  middleware.py   # Helpers para instrumentar el pipeline
```

#### API del TraceContext

```python
# app/tracing/context.py
import contextvars
import time
import uuid
from contextlib import asynccontextmanager

_current_trace: contextvars.ContextVar["TraceContext | None"] = contextvars.ContextVar(
    "current_trace", default=None,
)

def get_current_trace() -> "TraceContext | None":
    return _current_trace.get()


class TraceContext:
    def __init__(self, phone_number: str, input_text: str, recorder: "TraceRecorder"):
        self.trace_id = uuid.uuid4().hex
        self.phone_number = phone_number
        self.input_text = input_text
        self._recorder = recorder
        self._token: contextvars.Token | None = None

    async def __aenter__(self):
        self._token = _current_trace.set(self)
        await self._recorder.start_trace(self.trace_id, self.phone_number, self.input_text)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        status = "failed" if exc_type else "completed"
        await self._recorder.finish_trace(self.trace_id, status)
        if self._token:
            _current_trace.reset(self._token)
        return False  # don't swallow exceptions

    @asynccontextmanager
    async def span(self, name: str, kind: str = "span", parent_id: str | None = None):
        span_id = uuid.uuid4().hex
        start = time.monotonic()
        span_data = SpanData(span_id=span_id, name=name, kind=kind)
        try:
            await self._recorder.start_span(self.trace_id, span_id, name, kind, parent_id)
            yield span_data
        except Exception:
            span_data._status = "failed"
            raise
        finally:
            latency_ms = (time.monotonic() - start) * 1000
            await self._recorder.finish_span(
                span_id, span_data._status, latency_ms,
                input_data=span_data._input, output_data=span_data._output,
                metadata=span_data._metadata,
            )

    async def add_score(self, name: str, value: float, source: str = "system",
                        comment: str | None = None, span_id: str | None = None):
        await self._recorder.add_score(self.trace_id, name, value, source, comment, span_id)

    async def set_output(self, output_text: str):
        await self._recorder.set_trace_output(self.trace_id, output_text)

    async def set_wa_message_id(self, wa_message_id: str):
        await self._recorder.set_trace_wa_message_id(self.trace_id, wa_message_id)
```

**Propagación**: `TraceContext` se establece via `contextvars` al entrar en `_handle_message`.
`executor.py` y cualquier otro módulo acceden al trace via `get_current_trace()` sin
cambios en las firmas de funciones.

```python
# Uso en router.py — el trace se crea COMO context manager:
from app.tracing.context import TraceContext, get_current_trace

async with TraceContext(msg.from_number, user_text, recorder) as trace_ctx:
    # ... todo el pipeline normal ...
    async with trace_ctx.span("phase_a") as span:
        query_embedding, _, daily_logs = await asyncio.gather(...)
    # ...

# Uso en executor.py — accede via contextvar, NO via parámetro:
from app.tracing.context import get_current_trace

async def _run_tool_call(tc, skill_registry, mcp_manager):
    trace_ctx = get_current_trace()
    # Si tracing no está habilitado, trace_ctx es None — los spans se skipean
    if trace_ctx:
        async with trace_ctx.span(f"tool_{tool_name}", kind="tool") as span:
            span.set_metadata({"tool_name": tool_name})
            result = await skill_registry.execute_tool(tool_call)
            span.set_output({"result": result.content[:200]})
    else:
        result = await skill_registry.execute_tool(tool_call)
```

> **Nota sobre `classify_intent` como `asyncio.Task`**: El classify se lanza como
> `asyncio.create_task()` en router.py:518, no se puede wrappear en un `async with span`.
> Solución: instrumentar DENTRO de `classify_intent()` en `app/skills/router.py`,
> leyendo el trace del contextvar. Como `create_task` copia el contextvar context,
> el trace está disponible dentro del task.

```python
# app/skills/router.py — classify_intent instrumentado:
async def classify_intent(user_message, ollama_client):
    trace_ctx = get_current_trace()
    if trace_ctx:
        async with trace_ctx.span("classify_intent", kind="generation") as span:
            span.set_input({"user_message": user_message[:100]})
            result = await _do_classify(user_message, ollama_client)
            span.set_output({"categories": result})
            return result
    return await _do_classify(user_message, ollama_client)
```

**Principio**: Las trazas son **best-effort**. Errores de tracing nunca bloquean el pipeline principal. `TraceRecorder` wrappea toda la persistencia en `try/except` con logging. Si el recorder falla, el pipeline continúa normalmente.

### Instrumentación de `_handle_message`

Los puntos de instrumentación en `app/webhook/router.py`:

| Span | Kind | Ubicación actual (línea aprox) |
|------|------|-------------------------------|
| `message_received` | span | Inicio de `_handle_message` (~351) |
| `audio_transcription` | span | Bloque audio (~367) |
| `image_processing` | generation | Bloque image (~386) |
| `classify_intent` | generation | `asyncio.create_task(classify_intent(...))` (~518) |
| `phase_a` | span | `asyncio.gather` embed+save+logs (~521) |
| `phase_b` | span | `asyncio.gather` memories+notes+summary+history (~528) |
| `build_context` | span | `_build_context()` (~548) |
| `llm_generation` | generation | `execute_tool_loop` o `ollama_client.chat` (~562) |
| `guardrails` | guardrail | Pipeline de guardrails (Fase 1) |
| `delivery` | span | `wa_client.send_message` (~577) |

### Instrumentación de `execute_tool_loop`

En `app/skills/executor.py`:

| Span | Kind | Ubicación |
|------|------|-----------|
| `tool_loop` | span | Wrapping del loop completo |
| `tool_iteration_{n}` | span | Cada iteración del loop |
| `llm_tool_call` | generation | `ollama_client.chat_with_tools` (~131) |
| `tool_{name}` | tool | Cada `_run_tool_call` (~150) |

### Scores automáticos desde guardrails

Los resultados de Fase 1 se convierten en scores de la traza:

```python
for result in guardrail_report.results:
    await trace_ctx.add_score(
        name=result.check_name,
        value=1.0 if result.passed else 0.0,
        source="system",
    )
```

### Config

```python
# Tracing
tracing_enabled: bool = True
tracing_sample_rate: float = 1.0  # 1.0 = trace everything, 0.5 = 50%
```

---

## Fase 3: Evaluación en 3 Capas

### Capa 1 — Señales implícitas del usuario (automáticas)

#### 3.1.1 Reacciones de WhatsApp

Las reacciones requieren cambios en **3 archivos** porque actualmente son descartadas silenciosamente:

**1. Modelo** (`app/models.py`): Nuevo tipo para reacciones.

```python
class WhatsAppReaction(BaseModel):
    from_number: str
    reacted_message_id: str  # wa_message_id del mensaje al que reaccionó
    emoji: str
```

**2. Parser** (`app/webhook/parser.py`): Actualmente `_SUPPORTED_TYPES = {"text", "audio", "image"}`
filtra las reacciones en la línea 14. Agregar una función separada:

```python
def extract_reactions(payload: dict) -> list[WhatsAppReaction]:
    """Extract reactions from a WhatsApp webhook payload."""
    reactions: list[WhatsAppReaction] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                if msg.get("type") != "reaction":
                    continue
                reaction = msg.get("reaction", {})
                if not reaction.get("message_id") or not reaction.get("emoji"):
                    continue
                reactions.append(WhatsAppReaction(
                    from_number=msg["from"],
                    reacted_message_id=reaction["message_id"],
                    emoji=reaction["emoji"],
                ))
    return reactions
```

> **No** agregar "reaction" a `_SUPPORTED_TYPES` — las reacciones NO pasan por el
> pipeline de mensajes (dedup, rate limit, `_handle_message`).

**3. Webhook endpoint** (`app/webhook/router.py`): Las reacciones se procesan en el `POST /webhook`
handler (línea ~88), ANTES del loop de mensajes. Son fire-and-forget:

```python
@router.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks) -> Response:
    # ... existing signature validation ...

    # NUEVO: Process reactions (lightweight, no dedup needed)
    from app.webhook.parser import extract_reactions
    reactions = extract_reactions(payload)
    for reaction in reactions:
        background_tasks.add_task(_handle_reaction, reaction, repository)

    # ... existing message processing loop ...
```

**Handler**:

```python
async def _handle_reaction(reaction: WhatsAppReaction, repository) -> None:
    """Convert WhatsApp reaction to a trace score. Best-effort, no exceptions propagated."""
    try:
        # Buscar la traza vinculada al wa_message_id que recibió la reacción
        # Requiere que traces.wa_message_id esté poblado (prerequisito de Fase 2)
        trace_id = await repository.get_trace_id_by_wa_message_id(reaction.reacted_message_id)
        if not trace_id:
            logger.debug("Reaction to unknown message %s, ignoring", reaction.reacted_message_id)
            return

        # Mapear emoji a score
        score_map = {"👍": 1.0, "👎": 0.0, "❤️": 1.0, "😂": 0.8, "😮": 0.5, "😢": 0.2, "🙏": 0.9}
        value = score_map.get(reaction.emoji, 0.5)

        await repository.save_trace_score(
            trace_id=trace_id,
            name="user_reaction",
            value=value,
            source="user",
            comment=reaction.emoji,
        )
        logger.info("Reaction %s from %s → trace %s (score=%.1f)",
                     reaction.emoji, reaction.from_number, trace_id, value)
    except Exception:
        logger.warning("Failed to process reaction", exc_info=True)
```

> **Prerequisito**: Esto SOLO funciona si `traces.wa_message_id` se está guardando
> (Fase 2 + cambio en `WhatsAppClient` del prerequisito transversal).
> Si se implementa Fase 3 antes de Fase 2, las reacciones se loguean pero no se vinculan.

#### 3.1.2 Detección de preguntas repetidas

**En `_handle_message`**, después de Phase B, comparar el embedding del mensaje actual contra los últimos N mensajes del usuario:

```python
# Si el cosine similarity > 0.9 con un mensaje de las últimas 24h → flag
if query_embedding and await _is_repeated_question(query_embedding, conv_id, repository):
    await trace_ctx.add_score(name="repeated_question", value=0.0, source="system")
```

#### 3.1.3 Detección de correcciones

Heurística basada en patrones para detectar cuando el usuario corrige al bot.

> **Problema con la v1**: Patrones como `r"^no[,.]?\s"` son demasiado amplios.
> "No me acuerdo", "No problem", "No pasa nada" todos matchean. Esto generaría
> falsos positivos masivos.

**Enfoque corregido**: Patrones más estrictos + el score se registra como `0.3`
(sospecha), no `0.0` (falla confirmada). Solo patrones que son casi siempre correcciones:

```python
# Patrones de alta confianza (casi siempre son correcciones)
CORRECTION_PATTERNS_HIGH = [
    r"te pregunté|te pregunte",
    r"no era eso",
    r"eso no es lo que",
    r"no te pedí|no te pedi",
    r"está mal|esta mal",
    r"eso es incorrecto",
    r"no, (?:yo )?(?:dije|quise|pregunté)",
]

# Patrones de baja confianza (pueden ser correcciones O mensajes normales)
# Se registran como score 0.5 (neutral, para análisis posterior)
CORRECTION_PATTERNS_LOW = [
    r"^no[,.]?\s+(?:eso|así|esa|ese)",
    r"mal$",
]
```

La detección se corre al inicio de `_handle_message`, ANTES del pipeline normal,
contra la traza anterior del mismo usuario:

```python
if user_text and trace_ctx:
    correction_score = _detect_correction(user_text)
    if correction_score is not None:
        prev_trace_id = await repository.get_latest_trace_id(msg.from_number)
        if prev_trace_id:
            await trace_ctx.add_score(
                name="user_correction",
                value=correction_score,
                source="system",
                comment=f"Detected correction pattern in: {user_text[:50]}",
                # Score se agrega a la traza ANTERIOR, no a la actual
            )
            # Para esto se necesita repository.add_score_to_trace(prev_trace_id, ...)
            # en lugar de usar el trace_ctx actual
```

### Capa 2 — Evaluación automatizada (offline)

#### 3.2.1 Test suite con DeepEval

> **Dependencia externa importante**: DeepEval usa GPT-4 como evaluador por defecto.
> Para un proyecto 100% local con Ollama, hay dos opciones:
>
> **Opción A**: Configurar DeepEval con modelo custom via su API de custom models.
> DeepEval soporta modelos locales a través de su `DeepEvalBaseLLM` interface.
>
> **Opción B**: Implementar evaluadores custom que llamen a Ollama directamente,
> sin depender del evaluator backend de DeepEval. Más trabajo, pero zero dependencia externa.
>
> **Recomendación**: Opción B para los evaluadores que se corren frecuentemente
> (language, tool_use). Opción A solo para evaluaciones deep esporádicas.

```
tests/eval/
  conftest.py          # Fixtures: load dataset, OllamaClient para evaluación
  metrics.py           # Evaluadores custom que usan Ollama
  test_language.py     # ¿Responde en el idioma correcto?
  test_tool_use.py     # ¿Usa tools cuando debería?
  test_coherence.py    # ¿La respuesta es coherente con el contexto?
  test_safety.py       # Red teaming: prompt injection, data exfiltration
  test_multiturn.py    # Evaluación de conversaciones multi-turn
  helpers.py           # run_single_evaluation() — ejecuta pipeline simplificado
```

**Ejecución**: `pytest tests/eval/ -v -m eval` — separado del test suite normal.
Requiere Ollama corriendo. Agregar al `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = ["eval: marks tests as evaluation tests (deselect with '-m \"not eval\"')"]
```

#### 3.2.2 Métricas custom con G-Eval (o evaluador Ollama local)

```python
# tests/eval/metrics.py
# Opción A: DeepEval con modelo custom
from deepeval.metrics import GEval
from deepeval.models import DeepEvalBaseLLM

class OllamaEvaluator(DeepEvalBaseLLM):
    """Adapter para usar Ollama como evaluador en DeepEval."""
    def __init__(self, base_url="http://localhost:11434", model="qwen3.5:9b"):
        self.base_url = base_url
        self.model = model
    # ... implementar generate(), a_generate(), get_model_name()

language_metric = GEval(
    name="Language Consistency",
    criteria="The response is in the same language as the user's message.",
    evaluation_steps=[
        "Identify the language of the input message",
        "Identify the language of the response",
        "Check if they match",
    ],
    model=OllamaEvaluator(),  # Usar Ollama en lugar de GPT-4
)
```

```python
# Opción B: Evaluador simple que llama a Ollama directamente (sin DeepEval)
# tests/eval/metrics.py

async def evaluate_language_match(input_text: str, output_text: str,
                                   ollama_client: OllamaClient) -> float:
    """Evalúa si la respuesta está en el mismo idioma que el input. Returns 0.0-1.0."""
    prompt = (
        "You are a language consistency evaluator.\n"
        f"User message: {input_text}\n"
        f"Assistant response: {output_text}\n\n"
        "Is the response in the same language as the user message? "
        "Reply with ONLY a number from 0.0 to 1.0 (0=different language, 1=same language)."
    )
    result = await ollama_client.chat(
        [ChatMessage(role="user", content=prompt)],
    )
    try:
        return float(result.strip())
    except ValueError:
        return 0.5  # indeterminate
```

#### 3.2.3 Pipeline de evaluación simplificado

Los tests de eval necesitan ejecutar el pipeline de LocalForge **sin WhatsApp**. Esto requiere
un helper que construya el contexto mínimo:

```python
# tests/eval/helpers.py
async def run_single_evaluation(
    input_text: str,
    ollama_client: OllamaClient,
    skill_registry: SkillRegistry,
    system_prompt: str = "...",
) -> str:
    """Run a simplified pipeline: build context → LLM → reply. No WA, no DB."""
    context = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content=input_text),
    ]
    # Sin tools, para evaluar solo la calidad de respuesta
    return await ollama_client.chat(context)
```

### Capa 3 — Evaluación humana explícita

#### 3.3.1 Comando `/feedback`

**Archivo**: `app/commands/builtins.py`

> **Nota**: `CommandContext` ya tiene `ollama_client` disponible. Se usa para
> analizar el sentimiento del feedback al momento de escribirlo, no después.

```python
async def cmd_feedback(args: str, ctx: CommandContext) -> str:
    """Tag the last interaction with human feedback."""
    if not args.strip():
        return "Uso: /feedback <comentario>\nEjemplo: /feedback La respuesta estuvo bien pero en inglés"

    # Buscar la última traza de este usuario
    trace_id = await ctx.repository.get_latest_trace_id(ctx.phone_number)
    if not trace_id:
        return "No encontré una interacción reciente para evaluar."

    # Analizar sentimiento del feedback para asignar un score numérico
    sentiment_value = 0.5  # default neutral
    if ctx.ollama_client:
        try:
            from app.models import ChatMessage
            result = await ctx.ollama_client.chat([ChatMessage(
                role="user",
                content=(
                    f"Rate the sentiment of this feedback about an AI response on a scale of 0.0 to 1.0. "
                    f"0.0=very negative, 0.5=neutral, 1.0=very positive. "
                    f"Reply ONLY with the number.\n\nFeedback: {args.strip()}"
                ),
            )])
            sentiment_value = max(0.0, min(1.0, float(result.strip())))
        except (ValueError, Exception):
            pass  # keep default 0.5

    await ctx.repository.save_trace_score(
        trace_id=trace_id,
        name="human_feedback",
        value=sentiment_value,
        source="human",
        comment=args.strip(),
    )
    return "Gracias por el feedback. Lo voy a tener en cuenta para mejorar."
```

#### 3.3.2 Comando `/rate`

```python
async def cmd_rate(args: str, ctx: CommandContext) -> str:
    """Rate the last response on a 1-5 scale."""
    try:
        score = int(args.strip())
        if not 1 <= score <= 5:
            raise ValueError
    except ValueError:
        return "Uso: /rate <1-5>\nEjemplo: /rate 4"

    trace = await ctx.repository.get_latest_trace(ctx.phone_number)
    if not trace:
        return "No encontré una interacción reciente para evaluar."

    await ctx.repository.save_trace_score(
        trace_id=trace.id,
        name="human_rating",
        value=score / 5.0,  # Normalizar a 0-1
        source="human",
    )
    return f"Calificación {score}/5 registrada. ¡Gracias!"
```

---

## Fase 4: Dataset Vivo

### Objetivo
Convertir trazas de producción en un dataset de evaluación reutilizable.

### Schema

```sql
CREATE TABLE IF NOT EXISTS eval_dataset (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id    TEXT REFERENCES traces(id),
    entry_type  TEXT NOT NULL CHECK (entry_type IN ('golden', 'failure', 'correction')),
    input_text  TEXT NOT NULL,
    expected_output TEXT,         -- NULL para failures sin corrección
    actual_output TEXT NOT NULL,
    scores      TEXT NOT NULL DEFAULT '{}',  -- JSON: {"language_match": 1.0, "helpfulness": 0.8}
    metadata    TEXT NOT NULL DEFAULT '{}',  -- JSON: context, tools_used, categories, etc.
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dataset_type ON eval_dataset(entry_type);

-- Tags como tabla separada para queries eficientes (SQLite no puede indexar JSON arrays)
CREATE TABLE IF NOT EXISTS eval_dataset_tags (
    dataset_id  INTEGER NOT NULL REFERENCES eval_dataset(id) ON DELETE CASCADE,
    tag         TEXT NOT NULL,
    PRIMARY KEY (dataset_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_dataset_tag_name ON eval_dataset_tags(tag);
```

> **Cambio vs v1**: `tags` se movió a una tabla join en lugar de JSON array.
> `WHERE tag = 'language'` ahora usa un índice real en lugar de `LIKE '%language%'`
> sobre un TEXT column.

### Flujos de curación

#### 4.1 Curación automática

En background, después de que una traza se completa. Se ejecuta como `_track_task()`
al final de `_handle_message`, similar a `maybe_summarize`.

> **Problema de la v1**: La lógica de golden requería `has_positive_user`, pero la
> gran mayoría de trazas no tienen scores de usuario (reacciones/ratings son raros).
> Esto significaría que casi nada se cura como golden automáticamente.
>
> **Solución**: Tres tiers de curación:
> - **Golden confirmado**: system scores altos + señal positiva del usuario
> - **Golden candidato**: system scores altos + sin señal negativa (no requiere positiva)
> - **Failure**: cualquier score < 0.3 o señal negativa del usuario

```python
async def maybe_curate_to_dataset(trace_id: str, repository) -> None:
    """Auto-curate trace to dataset based on scores. Best-effort."""
    try:
        scores = await repository.get_trace_scores(trace_id)
        if not scores:
            return  # No scores = no curation

        system_scores = [s for s in scores if s.source == "system"]
        user_scores = [s for s in scores if s.source in ("user", "human")]

        # Skip si no hay scores del sistema (guardrails deshabilitados, etc.)
        if not system_scores:
            return

        all_system_high = all(s.value >= 0.8 for s in system_scores)
        any_system_failure = any(s.value < 0.3 for s in system_scores)
        has_positive_user = any(s.value >= 0.8 for s in user_scores)
        has_negative_user = any(s.value < 0.3 for s in user_scores)

        # Failure: prioridad (detectar problemas es más valioso que confirmar éxitos)
        if any_system_failure or has_negative_user:
            await repository.add_dataset_entry(trace_id, entry_type="failure")
            return

        # Golden confirmado: system OK + user confirmó
        if all_system_high and has_positive_user:
            await repository.add_dataset_entry(trace_id, entry_type="golden")
            return

        # Golden candidato: system OK, sin señal del usuario
        # Se guarda como golden pero con metadata indicando que no fue confirmado
        # El /eval skill puede pedir confirmación humana después
        if all_system_high and not user_scores:
            await repository.add_dataset_entry(
                trace_id, entry_type="golden",
                metadata={"confirmed": False},
            )
    except Exception:
        logger.warning("Dataset curation failed for trace %s", trace_id, exc_info=True)
```

#### 4.2 Curación manual via `/eval` skill (Fase 6)

El agente puede revisar trazas y proponerlas como dataset entries.

#### 4.3 Correction pairs

Cuando se detecta una corrección del usuario (Capa 1), la traza anterior + la corrección forman un par:

```python
await repository.add_dataset_entry(
    trace_id=previous_trace_id,
    entry_type="correction",
    expected_output=user_correction_text,
)
```

### Uso del dataset

#### 4.4 Regression testing

```python
# tests/eval/test_regression.py
async def test_dataset_regression():
    """Run current model against all golden examples."""
    entries = await repository.get_dataset_entries(entry_type="golden")
    for entry in entries:
        result = await run_pipeline(entry.input_text)
        # Evaluar con las mismas métricas
        assert evaluate(result, entry.expected_output) > 0.7
```

#### 4.5 Few-shot injection (futuro, Fase 5)

Los mejores 3-5 golden examples por categoría se inyectan dinámicamente en `_build_context()`.

### Archivos nuevos

```
app/eval/
  __init__.py
  dataset.py       # Curación automática + manual
  exporter.py      # Export a JSON/JSONL para DeepEval
```

---

## Fase 5: Auto-Evolución de Prompts

### Objetivo
El sistema propone cambios al system prompt o few-shot examples, los evalúa contra el dataset, y un humano aprueba o rechaza.

### Nivel 1 — Reflexión en contexto

Ya existe parcialmente con el sistema de memorias. Se extiende con "memorias de falla".

> **Error en la v1**: `memory_file.add_memory()` no existe. `MemoryFile` solo tiene
> `sync(memories_list)`. Para agregar una memoria se necesita:
> 1. `repository.save_memory(content, category)` → inserta en DB
> 2. `memory_file.sync(await repository.list_memories())` → actualiza MEMORY.md
> 3. Opcionalmente embed (best-effort)
>
> Esto es exactamente el mismo patrón que usa `/remember` en `builtins.py`.

```python
# Después de un guardrail failure, en _handle_message (background task):
if settings.guardrails_enabled and not guardrail_report.passed:
    failed_checks = [r.check_name for r in guardrail_report.results if not r.passed]
    failure_note = (
        f"[auto-corrección] Al responder '{user_text[:50]}...', "
        f"los guardrails detectaron: {', '.join(failed_checks)}. "
        f"Recordar evitar este tipo de error."
    )
    # Usar el mismo patrón que /remember:
    try:
        await repository.save_memory(failure_note, category="self_correction")
        all_memories = await repository.list_memories()
        await memory_file.sync(all_memories)
        # Embed (best-effort)
        if vec_available and settings.semantic_search_enabled:
            from app.embeddings.indexer import embed_memory
            mem = await repository.get_latest_memory()  # necesita nuevo method
            if mem:
                await embed_memory(
                    mem.id, mem.content, repository,
                    ollama_client, settings.embedding_model,
                )
    except Exception:
        logger.warning("Failed to save self-correction memory", exc_info=True)
```

Estas memorias de auto-corrección se inyectan en el contexto del LLM automáticamente
(ya pasan por `_get_memories()` en Phase B) permitiendo que el agente "aprenda" de
sus errores sin cambiar el system prompt.

> **Limpieza**: Las memorias de self_correction deberían tener un TTL. Después de 30 días
> sin reincidencia, el consolidador puede eliminarlas. Agregar lógica al
> `consolidate_memories()` existente para que considere la edad de las self_correction.

### Nivel 2 — Evolución de prompts (MIPRO-lite)

#### 5.1 Prompt registry

```sql
CREATE TABLE IF NOT EXISTS prompt_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_name TEXT NOT NULL,      -- "system_prompt", "router_prompt", "summarizer_prompt"
    version     INTEGER NOT NULL,
    content     TEXT NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 0,
    scores      TEXT NOT NULL DEFAULT '{}',  -- JSON: métricas agregadas del dataset
    created_by  TEXT NOT NULL DEFAULT 'human',  -- "human" | "agent"
    approved_at TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_prompt_version ON prompt_versions(prompt_name, version);
```

> **Constraint de integridad**: Solo puede haber UN `is_active=1` por `prompt_name`.
> SQLite no soporta partial unique indexes, así que esto se enforce a nivel aplicación:
> `activate_prompt_version()` desactiva el anterior en una transacción.

#### 5.2 Integración con el pipeline existente

**Problema**: Actualmente `_handle_message` lee `settings.system_prompt` (línea 499 de router.py),
que es un string estático desde `config.py`. Para que prompt versioning funcione, hay que
cambiar la fuente del system prompt.

**Solución**: Un cache en memoria que se refresca cuando se activa una nueva versión:

```python
# app/eval/prompt_manager.py
_active_prompts: dict[str, str] = {}  # prompt_name -> content

async def get_active_prompt(prompt_name: str, repository, default: str) -> str:
    """Get active prompt, with in-memory cache. Falls back to default from config."""
    if prompt_name not in _active_prompts:
        row = await repository.get_active_prompt_version(prompt_name)
        _active_prompts[prompt_name] = row["content"] if row else default
    return _active_prompts[prompt_name]

def invalidate_prompt_cache(prompt_name: str | None = None) -> None:
    """Called when a new version is activated."""
    if prompt_name:
        _active_prompts.pop(prompt_name, None)
    else:
        _active_prompts.clear()
```

En `_handle_message`:

```python
# Línea ~499 actual:
#   system_prompt_with_date = build_system_prompt(settings.system_prompt, ...)
# Cambia a:
from app.eval.prompt_manager import get_active_prompt
base_prompt = await get_active_prompt("system_prompt", repository, settings.system_prompt)
system_prompt_with_date = build_system_prompt(base_prompt, profile_row["data"], current_date)
```

#### 5.3 Flujo de evolución

1. **Diagnóstico**: El `/eval` skill identifica un patrón de falla recurrente (ej: "3 de 5 respuestas sobre clima fueron en inglés")
2. **Propuesta**: El agente propone un cambio al prompt
3. **Evaluación offline**: Se corre el dataset contra la versión propuesta usando `run_single_evaluation()`
4. **Comparación**: Se presentan scores (versión actual vs propuesta) al humano
5. **Aprobación**: Si el humano aprueba, la nueva versión se activa

```python
# app/eval/evolution.py
async def propose_prompt_change(
    prompt_name: str,
    diagnosis: str,
    proposed_change: str,
    ollama_client: OllamaClient,
    repository,
) -> dict:
    """Generate a prompt modification proposal. Does NOT activate it."""
    current_row = await repository.get_active_prompt_version(prompt_name)
    current_content = current_row["content"] if current_row else ""

    # Generar nueva versión
    new_content = await ollama_client.chat([
        ChatMessage(role="system", content=(
            "You are a prompt engineer. You modify system prompts to fix specific issues. "
            "Make minimal, targeted changes. Output ONLY the complete new prompt text."
        )),
        ChatMessage(role="user", content=(
            f"Current prompt:\n{current_content}\n\n"
            f"Problem identified: {diagnosis}\n"
            f"Proposed change: {proposed_change}\n\n"
            f"Generate the modified prompt."
        )),
    ])

    # Guardar como draft (is_active=0)
    current_version = current_row["version"] if current_row else 0
    version = await repository.save_prompt_version(
        prompt_name=prompt_name,
        version=current_version + 1,
        content=new_content,
        created_by="agent",
    )

    return {"version": version, "content": new_content, "prompt_name": prompt_name}
```

#### 5.4 Aprobación via WhatsApp

Comando `/approve-prompt <name> <version>`:

```python
async def cmd_approve_prompt(args: str, ctx: CommandContext) -> str:
    """Activate a proposed prompt version."""
    parts = args.strip().split()
    if len(parts) != 2:
        return "Uso: /approve-prompt <nombre> <versión>\nEjemplo: /approve-prompt system_prompt 3"

    prompt_name, version_str = parts
    try:
        version = int(version_str)
    except ValueError:
        return "La versión debe ser un número."

    # Verificar que existe y no está ya activa
    row = await ctx.repository.get_prompt_version(prompt_name, version)
    if not row:
        return f"No encontré la versión {version} del prompt '{prompt_name}'."
    if row["is_active"]:
        return f"Esa versión ya está activa."

    # Transacción: desactivar anterior, activar nueva
    await ctx.repository.activate_prompt_version(prompt_name, version)

    # Invalidar cache
    from app.eval.prompt_manager import invalidate_prompt_cache
    invalidate_prompt_cache(prompt_name)

    return f"Prompt '{prompt_name}' v{version} activado."
```

---

## Fase 6: Self-Evaluation Skill

### Objetivo
Un skill `/eval` que permite al agente diagnosticar sus propias fallas, proponer correcciones, y alimentar el dataset — todo via WhatsApp.

### SKILL.md

```yaml
---
name: eval
description: Self-evaluation and continuous improvement tools
version: 1
tools:
  - get_eval_summary
  - list_recent_failures
  - diagnose_trace
  - propose_correction
  - add_to_dataset
  - get_dataset_stats
  - run_quick_eval
---
Use these tools to analyze your own performance and improve over time.
- Use get_eval_summary() for an overview of recent performance metrics.
- Use list_recent_failures() to see traces where guardrails or users flagged issues.
- Use diagnose_trace(trace_id) to deep-dive into a specific interaction.
- Use propose_correction(trace_id, correction) to suggest what you should have said.
- Use add_to_dataset(trace_id, type) to curate traces into the eval dataset.
- Use get_dataset_stats() to see dataset composition and coverage.
- Use run_quick_eval(category) to evaluate yourself against the dataset for a category.
```

### Implementación de tools

```python
# app/skills/tools/eval_tools.py

async def get_eval_summary(days: int = 7) -> str:
    """Resumen de métricas de las últimas N días."""
    # Queries sobre trace_scores agrupados por nombre
    # Retorna: total traces, avg scores, top failures, trends

async def list_recent_failures(limit: int = 10) -> str:
    """Lista trazas con scores bajos o feedback negativo."""
    # WHERE value < 0.5 ORDER BY created_at DESC

async def diagnose_trace(trace_id: str) -> str:
    """Deep dive en una traza: spans, scores, input/output completo."""
    # Reconstruir el árbol de spans con sus resultados

async def propose_correction(trace_id: str, correction: str) -> str:
    """Proponer qué debería haber respondido el agente."""
    # Guardar como correction pair en el dataset

async def add_to_dataset(trace_id: str, entry_type: str = "failure") -> str:
    """Curar manualmente una traza al dataset."""

async def get_dataset_stats() -> str:
    """Composición del dataset: golden/failure/correction por categoría."""

async def run_quick_eval(category: str = "all") -> str:
    """Correr evaluación contra el dataset para una categoría."""
    # Ejecutar pipeline sobre N entries, retornar scores agregados
```

### Tool category para el router

> **Cambio crítico**: Agregar la categoría a `TOOL_CATEGORIES` NO es suficiente.
> El `CLASSIFIER_PROMPT` en `app/skills/router.py:52-57` tiene una lista hardcodeada
> de categorías. Si "evaluation" no aparece ahí, `classify_intent()` nunca lo retornará.

Cambios requeridos en `app/skills/router.py`:

```python
# 1. Agregar categoría al dict (línea ~48):
TOOL_CATEGORIES["evaluation"] = [
    "get_eval_summary", "list_recent_failures", "diagnose_trace",
    "propose_correction", "add_to_dataset", "get_dataset_stats",
    "run_quick_eval",
]

# 2. Actualizar CLASSIFIER_PROMPT (línea ~52) para incluir "evaluation":
CLASSIFIER_PROMPT = (
    "Classify this message into tool categories. "
    "Reply with ONLY category names separated by commas, or \"none\".\n"
    "Categories: time, math, weather, search, news, notes, files, memory, "
    "github, tools, selfcode, expand, projects, evaluation, none\n\n"
    #                                                ^^^^^^^^^^
    "Message: {user_message}"
)
```

> **Mejor aún**: Generar la lista de categorías dinámicamente desde `TOOL_CATEGORIES.keys()`
> para que no se desincronice al agregar categorías:
> ```python
> _CATEGORIES_LIST = ", ".join(sorted(TOOL_CATEGORIES.keys())) + ", none"
> CLASSIFIER_PROMPT = f"... Categories: {_CATEGORIES_LIST}\n\nMessage: {{user_message}}"
> ```
> Esto requiere que `TOOL_CATEGORIES` esté poblado ANTES de que se defina el prompt,
> lo cual ya es el caso dado el orden del archivo.

### Patrón de registración

Siguiendo el patrón de `selfcode_tools.py` y `expand_tools.py`, el skill necesita
una función `register()` que reciba las dependencias y registre todos los tools:

```python
# app/skills/tools/eval_tools.py

def register(
    registry: SkillRegistry,
    repository: Repository,
    ollama_client: OllamaClient | None = None,
) -> None:
    """Register evaluation tools. Requires tracing tables to exist in DB."""

    async def get_eval_summary(days: int = 7) -> str:
        # ... implementation using repository ...

    async def list_recent_failures(limit: int = 10) -> str:
        # ... implementation using repository ...

    # ... rest of tools ...

    registry.register_tool(
        name="get_eval_summary",
        description="Get summary of agent performance metrics for the last N days",
        parameters={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Number of days to summarize (default 7)"},
            },
        },
        handler=get_eval_summary,
        skill_name="eval",
    )
    # ... register rest of tools ...
```

Y en `app/skills/tools/__init__.py`, agregar al final de `register_builtin_tools()`:

```python
# Eval tools (requires tracing to be enabled)
if settings is not None and settings.tracing_enabled:
    from app.skills.tools import eval_tools
    eval_tools.register(registry, repository, ollama_client=ollama_client)
```

### Precaución sobre `run_quick_eval`

> **Recursión potencial**: Si `run_quick_eval()` ejecuta el pipeline completo del LLM
> (con tool calling), y ese pipeline usa tools, se crea un tool loop dentro de otro
> tool loop. Esto NO es soportado por la arquitectura actual.
>
> **Solución**: `run_quick_eval` usa `ollama_client.chat()` directo (sin tools),
> comparando la respuesta del LLM plano contra el expected_output del dataset.
> No pasa por `execute_tool_loop`.

### Flujo de uso

El usuario escribe en WhatsApp:
```
"Revisá cómo te fue esta semana"
→ classify_intent → ["evaluation"]
→ select_tools → [get_eval_summary]
→ LLM llama get_eval_summary(days=7)
→ Resultado: "155 interacciones, avg helpfulness 0.82, 3 language failures..."
→ LLM responde: "Esta semana tuve 155 interacciones con un score promedio de 0.82..."
```

```
"Mostrá los fallos recientes"
→ list_recent_failures(limit=5)
→ LLM: "Encontré 5 problemas: ..."
→ Usuario: "El tercero fue un error mío, no tuyo. Agregalo como golden"
→ add_to_dataset(trace_id="...", entry_type="golden")
```

---

## Orden de Implementación Detallado

### Iteración 0 (prerequisitos)
0. **`WhatsAppClient` retorna `wa_message_id`**: Cambiar `send_message` / `_send_single_message` para capturar y retornar el message ID de la Graph API. **Sin esto, las Fases 2 y 3 no pueden vincular trazas a mensajes WA.**
0. **`router.py` guarda `wa_message_id` del reply**: Después de `wa_client.send_message(...)`, guardar el ID retornado para pasarlo al trace.

### Iteración 1 (fundacional)
1. **Guardrails determinísticos**: `not_empty`, `language_match` (con umbral de 30 chars), `no_pii`, `excessive_length`, `no_raw_tool_json`
2. **Schema de trazas**: Crear tablas (traces, trace_spans, trace_scores) en `db.py`
3. **TraceContext + TraceRecorder**: Implementar con `contextvars` para propagación
4. **Instrumentar `_handle_message`**: Wrapping del flujo normal con `async with TraceContext(...)`
5. **Scores de guardrails → traza**: Conectar Fase 1 con Fase 2
6. **Repository methods**: `save_trace_score()`, `get_latest_trace_id()`, `get_trace_id_by_wa_message_id()`

### Iteración 2 (señales de usuario)
7. **Parsear reacciones WA**: `extract_reactions()` en `parser.py` + handler separado en `router.py`
8. **Comandos `/feedback` y `/rate`**: Con sentiment analysis para feedback
9. **Detección de correcciones**: Patrones estrictos, score 0.3 (sospecha)
10. **Detección de preguntas repetidas**: Via embedding similarity

### Iteración 3 (dataset)
11. **Schema del dataset**: `eval_dataset` + `eval_dataset_tags` tables
12. **Repository methods**: `add_dataset_entry()`, `get_dataset_entries()`, `get_trace_scores()`
13. **Curación automática**: Background task post-trace (3-tier: golden confirmado/candidato/failure)
14. **Export a JSONL** para tests offline

### Iteración 4 (eval skill + offline eval)
15. **SKILL.md** en `skills/eval/SKILL.md`
16. **`eval_tools.py`**: `register()` con repository + ollama_client
17. **Tool category**: En `TOOL_CATEGORIES` + actualizar `CLASSIFIER_PROMPT`
18. **Evaluadores locales**: Métricas custom con Ollama (sin DeepEval externo)
19. **`run_single_evaluation()` helper** para tests offline
20. **Tests**: Del skill y de las métricas

### Iteración 5 (auto-evolución)
21. **Memorias de auto-corrección**: Guardrail failures → repository.save_memory() → sync
22. **Prompt versioning**: Schema `prompt_versions` + repository methods
23. **Prompt manager**: Cache en memoria + `get_active_prompt()` + integración en router.py
24. **Flujo de propuesta + evaluación**
25. **Comando `/approve-prompt`**

### Iteración 6 (maduración)
26. **LLM guardrails**: `tool_coherence`, `hallucination_check` — ahora medibles via trazas
27. **Instrumentar `execute_tool_loop`**: Spans detallados para cada tool call
28. **Limpieza de trazas**: Job en APScheduler para purgar trazas > 90 días
29. **CLASSIFIER_PROMPT dinámico**: Generar lista de categorías desde `TOOL_CATEGORIES.keys()`
30. **Dashboard queries**: Funciones de consulta para el eval skill

---

## Consideraciones Técnicas

### Performance
- Los guardrails determinísticos agregan <50ms al pipeline
- El `TraceRecorder` persiste en background — NO bloquea el envío de la respuesta
- Los LLM guardrails (Iteración 6) se corren en paralelo con un timeout de 500ms
- El dataset se cura en background tasks (`_track_task`)
- El `CLASSIFIER_PROMPT` dinámico se computa una sola vez al import time (module level)

### Compatibilidad
- Todas las tablas nuevas usan `CREATE TABLE IF NOT EXISTS` — safe para DBs existentes
- Las features son feature-flaggeadas en `config.py`
- El sistema funciona sin tracing habilitado (graceful degradation)
- No se modifica el schema existente — solo se agrega
- `WhatsAppClient.send_message` cambia de `-> None` a `-> str | None` — los callers existentes que ignoran el return value no se rompen

### SQLite
- Las trazas pueden crecer rápido (~2KB por trace + spans). A 100 mensajes/día = ~6MB/mes
- **Limpieza periódica**: APScheduler job diario que purga trazas > 90 días. El proyecto ya tiene APScheduler inicializado en `main.py:107-113`
- Índices diseñados para queries del eval skill (filtrar por status, phone, fecha, score name)
- `trace_scores` con `source='system'` se escriben al final de cada traza en un solo batch

### contextvars y concurrencia
- `contextvars.ContextVar` es safe para uso con `asyncio` — cada Task hereda una copia del context
- `asyncio.create_task()` copia el context automáticamente, así `classify_intent` (que corre como Task) tiene acceso al trace
- `asyncio.gather()` también propaga el context a cada coroutine
- El `TraceRecorder` usa la misma conexión SQLite que el rest de la app — NO abrir conexiones nuevas

### Testing
- Tests de guardrails no requieren LLM (determinísticos)
- Tests de tracing mockean el `TraceRecorder`
- Tests de eval usan fixtures con dataset pre-poblado
- Tests de evaluación offline requieren Ollama: `pytest tests/eval/ -m eval`
- Tests regulares (`pytest tests/ -v`) NO corren los eval tests por defecto

### Dependencias nuevas (totales)

```toml
# pyproject.toml
dependencies = [
    # ... existentes ...
    "langdetect>=1.0.9",  # Fase 1: detección de idioma (puro Python, ~100KB)
]

[project.optional-dependencies]
dev = [
    # ... existentes ...
    "deepeval>=1.0",  # Solo si se usa Opción A para evaluación offline
]
```

> `langdetect` es la única dependencia nueva obligatoria. `deepeval` es opcional.

---

## Apéndice A: Nuevos Repository Methods Requeridos

Resumen de todos los métodos que hay que agregar a `app/database/repository.py`:

### Tracing (Fase 2)
```python
async def save_trace(self, trace_id, phone_number, input_text, message_type) -> None
async def finish_trace(self, trace_id, status, output_text, wa_message_id) -> None
async def save_trace_span(self, span_id, trace_id, name, kind, parent_id) -> None
async def finish_trace_span(self, span_id, status, latency_ms, input_data, output_data, metadata) -> None
async def save_trace_score(self, trace_id, name, value, source, comment, span_id) -> None
async def get_latest_trace_id(self, phone_number) -> str | None
async def get_trace_id_by_wa_message_id(self, wa_message_id) -> str | None
async def get_trace_scores(self, trace_id) -> list[dict]
async def get_trace_with_spans(self, trace_id) -> dict | None  # Para diagnose_trace
```

### Evaluación (Fase 3)
```python
async def get_recent_user_embeddings(self, conv_id, hours=24) -> list[list[float]]  # preguntas repetidas
```

### Dataset (Fase 4)
```python
async def add_dataset_entry(self, trace_id, entry_type, expected_output=None, metadata=None) -> int
async def get_dataset_entries(self, entry_type=None, tag=None, limit=100) -> list[dict]
async def add_dataset_tags(self, dataset_id, tags: list[str]) -> None
async def get_dataset_stats(self) -> dict  # counts por entry_type y tag
```

### Prompt Versioning (Fase 5)
```python
async def save_prompt_version(self, prompt_name, version, content, created_by) -> int
async def get_active_prompt_version(self, prompt_name) -> dict | None
async def get_prompt_version(self, prompt_name, version) -> dict | None
async def activate_prompt_version(self, prompt_name, version) -> None  # transacción
```

### Eval Skill (Fase 6)
```python
async def get_eval_summary(self, days=7) -> dict  # agregaciones de scores
async def get_failed_traces(self, limit=10) -> list[dict]  # trazas con score < 0.5
async def cleanup_old_traces(self, days=90) -> int  # retorna count eliminadas
```

---

## Apéndice B: Archivos Nuevos (Resumen)

```
app/
  guardrails/
    __init__.py
    models.py        # GuardrailResult, GuardrailReport
    checks.py        # check_not_empty, check_language_match, check_no_pii, redact_pii
    pipeline.py      # run_guardrails()
  tracing/
    __init__.py
    context.py       # TraceContext, SpanData, get_current_trace() (contextvars)
    recorder.py      # TraceRecorder (async SQLite persistence, best-effort)
  eval/
    __init__.py
    dataset.py       # maybe_curate_to_dataset(), correction pair logic
    exporter.py      # export_to_jsonl() para tests offline
    evolution.py     # propose_prompt_change()
    prompt_manager.py # get_active_prompt(), invalidate_prompt_cache()
  skills/tools/
    eval_tools.py    # register() — eval skill tools
skills/
  eval/
    SKILL.md         # Skill metadata
tests/
  guardrails/
    test_checks.py
    test_pipeline.py
  eval/
    conftest.py
    metrics.py       # Evaluadores custom con Ollama
    helpers.py       # run_single_evaluation()
    test_language.py
    test_tool_use.py
    test_regression.py
```

---

## Apéndice C: Config Settings Nuevas

```python
# app/config.py — agregar a Settings:

# Guardrails (Fase 1)
guardrails_enabled: bool = True
guardrails_language_check: bool = True
guardrails_pii_check: bool = True
guardrails_llm_checks: bool = False  # Activar en Iteración 6

# Tracing (Fase 2)
tracing_enabled: bool = True
tracing_sample_rate: float = 1.0  # 1.0 = trace everything
trace_retention_days: int = 90    # Para cleanup job

# Evaluation (Fase 3+)
eval_auto_curate: bool = True     # Curación automática al dataset
```

---

## Apéndice D: Errata de la v1

Listado de errores detectados en la revisión y sus correcciones:

| # | Error en v1 | Corrección |
|---|-------------|-----------|
| 1 | `pre_classified != ["none"]` no maneja `None` | Reemplazado por `tools_were_used` boolean explícito |
| 2 | `memory_file.add_memory()` no existe | Corregido: usa `repository.save_memory()` + `memory_file.sync()` |
| 3 | `max_length` check de 4096 chars | Cambiado a `excessive_length` (8000), `split_message()` ya maneja chunks |
| 4 | `langdetect` sin umbral de longitud | Agregado: solo aplica si input y output >= 30 chars |
| 5 | Sin recursion guard en `_handle_guardrail_failure` | Ahora es single-shot, sin re-check |
| 6 | `classify_intent` como `asyncio.Task` incompatible con `async with span` | Instrumentar dentro de `classify_intent()` via contextvar |
| 7 | `execute_tool_loop` no recibe trace context | Resuelto con `contextvars` — no cambia firma |
| 8 | Traces no tienen `wa_message_id` → reacciones no se vinculan | Agregado `wa_message_id` al schema + cambios en `WhatsAppClient` |
| 9 | `trace_spans.status` sin CHECK constraint | Agregado |
| 10 | Reactions no se parsean → no entran al webhook | Agregado `extract_reactions()` + handler separado |
| 11 | `CLASSIFIER_PROMPT` hardcodeado no incluye "evaluation" | Documentado + solución de prompt dinámico |
| 12 | `CORRECTION_PATTERNS` con `r"^no[,.]?\s"` demasiado amplio | Dividido en high/low confidence con scores distintos |
| 13 | DeepEval requiere OpenAI API key | Documentado + alternativa con evaluador Ollama local |
| 14 | `/feedback` guarda `value=0.5` siempre | Ahora usa sentiment analysis via Ollama |
| 15 | `idx_dataset_tags` sobre JSON column inútil | Tags movidos a tabla join separada |
| 16 | Golden curation requiere positive user signal (rara) | 3-tier: confirmado/candidato/failure |
| 17 | `run_quick_eval` podría causar tool loop recursivo | Documentado: usa `ollama_client.chat()` directo, sin tools |
| 18 | `prompt_versions.is_active` sin constraint de unicidad | Enforced a nivel aplicación (documentado) |
| 19 | Sin plan de cómo `_handle_message` lee prompts versionados | Agregado `prompt_manager.py` con cache + integración |
| 20 | Eval tools sin patrón de `register()` | Documentado siguiendo patrón de selfcode/expand |
