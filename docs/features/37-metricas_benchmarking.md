# Feature: Métricas y Benchmarking del Pipeline LLM

> **Versión**: v1.0
> **Fecha**: 2026-03-06
> **Estado**: Implementado — gaps 1-3, 5-6 resueltos en Plan 38; gap 4 descartado (baja prioridad)

---

## Por qué medir en un sistema LLM es diferente

En software tradicional, un bug es determinista: la misma entrada siempre produce el mismo error.
En un sistema LLM, la falla es probabilística: el mismo mensaje puede generar una respuesta buena
el 90% de las veces y una respuesta incorrecta el 10%. Esto hace que los tests unitarios clásicos
sean necesarios pero insuficientes.

El problema tiene tres dimensiones que hay que medir por separado:

**1. ¿El contexto que le damos al LLM es bueno?**
Un LLM solo puede razonar sobre lo que tiene en su ventana de tokens. Si le damos memorias
irrelevantes, historial truncado o notas que no aplican, la respuesta será mala aunque el modelo
sea excelente. Esta es la dimensión de *context quality*.

**2. ¿La respuesta que genera cumple los invariantes del sistema?**
Idioma correcto, sin datos sensibles, sin JSON crudo de tools, sin alucinaciones obvias.
Esta es la dimensión de *output quality* medida por guardrails.

**3. ¿La respuesta es correcta desde el punto de vista del usuario?**
Esto solo se puede saber con señales de usuario (feedback positivo/negativo) o con un juez
externo (LLM-as-judge). Esta es la dimensión de *task accuracy*.

Las tres dimensiones requieren mecanismos distintos. A continuación se describe cómo LocalForge
aborda cada una.

---

## Arquitectura general del stack de métricas

```
[Mensaje entra]
      |
      v
[ConversationContext.build()]    <-- Dimension 1: context quality
  |-- token_estimator.py            Token budget por mensaje (chars/4 proxy)
  |-- semantic search hit/miss       ¿Las memorias recuperadas son relevantes?
      |
      v
[execute_tool_loop / chat()]    <-- LLM razona sobre el contexto
      |
      v
[Guardrail Pipeline]            <-- Dimension 2: output quality
  |-- not_empty
  |-- language_match
  |-- no_pii
  |-- excessive_length
  |-- no_raw_tool_json
      |
      v
[TraceContext / TraceRecorder]  <-- Persistencia de toda la cadena
  |-- SQLite: traces, trace_spans, trace_scores
      |
      v
[maybe_curate_to_dataset()]     <-- Dimension 3: task accuracy
  |-- 3-tier: failure / golden / correction
  |-- LLM-as-judge en eval offline
```

---

## Capa 1: Token Budget

### El problema

qwen3:8b tiene una ventana de contexto de ~32K tokens. El sistema le envía: system prompt +
memorias + daily logs + notas relevantes + resumen de conversacion + historial reciente +
proyectos activos. Si todo esto supera el límite, el modelo trunca silenciosamente o degrada.

### Implementación

**Archivo:** `app/context/token_estimator.py`

El estimador usa `chars / 4` como proxy de tokens (±20% para BPE tokenizers como el de qwen3).
Es una aproximación, no un conteo exacto, y eso es intencional: un conteo exacto requeriría
ejecutar el tokenizador del modelo, lo cual añade latencia sin beneficio proporcional en este
rango de tamaño.

```python
def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)
```

El log emite en tres niveles:
- `INFO` si el contexto está bajo el 80% del límite (normal)
- `WARNING` si está entre 80% y 100% (near_limit)
- `ERROR` si supera el 100% (exceeded)

**Dónde se llama:** `_build_context()` en `webhook/router.py` — después de construir el system
message completo con `ContextBuilder`, antes de enviarlo al LLM.

### Gap actual: sin desglose por sección

El estimador hoy loguea el total. No dice qué porcentaje viene de memorias, qué porcentaje
del historial, qué porcentaje de daily logs. Cuando el contexto está al 90%, no sabés qué
recortar. Este es el gap de métricas más accionable.

---

## Capa 2: Guardrails como señal de calidad

### El problema

Un LLM puede generar respuestas sintácticamente correctas que violen invariantes del sistema:
responder en inglés a un usuario que escribe en español, incluir un número de teléfono de la
base de datos, devolver un objeto JSON crudo que el usuario no puede interpretar.

Los guardrails son checks determinísticos (sin LLM) que se ejecutan sobre cada respuesta
antes de enviarla. Si alguno falla, se intenta una remediation de un solo intento.

