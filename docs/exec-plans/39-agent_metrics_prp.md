# PRP: Agent Metrics & Efficacy — Plan de Implementación

## Archivos a Modificar

| Archivo | Cambio |
|---|---|
| `app/database/repository.py` | Agregar 6 métodos de query nuevos |
| `app/webhook/router.py` | Guardar `context_fill_score` como trace score (Fase 2) |
| `app/agent/loop.py` | Background task `goal_completion_score` al final de sesión (Fase 3) |
| `app/skills/tools/eval_tools.py` | Nuevo tool `get_agent_stats` |
| `scripts/baseline.py` | Extender con Fases 1 y 2 |
| `scripts/dashboard.py` | Nueva sección "Agent Efficiency" (Fase 4) |
| `docs/exec-plans/README.md` | Agregar Plan 39 |
| `docs/features/37-metricas_benchmarking.md` | Actualizar con Plan 39 |
| `tests/test_agent_metrics.py` | **Nuevo** — tests para todas las fases |

---

## Fase 1: Tool & Token Efficiency

**Objetivo:** exponer las métricas de eficiencia de herramientas y tokens que ya están
almacenadas en `trace_spans` pero que no tienen queries de agregado.

Todos los datos ya están en DB. Esta fase es solo queries SQL + presentación.

### 1A. Métodos de Repository

- [x] Leer `app/database/repository.py` — sección Metrics Hardening (líneas ~1532+)
- [x] Agregar `get_tool_efficiency(self, days: int = 7) -> dict`:
  ```python
  async def get_tool_efficiency(self, days: int = 7) -> dict:
      """Return tool call efficiency metrics: calls/interaction, error rates, iterations."""
      # Tool calls per trace
      cursor = await self._conn.execute("""
          SELECT
              AVG(tool_count)    AS avg_tools,
              MAX(tool_count)    AS max_tools,
              SUM(CASE WHEN tool_count = 0 THEN 1 ELSE 0 END) AS no_tools_count,
              COUNT(*)           AS total_traces
          FROM (
              SELECT t.id, COUNT(s.id) AS tool_count
              FROM traces t
              LEFT JOIN trace_spans s
                ON s.trace_id = t.id AND s.kind = 'tool'
              WHERE t.started_at >= datetime('now', ? || ' days')
                AND t.status = 'completed'
              GROUP BY t.id
          )
      """, (f"-{days}",))
      row = await cursor.fetchone()
      tools_stats = {
          "avg_tool_calls": round(row[0] or 0, 2),
          "max_tool_calls": row[1] or 0,
          "no_tool_traces": row[2] or 0,
          "total_traces":   row[3] or 0,
      }

      # LLM iterations per trace
      cursor = await self._conn.execute("""
          SELECT
              AVG(iter_count) AS avg_iters,
              MAX(iter_count) AS max_iters
          FROM (
              SELECT trace_id, COUNT(*) AS iter_count
              FROM trace_spans
              WHERE name LIKE 'llm:iteration_%'
                AND started_at >= datetime('now', ? || ' days')
              GROUP BY trace_id
          )
      """, (f"-{days}",))
      row = await cursor.fetchone()
      tools_stats["avg_llm_iterations"] = round(row[0] or 0, 2)
      tools_stats["max_llm_iterations"] = row[1] or 0

      # Tool error rate per tool
      cursor = await self._conn.execute("""
          SELECT
              name,
              COUNT(*) AS total,
              SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS errors
          FROM trace_spans
          WHERE kind = 'tool'
            AND started_at >= datetime('now', ? || ' days')
          GROUP BY name
          ORDER BY errors DESC
          LIMIT 10
      """, (f"-{days}",))
      rows = await cursor.fetchall()
      tools_stats["tool_error_rates"] = [
          {
              "tool": r[0],
              "total": r[1],
              "errors": r[2],
              "error_rate": round(r[2] / r[1], 3) if r[1] else 0,
          }
          for r in rows
      ]
      return tools_stats
  ```

