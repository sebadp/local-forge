# PRP: Metrics Hardening (Plan 38)

## Archivos a Modificar

| Archivo | Cambio |
|---------|--------|
| `app/context/token_estimator.py` | Agregar `estimate_sections()` y `log_context_budget_breakdown()` |
| `app/webhook/router.py` | Llamar breakdown en `_run_normal_flow()`, pushear a span metadata |
| `app/context/conversation_context.py` | Agregar field `search_stats: dict` + popularlo en `_get_memories_with_threshold()` |
| `app/database/repository.py` | Agregar `get_latency_percentiles()` y `get_search_hit_rate()` |
| `app/skills/tools/eval_tools.py` | Agregar tools `get_latency_stats` y `get_search_stats` |
| `tests/test_token_estimator.py` | **Nuevo** — tests para breakdown |
| `tests/test_metrics_tools.py` | **Nuevo** — tests para las dos tools nuevas |

---

## Fases de Implementación

### Phase 1: Token budget breakdown

**Objetivo:** Que el log `context.budget` incluya desglose por sección y que ese desglose
aparezca en los spans de tracing.

- [ ] Leer `app/context/token_estimator.py` completo
- [ ] Agregar función `estimate_sections(sections: dict[str, str | None]) -> dict[str, int]`:
  ```python
  def estimate_sections(sections: dict[str, str | None]) -> dict[str, int]:
      """Compute token estimate per named section. None/empty sections count as 0."""
      return {name: estimate_tokens(text) if text else 0 for name, text in sections.items()}
  ```
- [ ] Agregar función `log_context_budget_breakdown(sections: dict[str, int], context_limit: int = _CONTEXT_LIMIT) -> None`:
  - Calcular `total = sum(sections.values())`
  - Identificar `largest_section = max(sections, key=sections.get)`
  - Emitir un único log INFO con `extra={"token_breakdown": sections, "largest_section": largest_section, "total": total}`
  - NO emitir en WARNING/ERROR — eso ya lo hace `log_context_budget`
- [ ] Leer `app/webhook/router.py` función `_run_normal_flow()` (lines ~1280-1340)
- [ ] En `_run_normal_flow()`, después de llamar `_build_context()` y ANTES de `log_context_budget()`,
  agregar (dentro del `try` de token budget existente):
  ```python
  from app.context.token_estimator import estimate_sections, log_context_budget_breakdown

  sections = estimate_sections({
      "system_prompt": context[0].content if context else "",  # primer msg es system
      "history": " ".join(m.content or "" for m in context[1:]),
  })
  log_context_budget_breakdown(sections)
  ```
  Nota: el desglose más fino (memorias / daily_logs / notas por separado) requeriría acceso
  a las variables intermedias de `_build_context`. Para esta fase, system vs history es suficiente
  y correcto. El desglose fino se puede agregar si `_build_context` se refactoriza para retornar
  el breakdown (future work).
- [ ] Pushear el breakdown al span de tracing activo (best-effort, dentro del mismo `try`):
  ```python
  from app.tracing.context import get_current_trace
  trace_ctx = get_current_trace()
  if trace_ctx:
      # Attach to the active span via metadata — span name varies, use set_metadata via span context
      # La forma más simple: agregar al extra del log + no al span (los spans ya tienen latency_ms)
      # Ver nota de diseño abajo
  ```
  **Nota de diseño:** Los spans se cierran con `async with trace_ctx.span(...)`. El breakdown
  se computa FUERA de cualquier span activo en ese punto del código. La alternativa más simple
  y sin riesgo es incluirlo solo en el log estructurado (opción A). La alternativa más rica es
  agregar un span efímero `context:budget` (kind="span") que solo tenga `metadata` (opción B).
  **Usar opción A para esta fase** — el log estructurado con JSON ya es suficientemente consultable.
- [ ] Tests en `tests/test_token_estimator.py`:
  - [ ] `test_estimate_sections_basic` — dict de strings → dict de ints proporcionales
  - [ ] `test_estimate_sections_none_values` — None sections → 0
  - [ ] `test_estimate_sections_empty_string` — "" → 0
  - [ ] `test_log_context_budget_breakdown_emits_info` — mock logger, verificar extra keys

---

### Phase 2: Latencia p50/p95 por operación

**Objetivo:** Tool `get_latency_stats` devuelve percentiles de latencia por span name.