### Pipeline

**Archivo:** `app/guardrails/pipeline.py`

```
run_guardrails(user_text, reply, settings)
  ├── check_not_empty(reply)
  ├── check_excessive_length(reply, max=8000)
  ├── check_no_raw_tool_json(reply)
  ├── check_language_match(user_text, reply)   <- solo si len(reply) >= 30
  └── check_no_pii(reply)
```

Cada check retorna `GuardrailResult(passed: bool, check_name: str, latency_ms: float)`.
El pipeline es **fail-open**: si un check levanta una excepción (ej. `langdetect` falla),
se lo trata como "passed". La alternativa (fail-closed) bloquearía todas las respuestas ante
cualquier bug en los checks, lo cual es peor que dejar pasar una respuesta potencialmente mala.

### Por qué `langdetect` tiene umbral de 30 chars

```python
if len(reply) < 30:
    return GuardrailResult(passed=True, ...)
```

`langdetect` usa n-gramas de caracteres. Con textos cortos ("Sí", "Ok", "Claro"), la distribución
estadística es insuficiente para detectar el idioma con confianza. En pruebas con qwen3:8b,
respuestas cortas generaban falsos positivos de `language_match` en ~30% de los casos. El umbral
de 30 chars elimina esos falsos positivos sin sacrificar la detección en respuestas reales.

### Scores como señal cuantitativa

Cada guardrail genera un **trace score**: `value=1.0` si pasó, `value=0.0` si falló.
Esto convierte los checks binarios en una serie temporal consultable:

```sql
SELECT name, AVG(value) as pass_rate, COUNT(*) as n
FROM trace_scores
WHERE source = 'system'
GROUP BY name
ORDER BY pass_rate ASC;
```

Si `language_match` tiene `pass_rate=0.85`, significa que el 15% de las respuestas salieron
en el idioma incorrecto. Eso es una señal accionable para ajustar el system prompt.

---

## Capa 3: Dataset vivo y Task Accuracy

### El problema

Los guardrails miden invariantes del sistema, no corrección de contenido. Si el usuario pregunta
"¿cuál es la capital de Francia?" y el modelo responde "Madrid", todos los guardrails pasan.
Para medir corrección real se necesita una referencia de "respuesta esperada".

### Dataset 3-tier

**Archivo:** `app/eval/dataset.py`

Cada interacción completada pasa por `maybe_curate_to_dataset()`, que la clasifica:

| Tier | Condición | Uso |
|------|-----------|-----|
| `failure` | Cualquier guardrail < 0.3 O usuario negativo | Análisis de fallos, regresiones |
| `golden` (confirmado) | Todos los guardrails >= 0.8 Y usuario positivo | Ground truth confiable |
| `golden` (candidato) | Todos guardrails altos, sin señal de usuario | Ground truth potencial, a validar |
| `correction` | Usuario corrigió explícitamente | Par (bad_output, expected_output) para training |

Los pares de corrección (`correction`) son el insumo más valioso: tienen tanto la respuesta
incorrecta como la respuesta esperada. Son el dataset ideal para LLM-as-judge.

### Tags para filtrado causal

Los entries de tipo `failure` se etiquetan automáticamente con el check que falló:
`guardrail:language_match`, `guardrail:no_pii`, etc. Esto permite consultas causales:

```sql
SELECT COUNT(*) FROM eval_dataset_tags WHERE tag = 'guardrail:language_match';
```

Si este número crece, significa que el LLM está ignorando las instrucciones de idioma con más
frecuencia, y es momento de revisar el system prompt o el prompt de remediation.

---

## Benchmarking: LLM-as-judge

### Por qué no word overlap ni embeddings

La métrica clásica para evaluar respuestas de texto es BLEU o similitud de embeddings.
Ambas son semánticamente inválidas para respuestas conversacionales:

- BLEU mide solapamiento de n-gramas. "La capital es París" y "París es la capital de Francia"
  tienen BLEU bajo aunque son equivalentes.
- Similitud de embeddings captura cercanía semántica pero no corrección factual.

La alternativa es usar un LLM como juez binario (yes/no). Este es el mismo enfoque que usan
OpenAI Evals, Anthropic's Constitutional AI, y la mayoría de los frameworks modernos de eval.

### Implementación

**Prompt del juez** (compartido entre `eval_tools.py` y `scripts/run_eval.py`):

```
Question: {input_text[:300]}
Expected answer: {expected[:300]}
Actual answer: {actual[:300]}

Does the actual answer correctly and completely answer the question?
Reply ONLY 'yes' or 'no'.
```

