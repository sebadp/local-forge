# PRD: Agent Metrics & Efficacy — Cobertura Completa de Observabilidad

## 1. Objetivo y Contexto

Plan 38 (Metrics Hardening) resolvió la visibilidad de **velocidad** del pipeline: latencias
p50/p95 por span, token budget breakdown, semantic search hit rate, dashboard HTML.

Sin embargo, el análisis post-Plan 38 (y la investigación de estado del arte 2026)
identifica que medir solo velocidad es insuficiente para un sistema agéntico en producción.
Amazon (AWS Blog, Feb 2026), Galileo, Berkeley BFCL V4 y Chroma Research coinciden en que
los sistemas LLM+agente necesitan tres capas de métricas:

1. **Velocidad** (cubierta) → latencias por span, e2e, Phase A/B breakdown.
2. **Calidad del contexto** (parcialmente cubierta) → token budget, search mode.
3. **Eficacia del agente** (no cubierta) → tool accuracy, iteration efficiency, goal completion.

**Problema:** el sistema actual no puede responder preguntas como:
- ¿Cuántos tool calls en promedio necesita el agente para completar una tarea?
- ¿Con qué frecuencia el LLM elige la herramienta equivocada o llena mal los parámetros?
- ¿El planner necesita replanificar con frecuencia? (señal de que el plan inicial es malo)
- ¿Cuántos tokens consume por interacción? ¿Cuál es el costo de tool-heavy vs chat simple?
- ¿El contexto tiene "context rot"? (correlación entre tamaño del contexto y calidad)

**Objetivo:** implementar las tres capas por completo, en fases incrementales, con impacto
cero en el critical path de procesamiento de mensajes (todo best-effort o background).

---

## 2. Alcance

### In Scope

**Fase 1 — Tool & Token Efficiency (datos ya en DB, solo queries nuevas)**
- Tool calls por interacción (count, distribución, p50/p95)
- Iteraciones LLM por interacción (loops del tool_loop)
- Tool error rate por tool (% de tool spans con status=failed)
- Token consumption: input_tokens + output_tokens por traza (de span metadata)
- Extend `baseline.py` con estas métricas
- Extend `get_latency_stats` o nuevo tool `get_agent_stats`

**Fase 2 — Context Quality Metrics**
- `classify_upgrade_rate`: % de veces que `base_result="none"` y necesitó re-classify con
  contexto — indica cuántas veces el clasificador inicial falló
- `context_fill_score`: guardar el `pct` del token budget como score de la traza (no solo
  log) — permite correlacionar llenado de contexto con guardrail failures
- `memory_relevance_proxy`: ratio `memories_passed / memories_retrieved` de search_stats —
  ya está en span metadata, solo necesita query de agregado
- Context rot risk index: correlación entre `context_fill_score` alto y `guardrail_pass_rate`
  bajo en el mismo rango de tiempo

**Fase 3 — Agent Efficacy Metrics**
- `replanning_rate`: % de sesiones del planner que tuvieron al menos un span `planner:replan`
- `hitl_escalation_rate`: % de tool calls que fueron FLAG (requirieron aprobación humana)
- `goal_completion_score`: LLM-as-judge al final de sesiones agénticas — ¿se completó el objetivo?
- `tool_redundancy_index`: tool calls extras más allá de lo necesario (detectado por repetición
  de la misma tool con los mismos args en la misma traza)

**Fase 4 — Dashboard y Reporting**
- Extender `scripts/baseline.py` con todas las métricas nuevas
- Extender `scripts/dashboard.py` con sección "Agent Efficiency"
- Extender tool `get_agent_stats` en eval skill
- Documentación actualizada

### Out of Scope

- **True tool selection accuracy**: requiere dataset labeled de "tool esperada" por mensaje.
  Sin ground truth, imposible medir determinísticamente. Descartado por ahora.
- **True tool parameter accuracy**: mismo problema. Detectaremos errores de ejecución,
  no parámetros semánticamente incorrectos.
- **Streaming / TTFT**: requiere refactorizar el cliente Ollama para streaming.
  Impacto demasiado grande para este plan.
- **Cambios en el critical path**: todo lo nuevo es background, best-effort, o post-proceso.
  Cero latencia agregada a `_handle_message`.
- **UI/frontend de métricas**: el dashboard HTML es suficiente para esta etapa.

---

## 3. Casos de Uso Críticos

### 3.1 "¿Cuántas tools usa el agente típicamente?"

**Antes:** imposible responder cuantitativamente.
**Después:**
```
"¿cuántas herramientas usa el agente por interacción?"
→ get_agent_stats(days=7)

Tool efficiency (últimos 7 días):
- Promedio tool calls/interacción: 2.3
- p95 tool calls/interacción: 6
- Interacciones sin tools: 42% (chat simple)
- Iteraciones LLM p50: 1.8  p95: 4
- Tool error rate: 3.2% (top: weather_tools 8.1%)
```

### 3.2 "¿El contexto está causando degradación?"