- [x] Agregar `get_token_consumption(self, days: int = 7) -> dict`:
  ```python
  async def get_token_consumption(self, days: int = 7) -> dict:
      """Return avg input/output token usage per interaction from span metadata."""
      cursor = await self._conn.execute("""
          SELECT
              AVG(json_extract(metadata, '$.gen_ai.usage.input_tokens'))  AS avg_input,
              AVG(json_extract(metadata, '$.gen_ai.usage.output_tokens')) AS avg_output,
              SUM(json_extract(metadata, '$.gen_ai.usage.input_tokens'))  AS total_input,
              SUM(json_extract(metadata, '$.gen_ai.usage.output_tokens')) AS total_output,
              COUNT(*) AS n
          FROM trace_spans
          WHERE kind = 'generation'
            AND started_at >= datetime('now', ? || ' days')
            AND json_extract(metadata, '$.gen_ai.usage.input_tokens') IS NOT NULL
      """, (f"-{days}",))
      row = await cursor.fetchone()
      if not row or not row[4]:
          return {}
      return {
          "avg_input_tokens":  round(row[0] or 0, 1),
          "avg_output_tokens": round(row[1] or 0, 1),
          "total_input_tokens":  int(row[2] or 0),
          "total_output_tokens": int(row[3] or 0),
          "n_generations": row[4],
      }
  ```

- [x] Agregar `get_tool_redundancy(self, days: int = 7) -> list[dict]`:
  ```python
  async def get_tool_redundancy(self, days: int = 7) -> list[dict]:
      """Detect traces where the same tool was called with identical args (redundant calls)."""
      cursor = await self._conn.execute("""
          SELECT trace_id, name, input, COUNT(*) AS call_count
          FROM trace_spans
          WHERE kind = 'tool'
            AND started_at >= datetime('now', ? || ' days')
          GROUP BY trace_id, name, input
          HAVING COUNT(*) > 1
          ORDER BY call_count DESC
          LIMIT 20
      """, (f"-{days}",))
      rows = await cursor.fetchall()
      return [
          {
              "trace_id": r[0][:12],
              "tool": r[1],
              "repeated_calls": r[3],
          }
          for r in rows
      ]
  ```

### 1B. Tool `get_agent_stats` en eval_tools.py

- [x] Leer `app/skills/tools/eval_tools.py` — sección de registro al final
- [x] Agregar handler `get_agent_stats(days: int = 7, focus: str = "all") -> str`
  dentro de `register()`. `focus` acepta: `"all"`, `"tools"`, `"tokens"`, `"iterations"`.
- [x] Formato de salida:
  ```
  *Agent Efficiency — últimos 7 días*

  Tool calls por interacción:
  - Promedio: 2.3  Max: 9  Sin tools: 42% (chat puro)

  Iteraciones LLM por interacción:
  - Promedio: 1.8  Max: 5

  Tool error rates (top 5):
  - weather_tools: 8.1% (3/37)
  - calculator:    1.2% (2/165)

  Token consumption:
  - Avg input:  4,200 tok/traza  Total: 980K/semana
  - Avg output: 380 tok/traza    Total: 88K/semana
  - (Chat simple ~2.1K input; con tools ~8.4K input)

  Calls redundantes detectados: 4 trazas
  ```
- [x] Registrar con `registry.register_tool(name="get_agent_stats", ...)`
- [x] Agregar `"evaluation"` ya está en `TOOL_CATEGORIES` — no requiere cambio en router

### 1C. Extender `scripts/baseline.py`

- [x] Importar y llamar `get_tool_efficiency()` y `get_token_consumption()` en `_fetch_baseline()`
- [x] Agregar sección "TOOL & TOKEN EFFICIENCY" al output del `_print_report()`
- [x] Incluir en el JSON de snapshot bajo keys `"tool_efficiency"` y `"token_consumption"`

### 1D. Tests

- [x] Crear `tests/test_agent_metrics.py`
- [x] `test_get_tool_efficiency_no_data` — repo vacío → dict con zeros
- [x] `test_get_tool_efficiency_with_data` — mock con spans tool+iteration → stats correctos
- [x] `test_get_token_consumption_no_data` — sin metadata de tokens → dict vacío
- [x] `test_get_token_consumption_aggregates_correctly` — suma y promedio correctos
- [x] `test_get_tool_redundancy_detects_repeated_calls` — misma tool+args x2 → flaggeada
- [x] `test_get_agent_stats_formats_output` — mock repo → string con secciones esperadas