El prompt es binario y usa `think=False` para suprimir el chain-of-thought de qwen3. El
razonamiento extendido (think mode) hace que el modelo a veces contradiga su propia conclusión
en la respuesta final, lo cual rompe el parseo del "yes"/"no".

### Dos modos de uso

**Online (desde WhatsApp):** `run_quick_eval` en `eval_tools.py`
```
"evaluá mis últimos 5 pares de corrección"
→ Correct: 4/5 (80%)
  - entry #12: ✅
  - entry #13: ❌
  - ...
```

**Offline (desde terminal):** `scripts/run_eval.py`
```bash
python scripts/run_eval.py --entry-type correction --limit 20 --threshold 0.7
# Exit 0 si accuracy >= 0.7, exit 1 si no (útil para CI)
```

El script offline no requiere FastAPI ni el servidor completo. Solo necesita la DB SQLite
y acceso a Ollama. Esto permite correrlo en CI antes de un deploy.

### Por qué el juez es el mismo modelo (qwen3:8b)

Idealmente el juez sería un modelo más capaz que el evaluado (ej. usar Claude para evaluar
qwen3). Pero esto introduce dependencias externas, costos y latencia. Para un sistema local-first
como LocalForge, el auto-juicio con el mismo modelo es un compromiso razonable. Las limitaciones:
- El modelo puede ser ciego a sus propios errores sistemáticos
- El pass rate tiende a estar inflado vs un juez independiente

La mitigación es usar pares de corrección donde el `expected_output` lo escribió un humano,
no el modelo.

---

## Trazabilidad estructurada: el hilo conductor

Las tres capas anteriores necesitan un mecanismo de persistencia común. Eso es `TraceContext`.

**Archivo:** `app/tracing/context.py`

```python
async with TraceContext(phone, text, recorder) as trace:
    async with trace.span("phase_a") as span:
        span.set_input({"query": user_text})
        ...
    await trace.add_score("not_empty", 1.0, source="system")
```

### Por qué `contextvars` en lugar de pasar el trace como parámetro

La alternativa sería pasar `trace_ctx` como parámetro a cada función del pipeline:
`execute_tool_loop(messages, tools, trace_ctx=trace_ctx)`. Esto rompe todas las firmas
existentes y hace que cada función nueva tenga que aceptar el parámetro aunque no lo use.

`contextvars.ContextVar` resuelve esto de forma limpia: cualquier coroutine lanzada dentro
del `async with TraceContext(...)` hereda automáticamente el trace, incluyendo las tasks
creadas con `asyncio.create_task()`. No hay cambio de firma.

### Persistencia best-effort

`TraceRecorder` wrappea toda escritura en `try/except`:

```python
async def add_score(self, trace_id, name, value, ...):
    try:
        await self._repository.add_trace_score(...)
    except Exception:
        logger.warning("TraceRecorder: add_score failed", exc_info=True)
```

Si la DB falla (disco lleno, lock de SQLite), el pipeline de mensajes continúa sin interrupción.
Las métricas se pierden, pero el usuario no recibe un error. La alternativa (propagar la
excepción) haría que un problema de persistencia rompa el sistema de mensajería, lo cual
es inaceptable.

---

## Archivos clave

| Archivo | Rol |
|---|---|
| `app/context/token_estimator.py` | Estimación de tokens, log de budget |
| `app/guardrails/checks.py` | 5 checks determinísticos |
| `app/guardrails/pipeline.py` | Orquestación del pipeline, fail-open |
| `app/tracing/context.py` | `TraceContext` + `SpanData` (contextvars) |
| `app/tracing/recorder.py` | Persistencia async SQLite, best-effort |
| `app/eval/dataset.py` | Curación 3-tier, correction pairs |
| `app/skills/tools/eval_tools.py` | 9 tools: summary, failures, judge, dashboard |
| `scripts/run_eval.py` | Benchmark offline, exit code para CI |
| `app/database/repository.py` | `get_eval_summary`, `get_failure_trend`, `get_score_distribution` |

---

## Decisiones de diseño