**Antes:** hay logs de `context.budget` pero sin correlación con calidad.
**Después:**
```sql
-- Context rot risk: ¿los mensajes con contexto > 70% tienen más guardrail failures?
SELECT
  CASE WHEN context_fill > 0.70 THEN 'high_context' ELSE 'normal' END as bucket,
  AVG(guardrail_pass) as avg_quality,
  COUNT(*) as n
FROM context_fill_scores
JOIN guardrail_pass_rates USING (trace_id)
GROUP BY bucket;
-- Si avg_quality[high_context] < avg_quality[normal] por >10%: context rot activo
```

### 3.3 "¿El planner es confiable?"

**Antes:** no hay métricas de replanificación.
**Después:**
```
"¿cuántas sesiones del planner necesitaron replanificar?"
→ get_agent_stats(session_type="planner", days=30)

Planner sessions (últimos 30 días):
- Total sesiones: 23
- Con replan: 7 (30%)    ← si > 20%: el plan inicial es frecuentemente incorrecto
- Avg replans/session: 1.4
- Goal completion rate: 74% (LLM-as-judge)
```

### 3.4 "¿Qué tan eficiente es el token usage?"

**Antes:** hay `gen_ai.usage.input_tokens` en span metadata pero sin query agregada.
**Después:**
```
Token consumption (últimos 7 días):
- Avg input tokens/traza: 4,200
- Avg output tokens/traza: 380
- Total tokens/día: ~180K
- Chat simple: avg 2,100 input, 290 output
- Con tool loop: avg 8,400 input, 520 output  ← el multi-turn acumula tokens
```

---

## 4. Restricciones Arquitectónicas

- **Zero latency impact**: toda instrumentación nueva es post-process (background tasks) o
  queries offline (scripts). Nada se agrega al critical path de `_handle_message`.
- **Fail-open siempre**: si una query de métricas falla, no propaga la excepción. Log y
  devolver vacío.
- **Sin dependencias nuevas**: todo se resuelve con SQLite + Python stdlib. No se agrega
  ningún paquete.
- **Backward compatible**: los nuevos scores y campos son opcionales. Trazas existentes
  (sin los nuevos scores) siguen siendo válidas.
- **Best-effort para LLM-as-judge** (Fase 3): `goal_completion_score` corre como background
  task al final de sesiones agénticas. Si Ollama no está disponible, se omite silenciosamente.
- **Datos en DB suficientes**: Fases 1 y 2 extraen información de spans ya existentes en
  `trace_spans`. No requieren instrumentación nueva del pipeline. Fase 3 sí requiere
  guardar nuevos scores.

---

## 5. Métricas de Éxito

| Métrica | Estado pre-Plan 39 | Target post-Plan 39 |
|---|---|---|
| Preguntas respondibles sobre tool efficiency | 0 | 4+ (tool calls/iter, error rate, tokens, iterations) |
| Preguntas respondibles sobre context quality | 2 (budget, search mode) | 5+ (+ fill score, relevance proxy, rot risk) |
| Preguntas respondibles sobre agent efficacy | 0 | 3+ (replanning rate, HITL rate, goal completion) |
| Cobertura de span metadata aprovechada | ~30% | ~80% |
| Tiempo para capturar baseline completo | ~2min (solo latencias) | ~2min (latencias + eficacia + contexto) |
| `baseline.py` secciones | 4 | 7+ |

---

## 6. Riesgos

| Riesgo | Probabilidad | Mitigación |
|---|---|---|
| `goal_completion_score` infla métricas (auto-juicio con mismo modelo) | Alta | Documentar limitación; marcar como "advisory". Usar correction pairs para validación cruzada |
| `tool_redundancy_index` da falsos positivos (misma tool con args distintos) | Media | Comparar args con `json_extract` + threshold de similitud |
| Queries de Fase 2 lentas si DB tiene >100K spans | Baja | Índices existentes (`idx_spans_trace`, `idx_spans_kind`) los cubren. Agregar `idx_spans_name` si hace falta |
| `replanning_rate` depende de que el planner esté habilitado | Media | Mostrar "no data" + nota explicativa si 0 sesiones de planner |

---

## 7. Secuencia de Implementación

```
Fase 1: Tool & Token Efficiency        (~3-4h)   ← solo queries SQL + tool nueva
Fase 2: Context Quality Metrics        (~4-5h)   ← score nuevo + 2 queries + correlación
Fase 3: Agent Efficacy                 (~6-8h)   ← background task LLM-as-judge + scores
Fase 4: Dashboard + Documentación      (~3h)     ← extender scripts + docs

Total estimado: 16-20h de implementación
```

Cada fase es independiente y deployable por sí sola. No hay dependencias entre fases.

---

## 8. Conexión con otros planes

- **Plan 36 (Performance Optimization)**: las métricas de Fase 1 (tool calls/iteration,
  iterations/interaction, token consumption) son el baseline para medir el impacto de
  las optimizaciones de Plan 36. **Implementar Fase 1 de este plan antes de iniciar Plan 36.**
- **Plan 38 (Metrics Hardening)**: este plan extiende, no reemplaza, Plan 38.
  Los spans, scores y queries de Plan 38 siguen siendo la base.
- **Plan 28 (Planner-Orchestrator)**: la Fase 3 de este plan instrumenta métricas del
  planner. Requiere que el planner esté funcionando (ya completado).