- [ ] Leer `app/database/repository.py` métodos de tracing existentes (líneas ~1200-1360)
- [ ] Agregar `get_latency_percentiles(self, span_name: str | None, days: int = 7) -> list[dict]`
  en `repository.py`:
  ```python
  async def get_latency_percentiles(
      self, span_name: str | None = None, days: int = 7
  ) -> list[dict]:
      """Return p50/p95/p99 latency per span name for the last N days.

      If span_name is None, returns stats for the most frequent span names.
      Percentiles computed in Python (SQLite has no PERCENTILE_DISC).
      """
      if span_name:
          cursor = await self._conn.execute(
              """
              SELECT name, latency_ms FROM trace_spans
              WHERE name = ? AND latency_ms IS NOT NULL
                AND started_at >= datetime('now', ? || ' days')
              ORDER BY latency_ms ASC
              """,
              (span_name, f"-{days}"),
          )
          rows = await cursor.fetchall()
          if not rows:
              return []
          return [_compute_percentiles(span_name, [r[1] for r in rows])]
      else:
          # Top frequent span names
          cursor = await self._conn.execute(
              """
              SELECT name, COUNT(*) AS n FROM trace_spans
              WHERE latency_ms IS NOT NULL
                AND started_at >= datetime('now', ? || ' days')
              GROUP BY name
              ORDER BY n DESC
              LIMIT 10
              """,
              (f"-{days}",),
          )
          name_rows = await cursor.fetchall()
          results = []
          for (sname, _) in name_rows:
              cursor2 = await self._conn.execute(
                  """
                  SELECT latency_ms FROM trace_spans
                  WHERE name = ? AND latency_ms IS NOT NULL
                    AND started_at >= datetime('now', ? || ' days')
                  ORDER BY latency_ms ASC
                  """,
                  (sname, f"-{days}"),
              )
              lat_rows = await cursor2.fetchall()
              results.append(_compute_percentiles(sname, [r[0] for r in lat_rows]))
          return results
  ```
- [ ] Agregar helper `_compute_percentiles(name: str, sorted_values: list[float]) -> dict`
  como función module-level en `repository.py` (fuera de la clase):
  ```python
  def _compute_percentiles(name: str, sorted_values: list[float]) -> dict:
      def _pct(values: list[float], p: float) -> float:
          if not values:
              return 0.0
          idx = max(0, int(len(values) * p / 100) - 1)
          return round(values[idx], 1)

      return {
          "span": name,
          "n": len(sorted_values),
          "p50": _pct(sorted_values, 50),
          "p95": _pct(sorted_values, 95),
          "p99": _pct(sorted_values, 99),
          "max": round(sorted_values[-1], 1) if sorted_values else 0.0,
      }
  ```
- [ ] Leer `app/skills/tools/eval_tools.py` (inicio + sección de registro de tools)
- [ ] Agregar handler `get_latency_stats(span_name: str = "all", days: int = 7) -> str`
  en `eval_tools.py` dentro de `register()`:
  ```python
  async def get_latency_stats(span_name: str = "all", days: int = 7) -> str:
      """Return p50/p95/p99 latency stats per pipeline span for the last N days."""
      try:
          target = None if span_name == "all" else span_name
          stats = await repository.get_latency_percentiles(target, days=days)
      except Exception:
          logger.exception("get_latency_stats failed")
          return "Error retrieving latency stats."

      if not stats:
          return f"No latency data found for span='{span_name}' in the last {days} days."

      lines = [f"*Latencias p50/p95/p99 — últimos {days} días*", ""]
      for s in stats:
          lines.append(
              f"- `{s['span']}`: p50={s['p50']:.0f}ms  p95={s['p95']:.0f}ms  "
              f"p99={s['p99']:.0f}ms  max={s['max']:.0f}ms  (n={s['n']})"
          )
      return "\n".join(lines)
  ```
- [ ] Registrar la tool en la sección de `registry.register_tool(...)` al final de `register()`:
  ```python
  registry.register_tool(
      name="get_latency_stats",
      description="Return p50/p95/p99 latency for each pipeline span (classify_intent, embed, execute_tool_loop, guardrails, etc.)",
      parameters={
          "type": "object",
          "properties": {
              "span_name": {
                  "type": "string",
                  "description": "Span name to filter (default 'all' = all frequent spans)",
              },
              "days": {
                  "type": "integer",
                  "description": "Number of days to look back (default 7)",
              },
          },
      },
      handler=get_latency_stats,
      skill_name=_SKILL_NAME,
  )
  ```