### 1E. Validación Fase 1

- [x] `make check` pasa limpio
- [x] Verificar desde WhatsApp: "dame las estadísticas del agente"
- [x] Verificar que `python scripts/baseline.py` incluye sección Tool Efficiency
- [x] Query manual de sanidad:
  ```sql
  SELECT AVG(cnt), MAX(cnt) FROM (
    SELECT trace_id, COUNT(*) cnt FROM trace_spans
    WHERE kind='tool' GROUP BY trace_id
  );
  ```

---

## Fase 2: Context Quality Metrics

**Objetivo:** persistir métricas de calidad del contexto como scores en DB (no solo logs),
y agregar queries de correlación para detectar context rot.

### 2A. `context_fill_score` como trace score

- [x] Leer `app/context/token_estimator.py` — función `log_context_budget()`
- [x] Leer `app/webhook/router.py` — bloque de token budget en `_run_normal_flow()`
      (buscar `log_context_budget`)
- [x] En el bloque del token budget en `_run_normal_flow()`, después de `log_context_budget()`,
  agregar (best-effort, dentro del `try`):
  ```python
  if trace_ctx:
      pct = total_tokens / settings.context_limit if settings else 0
      await trace_ctx.add_score(
          name="context_fill_rate",
          value=round(min(pct, 1.0), 3),
          source="system",
          comment=f"tokens={total_tokens}",
      )
  ```
  **Nota:** `total_tokens` y `context_limit` ya están en scope en ese bloque.
  El score value=0.0..1.0 representa el % de llenado del contexto.

### 2B. `classify_upgrade_rate` como score

- [x] En `_run_normal_flow()`, en el bloque donde `needs_context_upgrade=True` (Phase C),
  agregar:
  ```python
  if trace_ctx and needs_context_upgrade:
      await trace_ctx.add_score(
          name="classify_upgrade",
          value=1.0,
          source="system",
          comment=f"base={base_result} → upgraded with context",
      )
  ```
  Esto hace que cada traza que necesitó re-classify tenga un score "classify_upgrade".
  La tasa se calcula como `COUNT(classify_upgrade) / COUNT(traces)`.

### 2C. Repository queries para context quality

- [x] Agregar `get_context_quality_metrics(self, days: int = 7) -> dict`:
  ```python
  async def get_context_quality_metrics(self, days: int = 7) -> dict:
      """Return context quality aggregates: fill rate, classify upgrade rate, memory relevance."""
      # Context fill rate distribution
      cursor = await self._conn.execute("""
          SELECT
              AVG(value)  AS avg_fill,
              MAX(value)  AS max_fill,
              SUM(CASE WHEN value > 0.8 THEN 1 ELSE 0 END) AS near_limit_count,
              COUNT(*)    AS n
          FROM trace_scores
          WHERE name = 'context_fill_rate'
            AND created_at >= datetime('now', ? || ' days')
      """, (f"-{days}",))
      row = await cursor.fetchone()
      fill_stats = {
          "avg_fill_rate":    round((row[0] or 0) * 100, 1),
          "max_fill_rate":    round((row[1] or 0) * 100, 1),
          "near_limit_count": row[2] or 0,
          "n":                row[3] or 0,
      }

      # Classify upgrade rate
      cursor = await self._conn.execute("""
          SELECT
              COUNT(DISTINCT s.trace_id) AS upgraded,
              (SELECT COUNT(*) FROM traces
               WHERE status = 'completed'
                 AND started_at >= datetime('now', ? || ' days')) AS total
          FROM trace_scores s
          WHERE s.name = 'classify_upgrade'
            AND s.created_at >= datetime('now', ? || ' days')
      """, (f"-{days}", f"-{days}"))
      row = await cursor.fetchone()
      upgraded = row[0] or 0
      total    = row[1] or 1
      fill_stats["classify_upgrade_rate"] = round(upgraded / total * 100, 1)
      fill_stats["classify_upgraded_n"]   = upgraded

      # Memory relevance proxy: memories_passed / memories_retrieved from phase_ab metadata
      cursor = await self._conn.execute("""
          SELECT
              AVG(json_extract(metadata, '$.memories_retrieved')) AS avg_retrieved,
              AVG(json_extract(metadata, '$.memories_passed'))    AS avg_passed,
              AVG(json_extract(metadata, '$.memories_returned'))  AS avg_returned
          FROM trace_spans
          WHERE name = 'phase_ab'
            AND started_at >= datetime('now', ? || ' days')
            AND json_extract(metadata, '$.memories_retrieved') IS NOT NULL
      """, (f"-{days}",))
      row = await cursor.fetchone()
      fill_stats["avg_memories_retrieved"] = round(row[0] or 0, 1)
      fill_stats["avg_memories_passed"]    = round(row[1] or 0, 1)
      fill_stats["avg_memories_returned"]  = round(row[2] or 0, 1)
      if (row[0] or 0) > 0:
          fill_stats["memory_relevance_pct"] = round((row[1] or 0) / (row[0] or 1) * 100, 1)
      else:
          fill_stats["memory_relevance_pct"] = None

      return fill_stats
  ```