| Decisión | Alternativa descartada | Motivo |
|---|---|---|
| `chars/4` como proxy de tokens | Tokenizador real (tiktoken) | Zero dependencias extra; ±20% es suficiente para alertas de presupuesto |
| Guardrails fail-open | Fail-closed | Un bug en un check no debe bloquear el sistema de mensajería |
| LLM-as-judge binario (yes/no) | BLEU / word overlap | Métricas de n-gramas son semánticamente inválidas para conversación |
| `think=False` en el juez | Dejar think activo | El chain-of-thought puede contradecir la conclusión final, rompiendo el parseo |
| SQLite como backend de trazas | Langfuse self-hosted | Zero infra adicional; schema diseñado para ser compatible con migración posterior |
| `contextvars` para propagación | Pasar `trace_ctx` como parámetro | No rompe firmas existentes; asyncio tasks heredan el contexto automáticamente |
| Dataset 3-tier (failure/golden/correction) | Solo golden o solo failure | La distinción permite priorizar: los failures son más valiosos que los candidatos no validados |
| Auto-curation como background task | Síncrona en el pipeline | La curación no es crítica; no debe añadir latencia al path de respuesta |
| Exit code 0/1 en `run_eval.py` | Solo output de texto | Permite integración con CI: el pipeline puede fallar automáticamente si accuracy < threshold |

---

## Gaps actuales y próximos pasos

Estas son las métricas identificadas pero aún no implementadas, en orden de impacto:

### 1. Token budget por sección ✅ Resuelto (Plan 38)
Implementado en `app/context/token_estimator.py` — `estimate_sections()` y
`log_context_budget_breakdown()`. Llamado en `_run_normal_flow()` después de `_build_context()`.
El log `context.budget.breakdown` incluye `system_prompt` vs `history` con el campo
`largest_section` para diagnóstico rápido.

### 2. Latencia p50/p95 por operación ✅ Resuelto (Plan 38)
Implementado en `app/database/repository.py` — `get_latency_percentiles(span_name, days)` y
`_compute_percentiles()`. Expuesto via tool `get_latency_stats` en `eval_tools.py`.
Responde desde WhatsApp: "dame las latencias del pipeline de los últimos 7 días".

### 3. Semantic search hit rate ✅ Resuelto (Plan 38)
`ConversationContext._get_memories_with_threshold()` ahora retorna `(memories, search_stats)`.
El campo `search_stats` trackea `search_mode` (semantic/fallback_threshold/full_fallback),
`memories_retrieved`, `memories_passed`, `memories_returned`. Loguea en `context.search_stats`.
Tool `get_search_stats` en `eval_tools.py` consulta la distribución de modos.

### 4. Tool usage metrics (baja prioridad)
Qué tools se invocan más, cuáles tienen mayor tasa de error, cuáles nunca se usan.
Útil para detectar tools redundantes y para calibrar el router de intención.
**Descartado de Plan 38** — requiere cambios más invasivos en el executor.

### 5. Dashboard HTML offline ✅ Implementado (Plan 38)
`scripts/dashboard.py` — genera HTML autocontenido con Chart.js desde CDN.
Secciones: summary cards, guardrail pass rates, failure trend (Chart.js), latencias p50/p95/p99,
dataset composition, recent failures con links a Langfuse.
Uso: `python scripts/dashboard.py --db data/localforge.db --output reports/dashboard.html`

### 6. Langfuse infrautilizado ✅ Resuelto (Plan 38)
- `session_id=phone_number` en cada traza → agrupa conversaciones en Langfuse Sessions
- `platform` tag en metadata → filtra WhatsApp vs Telegram
- `update_trace_tags()` → tags de categorías de intent (`math`, `time`, etc.) en cada traza
- `sync_dataset_to_langfuse()` → golden/correction entries se sincronizan a Langfuse Datasets

---

## Variables de configuración relevantes

| Variable (`config.py`) | Default | Efecto |
|---|---|---|
| `tracing_enabled` | `True` | Activa/desactiva trazabilidad estructurada |
| `tracing_sample_rate` | `1.0` | Fracción de mensajes trazados (1.0 = todos) |
| `trace_retention_days` | `90` | Días antes de purgar trazas (APScheduler 03:00 UTC) |
| `guardrails_enabled` | `True` | Activa/desactiva pipeline de guardrails |
| `guardrails_language_check` | `True` | Check de idioma con langdetect |
| `guardrails_pii_check` | `True` | Check de PII con regex |
| `guardrails_llm_checks` | `False` | LLM judges adicionales (tool_coherence, hallucination) |
| `guardrails_llm_timeout` | `3.0` | Timeout en segundos para LLM judges |
| `eval_auto_curate` | `True` | Curación automática del dataset después de cada trace |
| `memory_similarity_threshold` | `1.0` | Distancia L2 máxima para memorias semánticas |
| `semantic_search_top_k` | `5` | Máximo de resultados en búsqueda semántica |
