# PRD: Metrics Hardening — Gaps de observabilidad identificados en Plan 37

## 1. Objetivo y Contexto

El documento `docs/features/37-metricas_benchmarking.md` describe el stack de métricas actual
(token budget, guardrails, eval dataset, LLM-as-judge) e identifica cuatro gaps concretos donde
la observabilidad es insuficiente o inexistente.

Este plan cierra los tres gaps de mayor impacto:

| # | Gap | Impacto |
|---|-----|---------|
| 1 | Token budget loguea el total pero no el desglose por sección | No sabés qué recortar cuando el contexto está al 90% |
| 2 | Latencias almacenadas en spans pero sin agregación p50/p95 | No podés identificar el cuello de botella en producción |
| 3 | Semantic search: cero visibilidad de hit rate vs fallback | No sabés si `memory_similarity_threshold=1.0` está bien calibrado |

El gap 4 (tool usage metrics) se descarta de este plan por ser de baja prioridad y requerir
cambios más invasivos en el executor.

---

## 2. Alcance

### In Scope

**Gap 1 — Token breakdown por sección:**
- Agregar `log_context_budget_breakdown()` en `token_estimator.py`
- Llamarlo en `_run_normal_flow()` después de `_build_context()`
- Persistir el breakdown en el span de tracing de la fase LLM para que sea consultable via `diagnose_trace`

**Gap 2 — Latencia p50/p95 por span:**
- Agregar `get_latency_percentiles(span_name, days)` en `repository.py`
- Si `span_name == "all"`, iterar sobre los spans más frecuentes del sistema
- Exponer via nueva tool `get_latency_stats` en `eval_tools.py`

**Gap 3 — Semantic search hit rate:**
- Trackear en `ConversationContext.build()` el modo de recuperación (semantic / fallback_threshold / full_fallback)
- Persistir stats en metadata del span Phase B para que sea consultable
- Agregar `get_search_stats(days)` en `repository.py` + tool en `eval_tools.py`

**Gap 5 — Dashboard HTML offline:**
- Script `scripts/dashboard.py` que genera un HTML autocontenido con tablas + gráficos
- Sin FastAPI, sin deps extra — solo SQLite + stdlib + Chart.js desde CDN
- Secciones: summary cards, guardrail pass rates, failure trend, dataset composition, latencias p95, recent failures con links a Langfuse

**Gap 6 — Langfuse infrautilizado:**
- `session_id` no se envía → Langfuse no puede agrupar conversaciones por usuario
- Tags solo llevan status (`completed`/`failed`) → sin categorías de intento (`math`, `time`, etc.)
- El eval dataset local (golden/failure/correction) no está sincronizado con Langfuse Datasets
- Platform (`whatsapp`/`telegram`) no está en metadata de la traza

### Out of Scope

- Tool usage metrics (qué tools se llaman más, tasas de error por tool) — gap 4
- A/B testing de configuraciones de context (requiere routing)
- Alertas automáticas cuando métricas superan umbrales
- Langfuse Prompt Management sync (el sistema local de versioning ya cubre esto)

---

## 3. Casos de Uso Críticos

### 3.1 Operador ve "context.budget.near_limit" en logs

**Antes:** El log dice `estimated_tokens=27000 (84% of 32000)`. No hay información de qué
sección ocupa qué. El operador no sabe si recortar el historial, las memorias, o los daily logs.

**Después:** El log incluye breakdown estructurado:
```json
{
  "estimated_tokens": 27000,
  "breakdown": {
    "system_prompt": 800,
    "user_memories": 4200,
    "active_projects": 1100,
    "relevant_notes": 3800,
    "recent_activity": 6500,
    "conversation_summary": 2100,
    "history": 8500
  },
  "largest_section": "history"
}
```
El operador puede recortar `recent_activity` (daily logs) o reducir `history_verbatim_count`.

### 3.2 Respuestas lentas — diagnóstico via WhatsApp