- [ ] Tests en `tests/test_metrics_tools.py`:
  - [ ] `test_compute_percentiles_empty` — lista vacía → zeros
  - [ ] `test_compute_percentiles_single` — [100.0] → p50=p95=p99=100.0
  - [ ] `test_compute_percentiles_sorted` — [10, 50, 90, 100] → p50=50, p95~=100
  - [ ] `test_get_latency_stats_no_data` — mock repository vacío → mensaje descriptivo
  - [ ] `test_get_latency_stats_all` — mock repository con datos → formato correcto

---

### Phase 3: Semantic search hit rate

**Objetivo:** Trackear cuántos requests usan búsqueda semántica real vs fallback, y exponer
el agregado via tool.

- [ ] Leer `app/context/conversation_context.py` completo
- [ ] Agregar field `search_stats: dict = field(default_factory=dict)` al dataclass
  `ConversationContext` (después de `query_embedding`, antes de `token_estimate`)
- [ ] Refactorizar `_get_memories_with_threshold()` dentro de `ConversationContext.build()`
  para que retorne una tupla `(memories, stats)` en lugar de solo `memories`:
  ```python
  async def _get_memories_with_threshold(embedding) -> tuple[list[str], dict]:
      stats: dict = {"search_mode": "full_fallback", "retrieved": 0, "passed": 0, "returned": 0}
      if embedding is not None and settings is not None:
          try:
              results = await repository.search_similar_memories_with_distance(...)
              stats["retrieved"] = len(results)
              threshold = settings.memory_similarity_threshold
              passed = [c for c, d in results if d < threshold]
              stats["passed"] = len(passed)
              if not passed and results:
                  passed = [c for c, _ in results[:3]]
                  stats["search_mode"] = "fallback_threshold"
              else:
                  stats["search_mode"] = "semantic"
              stats["returned"] = len(passed)
              return passed, stats
          except Exception:
              logger.warning("semantic memory search failed, falling back", exc_info=True)
      top_k = settings.semantic_search_top_k if settings else 10
      memories = await repository.get_active_memories(limit=top_k)
      stats["returned"] = len(memories)
      return memories, stats
  ```
- [ ] Actualizar el `asyncio.gather` en `build()` para desempaquetar la tupla:
  ```python
  (memories_and_stats, windowed, sticky, logs, relevant_notes, projects_summary) = await asyncio.gather(
      _get_memories_with_threshold(query_embedding),
      ...
  )
  memories_raw, search_stats = memories_and_stats
  ```
- [ ] Pasar `search_stats=search_stats` al constructor `cls(...)` al final de `build()`
- [ ] Loguear los stats (best-effort) — agregar al final de `build()`:
  ```python
  logger.debug(
      "ConversationContext: search_stats=%s", search_stats,
      extra={"search_stats": search_stats, "phone": phone_number},
  )
  ```
- [ ] En `app/webhook/router.py`, en `_run_normal_flow()`, pushear `ctx.search_stats` al
  span de Phase B si existe (dentro del bloque de tracing, best-effort):
  ```python
  # Después de que se complete ConversationContext.build()
  trace_ctx = get_current_trace()
  if trace_ctx and ctx.search_stats:
      # Agregar como extra info en el log — no hay un span Phase B abierto en ese punto
      # porque build() ya corrió; usar structured log
      logger.info("context.search_stats", extra={"search_stats": ctx.search_stats})
  ```
  **Nota:** La alternativa de persistir en span metadata requeriría que `build()` tenga acceso
  al `trace_ctx`, lo que añadiría un parámetro. Por simplicidad, loguear es suficiente para esta
  fase. El `get_search_stats` tool agregará query sobre `trace_spans` con `json_extract` en
  un sprint futuro si se decide persistir en span metadata.
- [ ] Agregar `get_search_hit_rate(self, days: int = 7) -> dict` en `repository.py`:
  ```python
  async def get_search_hit_rate(self, days: int = 7) -> list[dict]:
      """Return distribution of semantic search modes from span metadata.

      Requires that search_stats were stored in trace_spans.metadata_json.
      Returns empty list if tracing is disabled or spans have no metadata.
      """
      cursor = await self._conn.execute(
          """
          SELECT
              json_extract(metadata_json, '$.search_mode') AS mode,
              COUNT(*) AS n,
              AVG(json_extract(metadata_json, '$.memories_retrieved')) AS avg_retrieved,
              AVG(json_extract(metadata_json, '$.memories_passed')) AS avg_passed
          FROM trace_spans
          WHERE name = 'phase_b'
            AND started_at >= datetime('now', ? || ' days')
            AND metadata_json IS NOT NULL
            AND json_extract(metadata_json, '$.search_mode') IS NOT NULL
          GROUP BY mode
          ORDER BY n DESC
          """,
          (f"-{days}",),
      )
      rows = await cursor.fetchall()
      return [
          {
              "mode": r[0],
              "n": r[1],
              "avg_retrieved": round(r[2], 1) if r[2] else 0.0,
              "avg_passed": round(r[3], 1) if r[3] else 0.0,
          }
          for r in rows
      ]
  ```
  **Nota:** Este método depende de que los search_stats estén en `metadata_json` del span
  `phase_b`. Si en `_run_normal_flow()` se decide solo loguear (no guardar en span), este
  método siempre retornará vacío. En ese caso la tool muestra un mensaje explicativo.