- [x] Agregar `get_context_rot_risk(self, days: int = 7) -> list[dict]`:
  ```python
  async def get_context_rot_risk(self, days: int = 7) -> list[dict]:
      """Correlate context fill rate with guardrail pass rate to detect context rot.

      Returns two buckets: high_context (fill > 0.70) vs normal.
      If avg_guardrail_pass is lower for high_context, context rot is active.
      """
      cursor = await self._conn.execute("""
          SELECT
              CASE WHEN cf.value > 0.70 THEN 'high_context' ELSE 'normal' END AS bucket,
              AVG(gp.avg_pass)  AS avg_guardrail_pass,
              AVG(cf.value)     AS avg_fill_rate,
              COUNT(*)          AS n
          FROM trace_scores cf
          JOIN (
              SELECT trace_id, AVG(value) AS avg_pass
              FROM trace_scores
              WHERE source = 'system' AND name != 'context_fill_rate'
                AND name != 'classify_upgrade' AND name != 'repeated_question'
              GROUP BY trace_id
          ) gp ON gp.trace_id = cf.trace_id
          WHERE cf.name = 'context_fill_rate'
            AND cf.created_at >= datetime('now', ? || ' days')
          GROUP BY bucket
          ORDER BY bucket
      """, (f"-{days}",))
      rows = await cursor.fetchall()
      return [
          {
              "bucket":              r[0],
              "avg_guardrail_pass":  round((r[1] or 0) * 100, 1),
              "avg_fill_rate_pct":   round((r[2] or 0) * 100, 1),
              "n":                   r[3],
          }
          for r in rows
      ]
  ```

### 2D. Extender `get_agent_stats` con context quality

- [x] Cuando `focus="all"` o `focus="context"`, llamar `get_context_quality_metrics()`
  y `get_context_rot_risk()` y agregar sección al output
- [x] Formato:
  ```
  Context Quality:
  - Fill rate avg: 34.2%  max: 91.4%  Near-limit (>80%): 3 trazas
  - Classify upgrade rate: 18.3% (33/180 interacciones necesitaron re-classify)
  - Memory relevance: 72% pasaron el threshold (avg 3.6 recuperadas → 2.6 usadas)

  Context Rot Risk:
  - Normal context (<70%): guardrail pass = 96.2%  (n=170)
  - High context (>70%):   guardrail pass = 88.1%  (n=10)  ⚠️ posible context rot
  ```

### 2E. Extender `scripts/baseline.py`

- [x] Llamar `get_context_quality_metrics()` y `get_context_rot_risk()` en `_fetch_baseline()`
- [x] Agregar sección "CONTEXT QUALITY" al `_print_report()`

### 2F. Tests (agregar a `tests/test_agent_metrics.py`)

- [x] `test_context_fill_score_saved_as_trace_score` — mock trace_ctx, verificar add_score
- [x] `test_classify_upgrade_saved_when_needed` — mock + flow, verificar score guardado
- [x] `test_get_context_quality_metrics_no_data` — repo vacío → dict con zeros/None
- [x] `test_get_context_quality_metrics_aggregates` — datos mock → % correctos
- [x] `test_get_context_rot_risk_two_buckets` — datos con fill alto y bajo → 2 buckets