**Antes:** "El bot está lento" — no hay forma de saber si el cuello de botella es el embed,
el classify_intent, o el tool loop.

**Después:** Desde WhatsApp:
```
"dame las latencias del pipeline de los últimos 7 días"
→ get_latency_stats(span_name="all", days=7)

Latencias p50/p95/p99 (últimos 7 días):
- classify_intent:    p50=210ms  p95=480ms  p99=820ms  (n=143)
- embed:              p50=95ms   p95=200ms  p99=310ms  (n=143)
- execute_tool_loop:  p50=2100ms p95=4800ms p99=7200ms (n=98)
- guardrails:         p50=12ms   p95=28ms   p99=45ms   (n=143)
```
El cuello de botella es `execute_tool_loop` — problema en el LLM o en las tools, no en el embed.

### 3.4 Dashboard offline para revisión semanal

**Antes:** Para revisar métricas hay que abrir WhatsApp y pedir al bot que ejecute tools una
por una. No hay vista consolidada de todo el sistema.

**Después:**
```bash
python scripts/dashboard.py --days 30 --output reports/week.html
# → Abre reports/week.html en el browser
```
El HTML muestra: 143 trazas, 91.5% pass rate, 12 fallos, latencia p95 4.8s en tool loop,
dataset con 23 goldens / 12 failures / 8 corrections, tendencia diaria de los últimos 30 días.
Cada trace_id es un link clickable a `{langfuse_host}/trace/{trace_id}`.

### 3.5 Agrupación por usuario en Langfuse

**Antes:** En Langfuse, "Sessions" está vacío. Todas las trazas aparecen desconectadas aunque
provengan del mismo usuario. No se puede ver la conversación completa de un usuario en la UI.

**Después:** Cada traza lleva `session_id=phone_number`. En Langfuse → Sessions, se puede
filtrar por número de teléfono y ver el histórico completo de interacciones como una timeline.

### 3.6 Filtrar trazas por intent en Langfuse

**Antes:** En Langfuse solo se puede filtrar por status (completed/failed). Si querés ver
todas las interacciones matemáticas para verificar que la calculadora funciona, no hay filtro.

**Después:** Cada traza lleva tags `["completed", "math", "calculator"]` (status + categorías
resueltas). En Langfuse → Traces → Filter by Tag: `math` muestra solo las interacciones
de cálculo. Tags de platform: `whatsapp` o `telegram`.

### 3.7 Eval dataset visible en Langfuse

**Antes:** El dataset de correcciones/goldens existe en SQLite pero es invisible en Langfuse.
No se puede usar el playground de Langfuse para revisar o anotar manualmente los entries.

**Después:** Cuando `maybe_curate_to_dataset()` guarda un entry de tipo `correction` o `golden`,
también lo pushea a `langfuse.create_dataset_item(dataset_name="localforge-eval", ...)`.
El dataset aparece en Langfuse → Datasets para revisión manual y futura integración con
Langfuse Evals.

### 3.3 Calibrar memory_similarity_threshold

**Antes:** `memory_similarity_threshold=1.0` está en config pero nadie sabe si está generando
buenos resultados o si el 80% de las requests cae al fallback.

**Después:** Desde WhatsApp:
```
"¿qué tal está funcionando la búsqueda semántica?"
→ get_search_stats(days=7)

Búsqueda semántica — últimos 7 días (n=143):
- Modo semantic (pasaron threshold): 89 (62%)
- Modo fallback_threshold (0 pasaron, usé top-3): 31 (22%)
- Modo full_fallback (sin embedding): 23 (16%)
Promedio memorias recuperadas: 3.4 / promedio que pasaron: 1.8
```
Un fallback_threshold de 22% indica que el threshold está demasiado estricto para muchos queries.

---

## 4. Restricciones Arquitectónicas

- **Best-effort en todo**: el breakdown de tokens, los search stats y las métricas de latencia
  son observabilidad, nunca deben bloquear el pipeline de mensajes. Todo va en `try/except`.