- [ ] Agregar handler `get_search_stats(days: int = 7) -> str` en `eval_tools.py`:
  ```python
  async def get_search_stats(days: int = 7) -> str:
      """Return distribution of semantic search modes (hit vs fallback) for the last N days."""
      try:
          stats = await repository.get_search_hit_rate(days=days)
      except Exception:
          logger.exception("get_search_stats failed")
          return "Error retrieving search stats."

      if not stats:
          return (
              f"No hay datos de búsqueda semántica en los últimos {days} días. "
              "Asegurate de que tracing_enabled=True y que los spans Phase B tengan metadata."
          )

      total = sum(s["n"] for s in stats)
      lines = [f"*Búsqueda semántica — últimos {days} días (n={total})*", ""]
      for s in stats:
          pct = s["n"] / total * 100 if total else 0
          lines.append(
              f"- `{s['mode']}`: {s['n']} requests ({pct:.0f}%)  "
              f"recuperadas={s['avg_retrieved']:.1f}  pasaron_threshold={s['avg_passed']:.1f}"
          )
      return "\n".join(lines)
  ```
- [ ] Registrar la tool `get_search_stats` en `eval_tools.py`
- [ ] Tests en `tests/test_metrics_tools.py`:
  - [ ] `test_search_stats_no_data` — repository vacío → mensaje descriptivo
  - [ ] `test_search_stats_formats_correctly` — mock con datos → porcentajes bien calculados
  - [ ] `test_conversation_context_search_stats_field_defaults_empty`

---

### Phase 4: Integración, tests y documentación

- [ ] Correr `make check` (lint + typecheck + tests) — target: 0 errores
- [ ] Verificar manualmente con un trace real:
  - [ ] Log `context.budget` incluye el breakdown (sistema + historial)
  - [ ] Tool `get_latency_stats()` desde WhatsApp retorna datos reales
  - [ ] Tool `get_search_stats()` retorna datos o mensaje explicativo apropiado
- [ ] Actualizar `docs/exec-plans/README.md` con entrada del plan 38
- [ ] Actualizar `docs/features/37-metricas_benchmarking.md` — marcar gaps 1-3 como resueltos
- [ ] Actualizar `CLAUDE.md` si hay patrones nuevos que preservar

---

## Diagrama de flujo post-implementación

```
_run_normal_flow()
  │
  ├── ConversationContext.build()
  │     └── _get_memories_with_threshold()
  │           → retorna (memories, search_stats)  # NEW: search_stats dict
  │           → logger.debug("context.search_stats", ...)
  │
  ├── _build_context(...)  → context: list[ChatMessage]
  │
  ├── # Token breakdown (best-effort, try/except)
  │   estimate_sections({"system_prompt": ..., "history": ...})
  │   log_context_budget_breakdown(sections)          # NEW: breakdown log
  │   log_context_budget(context, extra={...})        # existing: total log
  │
  └── execute_tool_loop / ollama.chat()
        └── [spans existentes — sin cambios]

eval_tools.register()
  ├── get_latency_stats(span_name, days)   # NEW
  └── get_search_stats(days)              # NEW

repository.py
  ├── get_latency_percentiles(span_name, days)  # NEW
  └── get_search_hit_rate(days)                 # NEW
```

---

## Verificación de compleción

```bash
# 1. Tests
make test

# 2. Lint + typecheck
make check

# 3. Verificar breakdown en logs (requiere mensajes reales)
tail -f logs/app.json | grep "token_breakdown"

# 4. Verificar tools desde terminal (simulando call)
python -c "
import asyncio
from app.database.db import init_db
from app.database.repository import Repository

async def main():
    conn, _ = await init_db('data/localforge.db')
    repo = Repository(conn)
    stats = await repo.get_latency_percentiles(None, days=7)
    print(stats)
    await conn.close()

asyncio.run(main())
"
```