### 2G. Validación Fase 2

- [x] `make check` pasa limpio
- [x] Enviar 10+ mensajes en el sistema y verificar:
  ```sql
  SELECT name, AVG(value), COUNT(*) FROM trace_scores
  WHERE name IN ('context_fill_rate', 'classify_upgrade') GROUP BY name;
  ```
- [x] Verificar que `get_agent_stats` muestra sección "Context Quality"

---

## Fase 3: Agent Efficacy Metrics

**Objetivo:** métricas que responden "¿completó el objetivo el agente?" y
"¿cuánto overhead generó el modo agéntico?".

### 3A. `replanning_rate` en repository

- [x] Agregar `get_planner_metrics(self, days: int = 7) -> dict`:
  ```python
  async def get_planner_metrics(self, days: int = 7) -> dict:
      """Return metrics for planner-orchestrator sessions."""
      # Sessions with at least one planner:create_plan span
      cursor = await self._conn.execute("""
          SELECT
              COUNT(DISTINCT trace_id)  AS total_sessions
          FROM trace_spans
          WHERE name = 'planner:create_plan'
            AND started_at >= datetime('now', ? || ' days')
      """, (f"-{days}",))
      row = await cursor.fetchone()
      total = row[0] or 0
      if total == 0:
          return {"total_planner_sessions": 0}

      # Sessions with at least one replan
      cursor = await self._conn.execute("""
          SELECT COUNT(DISTINCT trace_id) AS replanned
          FROM trace_spans
          WHERE name = 'planner:replan'
            AND started_at >= datetime('now', ? || ' days')
      """, (f"-{days}",))
      row = await cursor.fetchone()
      replanned = row[0] or 0

      # Avg replans per session (among those that replanned)
      cursor = await self._conn.execute("""
          SELECT AVG(replan_count) FROM (
              SELECT trace_id, COUNT(*) AS replan_count
              FROM trace_spans
              WHERE name = 'planner:replan'
                AND started_at >= datetime('now', ? || ' days')
              GROUP BY trace_id
          )
      """, (f"-{days}",))
      row = await cursor.fetchone()

      return {
          "total_planner_sessions":  total,
          "replanned_sessions":      replanned,
          "replanning_rate_pct":     round(replanned / total * 100, 1),
          "avg_replans_per_session": round(row[0] or 0, 2),
      }
  ```

### 3B. `hitl_escalation_rate`

- [x] Agregar `get_hitl_rate(self, days: int = 7) -> dict`:
  HITL se registra cuando `PolicyEngine` devuelve `FLAG` y el executor pausa.
  Actualmente se loguea pero no se guarda como score. Dos sub-tareas:

  **3B-i**: En `app/skills/executor.py`, cuando se resuelve HITL approval (aprobado o
  rechazado), guardar un trace score:
  ```python
  trace = get_current_trace()
  if trace:
      await trace.add_score(
          name="hitl_escalation",
          value=1.0 if approved else 0.0,
          source="system",
          comment=f"tool={tool_name} approved={approved}",
      )
  ```
  - [x] Leer `app/skills/executor.py` para encontrar el punto correcto de inserción
  - [x] Agregar el score en el callback de resolución HITL

  **3B-ii**: Query de agregado:
  ```python
  async def get_hitl_rate(self, days: int = 7) -> dict:
      cursor = await self._conn.execute("""
          SELECT
              COUNT(*)  AS total_escalations,
              SUM(CASE WHEN value = 1.0 THEN 1 ELSE 0 END) AS approved,
              SUM(CASE WHEN value = 0.0 THEN 1 ELSE 0 END) AS rejected
          FROM trace_scores
          WHERE name = 'hitl_escalation'
            AND created_at >= datetime('now', ? || ' days')
      """, (f"-{days}",))
      row = await cursor.fetchone()
      return {
          "total_escalations": row[0] or 0,
          "approved":          row[1] or 0,
          "rejected":          row[2] or 0,
      }
  ```

### 3C. `goal_completion_score` (LLM-as-judge, background task)