- **Sin DB schema changes**: los search stats se almacenan en `metadata_json` del span existente
  (columna ya existe en `trace_spans`). No se agrega ninguna tabla nueva.
- **Fail-open en `get_latency_percentiles`**: si `trace_spans` está vacía o `tracing_enabled=False`,
  las tools deben retornar un mensaje descriptivo en lugar de error.
- **Dashboard usa Chart.js desde CDN**: única dependencia externa, solo para renderizado en browser. El script en sí no la importa en Python — va embebida en el HTML como `<script src="https://cdn.jsdelivr.net/npm/chart.js">`.
- **Langfuse `update_trace_tags` es un upsert**: llamar `langfuse.trace(id=existing_id, tags=[...])` actualiza la traza existente, no crea una nueva. Safe llamarlo mid-flight.
- **Dataset sync solo para correction y golden**: los `failure` entries no se sincronizan a Langfuse Datasets (son demasiado ruidosos sin curación). Solo entries con `expected_output` o `confirmed=True`.
- **`get_latency_percentiles` usa Python para percentiles**: SQLite no tiene `PERCENTILE_DISC`.
  Se hace `SELECT latency_ms ORDER BY latency_ms` y se indexa por posición en Python. El volumen
  de filas (máx. 90 días × N msgs/día) es manejable en memoria.
- **`ConversationContext` no debe crecer**: `search_stats` se agrega como field con `default_factory=dict`,
  no rompe ningún call site existente.
- **La tool `get_latency_stats` está gated por `tracing_enabled`**: igual que el resto de eval_tools.

---

## 5. Schema de datos

### Span metadata (Phase B / Phase D) — sin tabla nueva

El span `"phase_b"` ya guarda `metadata_json`. Se extiende con:

```json
{
  "search_mode": "semantic" | "fallback_threshold" | "full_fallback",
  "memories_retrieved": 5,
  "memories_passed_threshold": 3,
  "memories_returned": 3
}
```

El span `"llm_generation"` (o el span del tool loop) se extiende con:

```json
{
  "token_breakdown": {
    "system_prompt": 820,
    "user_memories": 3400,
    "active_projects": 0,
    "relevant_notes": 1200,
    "recent_activity": 5100,
    "conversation_summary": 800,
    "history": 7300,
    "total": 18620
  }
}
```

### Query de latencias (repository)

```sql
SELECT latency_ms
FROM trace_spans
WHERE name = :span_name
  AND started_at >= datetime('now', '-' || :days || ' days')
  AND latency_ms IS NOT NULL
ORDER BY latency_ms ASC
```

Percentiles calculados en Python:
```python
def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    idx = int(len(sorted_values) * p / 100)
    return sorted_values[min(idx, len(sorted_values) - 1)]
```

### Query de search stats (repository)

```sql
SELECT
    json_extract(metadata_json, '$.search_mode') AS mode,
    COUNT(*) AS n,
    AVG(json_extract(metadata_json, '$.memories_retrieved')) AS avg_retrieved,
    AVG(json_extract(metadata_json, '$.memories_passed_threshold')) AS avg_passed
FROM trace_spans
WHERE name = 'phase_b'
  AND started_at >= datetime('now', '-' || :days || ' days')
  AND metadata_json IS NOT NULL
GROUP BY mode
ORDER BY n DESC
```

---

## 6. Métricas de éxito

| Objetivo | Verificación |
|----------|-------------|
| El log `context.budget` incluye breakdown por sección | `grep "token_breakdown" logs.json` muestra desglose |
| `diagnose_trace` muestra token breakdown en span metadata | Llamar `diagnose_trace(trace_id)` desde WhatsApp |
| `get_latency_stats` devuelve p50/p95/p99 por span name | Llamar tool desde WhatsApp con datos reales |
| `get_search_stats` devuelve distribución de modos | Llamar tool y ver porcentajes coherentes con el config |
| Ninguna de las 3 mejoras añade latencia medible al critical path | Spans no cambian (todas las operaciones son best-effort / logging) |