- [x] Leer `app/agent/loop.py` — final de `run_agent_session()`, donde se genera la
  respuesta final
- [x] Agregar función `_score_goal_completion(session, output, ollama_client, trace_ctx)`:
  ```python
  async def _score_goal_completion(
      initial_message: str,
      final_output: str,
      ollama_client: OllamaClient,
      trace_ctx: TraceContext,
  ) -> None:
      """LLM-as-judge: did the agent complete the user's goal? Best-effort background."""
      try:
          prompt = (
              f"User request: {initial_message[:300]}\n"
              f"Agent final response: {final_output[:400]}\n\n"
              "Did the agent's response successfully address the user's request? "
              "Reply ONLY 'yes' or 'no'."
          )
          response = await ollama_client.chat(
              messages=[{"role": "user", "content": prompt}],
              think=False,
          )
          verdict = (response.content or "").strip().lower()
          score = 1.0 if verdict.startswith("yes") else 0.0
          await trace_ctx.add_score(
              name="goal_completion",
              value=score,
              source="system",
              comment=f"LLM-as-judge: {verdict[:20]}",
          )
      except Exception:
          logger.debug("goal_completion scoring failed (best-effort)", exc_info=True)
  ```
- [x] Llamar como `asyncio.create_task()` al final de `run_agent_session()`, antes del
  return — solo si `trace_ctx` y `ollama_client` están disponibles
- [x] Agregar `get_goal_completion_rate(self, days: int = 7) -> dict` en repository:
  ```python
  async def get_goal_completion_rate(self, days: int = 7) -> dict:
      cursor = await self._conn.execute("""
          SELECT AVG(value) AS rate, COUNT(*) AS n
          FROM trace_scores
          WHERE name = 'goal_completion'
            AND created_at >= datetime('now', ? || ' days')
      """, (f"-{days}",))
      row = await cursor.fetchone()
      return {
          "goal_completion_rate_pct": round((row[0] or 0) * 100, 1),
          "n": row[1] or 0,
      }
  ```

### 3D. Actualizar `get_agent_stats` con Fase 3

- [x] Cuando `focus="all"` o `focus="agent"`, incluir planner + HITL + goal completion:
  ```
  Agent Efficacy:
  - Planner sessions: 23 → 30% necesitaron replan (avg 1.4 replans)
  - HITL escalations: 7 (5 aprobadas, 2 rechazadas)
  - Goal completion (LLM-as-judge): 74%  (n=19 sesiones agénticas)
    ⚠ Advisory: auto-judge con el mismo modelo puede inflar el score
  ```

### 3E. Tests (agregar a `tests/test_agent_metrics.py`)

- [x] `test_get_planner_metrics_no_sessions` → `{"total_planner_sessions": 0}`
- [x] `test_get_planner_metrics_with_replans` → replanning_rate_pct correcto
- [x] `test_get_hitl_rate_aggregates` → approved/rejected correctos
- [x] `test_score_goal_completion_yes` → mock ollama retorna "yes" → score 1.0
- [x] `test_score_goal_completion_no` → mock ollama retorna "no" → score 0.0
- [x] `test_score_goal_completion_fail_open` → ollama lanza excepción → no propaga

### 3F. Validación Fase 3

- [x] `make check` pasa limpio
- [x] Trigger una sesión agéntica (comando `/dev-review`) y verificar:
  ```sql
  SELECT name, value, comment FROM trace_scores
  WHERE name IN ('hitl_escalation', 'goal_completion')
  ORDER BY created_at DESC LIMIT 10;
  ```
- [x] Verificar que `get_agent_stats` muestra todas las secciones

---

## Fase 4: Dashboard y Documentación

### 4A. Extender `scripts/dashboard.py`

- [x] Leer `scripts/dashboard.py` — función `_fetch_all_data()` y `_render_html()`
- [x] Agregar al `_fetch_all_data()`:
  ```python
  "tool_efficiency":  await repo.get_tool_efficiency(days),
  "token_consumption": await repo.get_token_consumption(days),
  "context_quality":  await repo.get_context_quality_metrics(days),
  "planner_metrics":  await repo.get_planner_metrics(days),
  "goal_completion":  await repo.get_goal_completion_rate(days),
  ```
- [x] Agregar sección "Agent Efficiency" en el HTML:
  - Card: Avg tool calls/interaction + Avg LLM iterations
  - Card: Goal completion rate % (con nota "advisory")
  - Tabla: Tool error rates (top 5 tools con más errores)
  - Card: Context fill rate avg % + Near-limit count
  - Card: Classify upgrade rate %
  - (Si hay datos): Context rot risk table (high vs normal context)

### 4B. Extender `scripts/baseline.py`

- [x] Fase 3 data en `_fetch_baseline()`: planner metrics + HITL rate + goal completion
- [x] Agregar sección "AGENT EFFICACY" al `_print_report()`
- [x] Guardar en el JSON bajo key `"agent_efficacy"`

### 4C. Documentación

- [x] Actualizar `docs/features/37-metricas_benchmarking.md`:
  - Agregar sección sobre Fase 1-3 de Plan 39
  - Agregar tabla de todas las métricas disponibles post-Plan 39
- [x] Actualizar `docs/exec-plans/README.md` con Plan 39
- [x] Actualizar `CLAUDE.md` con patrones de las Fases 3 (goal_completion background task)
- [x] Agregar entrada en `docs/features/README.md`

### 4D. Validación Final

- [x] `make check` pasa limpio (lint + typecheck + tests)
- [x] `python scripts/baseline.py` muestra 7 secciones completas
- [x] `python scripts/dashboard.py` genera HTML con sección "Agent Efficiency"
- [x] Desde WhatsApp: "dame todas las estadísticas del agente de los últimos 30 días"
  → respuesta con todas las secciones (tool efficiency + context quality + agent efficacy)

---

## Orden de commits sugerido

```
1. feat: add get_tool_efficiency and get_token_consumption to repository
2. feat: add get_agent_stats tool to eval skill
3. feat: extend baseline.py with tool and token efficiency metrics
4. feat: add context_fill_score and classify_upgrade as trace scores
5. feat: add get_context_quality_metrics and get_context_rot_risk queries
6. feat: add hitl_escalation score in executor
7. feat: add goal_completion LLM-as-judge background task in agent loop
8. feat: add get_planner_metrics, get_hitl_rate, get_goal_completion_rate
9. feat: extend dashboard with agent efficiency section
10. docs: update metrics_benchmarking and exec-plans README for Plan 39
```

---

## Diagrama de flujo post-implementación

```
message processed
    │
    ├── phase_ab span
    │     └── metadata: embed_ms, searches_ms, search_mode,
    │                   memories_retrieved, memories_passed    [ya existía]
    │
    ├── tool_loop span
    │     ├── llm:iteration_N [ya existía]
    │     └── tool:name spans [ya existía]
    │
    ├── trace_scores
    │     ├── guardrail scores (not_empty, language_match, etc.)  [ya existía]
    │     ├── context_fill_rate   [NEW Fase 2]
    │     ├── classify_upgrade    [NEW Fase 2]
    │     └── hitl_escalation     [NEW Fase 3]
    │
    └── [background] goal_completion LLM-as-judge  [NEW Fase 3, solo sessions agénticas]

get_agent_stats tool:
    ├── get_tool_efficiency()          [NEW Fase 1]
    ├── get_token_consumption()        [NEW Fase 1]
    ├── get_tool_redundancy()          [NEW Fase 1]
    ├── get_context_quality_metrics()  [NEW Fase 2]
    ├── get_context_rot_risk()         [NEW Fase 2]
    ├── get_planner_metrics()          [NEW Fase 3]
    ├── get_hitl_rate()                [NEW Fase 3]
    └── get_goal_completion_rate()     [NEW Fase 3]

scripts/baseline.py:
    ├── Sección 1: Trace volume         [ya existía]
    ├── Sección 2: E2E latency          [ya existía]
    ├── Sección 3: Phase breakdown      [ya existía]
    ├── Sección 4: All spans            [ya existía]
    ├── Sección 5: Search modes         [ya existía]
    ├── Sección 6: Tool & token efficiency  [NEW Fase 1]
    ├── Sección 7: Context quality          [NEW Fase 2]
    └── Sección 8: Agent efficacy           [NEW Fase 3]
```
