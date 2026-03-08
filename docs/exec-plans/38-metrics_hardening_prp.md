# PRP: Metrics Hardening (Plan 38)

## Archivos a Modificar

| Archivo | Cambio |
|---------|--------|
| `app/context/token_estimator.py` | Agregar `estimate_sections()` y `log_context_budget_breakdown()` |
| `app/webhook/router.py` | Llamar breakdown en `_run_normal_flow()`, pushear tags de categorías al recorder |
| `app/context/conversation_context.py` | Agregar field `search_stats: dict` + popularlo en `_get_memories_with_threshold()` |
| `app/database/repository.py` | Agregar `get_latency_percentiles()` y `get_search_hit_rate()` |
| `app/skills/tools/eval_tools.py` | Agregar tools `get_latency_stats` y `get_search_stats` |
| `app/tracing/recorder.py` | Agregar `session_id` en `start_trace()`, `update_trace_tags()`, `sync_dataset_to_langfuse()` |
| `app/eval/dataset.py` | Llamar `sync_dataset_to_langfuse()` desde `maybe_curate_to_dataset()` |
| `scripts/dashboard.py` | **Nuevo** — dashboard HTML offline |
| `tests/test_token_estimator.py` | **Nuevo** — tests para breakdown |
| `tests/test_metrics_tools.py` | **Nuevo** — tests para las dos tools nuevas |
| `tests/test_langfuse_enrichment.py` | **Nuevo** — tests para session_id, tags, dataset sync |

---

## Fases de Implementación

### Phase 1: Token budget breakdown

**Objetivo:** Que el log `context.budget` incluya desglose por sección y que ese desglose
aparezca en los spans de tracing.

- [x] Leer `app/context/token_estimator.py` completo
- [x] Agregar función `estimate_sections(sections: dict[str, str | None]) -> dict[str, int]`:
  ```python
  def estimate_sections(sections: dict[str, str | None]) -> dict[str, int]:
      """Compute token estimate per named section. None/empty sections count as 0."""
      return {name: estimate_tokens(text) if text else 0 for name, text in sections.items()}
  ```
- [x] Agregar función `log_context_budget_breakdown(sections: dict[str, int], context_limit: int = _CONTEXT_LIMIT) -> None`:
  - Calcular `total = sum(sections.values())`
  - Identificar `largest_section = max(sections, key=sections.get)`
  - Emitir un único log INFO con `extra={"token_breakdown": sections, "largest_section": largest_section, "total": total}`
  - NO emitir en WARNING/ERROR — eso ya lo hace `log_context_budget`
- [x] Leer `app/webhook/router.py` función `_run_normal_flow()` (lines ~1280-1340)
- [x] En `_run_normal_flow()`, después de llamar `_build_context()` y ANTES de `log_context_budget()`,
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
- [x] Pushear el breakdown al span de tracing activo (best-effort, dentro del mismo `try`):
  **Decisión:** Usando opción A — solo log estructurado. El breakdown se computa fuera de cualquier
  span activo, y el log JSON es suficientemente consultable.
- [x] Tests en `tests/test_token_estimator.py`:
  - [x] `test_estimate_sections_basic` — dict de strings → dict de ints proporcionales
  - [x] `test_estimate_sections_none_values` — None sections → 0
  - [x] `test_estimate_sections_empty_string` — "" → 0
  - [x] `test_log_context_budget_breakdown_emits_info` — mock logger, verificar extra keys

---

### Phase 2: Latencia p50/p95 por operación

**Objetivo:** Tool `get_latency_stats` devuelve percentiles de latencia por span name.

- [x] Leer `app/database/repository.py` métodos de tracing existentes (líneas ~1200-1360)
- [x] Agregar `get_latency_percentiles(self, span_name: str | None, days: int = 7) -> list[dict]`
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
- [x] Agregar helper `_compute_percentiles(name: str, sorted_values: list[float]) -> dict`
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
- [x] Leer `app/skills/tools/eval_tools.py` (inicio + sección de registro de tools)
- [x] Agregar handler `get_latency_stats(span_name: str = "all", days: int = 7) -> str`
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
- [x] Registrar la tool en la sección de `registry.register_tool(...)` al final de `register()`:
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
- [x] Tests en `tests/test_metrics_tools.py`:
  - [x] `test_compute_percentiles_empty` — lista vacía → zeros
  - [x] `test_compute_percentiles_single` — [100.0] → p50=p95=p99=100.0
  - [x] `test_compute_percentiles_sorted` — [10, 50, 90, 100] → p50=50, p95~=100
  - [x] `test_get_latency_stats_no_data` — mock repository vacío → mensaje descriptivo
  - [x] `test_get_latency_stats_all` — mock repository con datos → formato correcto

---

### Phase 3: Semantic search hit rate

**Objetivo:** Trackear cuántos requests usan búsqueda semántica real vs fallback, y exponer
el agregado via tool.

- [x] Leer `app/context/conversation_context.py` completo
- [x] Agregar field `search_stats: dict = field(default_factory=dict)` al dataclass
  `ConversationContext` (después de `query_embedding`, antes de `token_estimate`)
- [x] Refactorizar `_get_memories_with_threshold()` dentro de `ConversationContext.build()`
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
- [x] Actualizar el `asyncio.gather` en `build()` para desempaquetar la tupla:
  ```python
  (memories_and_stats, windowed, sticky, logs, relevant_notes, projects_summary) = await asyncio.gather(
      _get_memories_with_threshold(query_embedding),
      ...
  )
  memories_raw, search_stats = memories_and_stats
  ```
- [x] Pasar `search_stats=search_stats` al constructor `cls(...)` al final de `build()`
- [x] Loguear los stats (best-effort) — agregar al final de `build()`:
  ```python
  logger.debug(
      "ConversationContext: search_stats=%s", search_stats,
      extra={"search_stats": search_stats, "phone": phone_number},
  )
  ```
- [x] En `app/webhook/router.py`, en `_run_normal_flow()`, pushear `ctx.search_stats` al
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
- [x] Agregar `get_search_hit_rate(self, days: int = 7) -> dict` en `repository.py`:
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
- [x] Agregar handler `get_search_stats(days: int = 7) -> str` en `eval_tools.py`:
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
- [x] Registrar la tool `get_search_stats` en `eval_tools.py`
- [x] Tests en `tests/test_metrics_tools.py`:
  - [x] `test_search_stats_no_data` — repository vacío → mensaje descriptivo
  - [x] `test_search_stats_formats_correctly` — mock con datos → porcentajes bien calculados
  - [x] `test_conversation_context_search_stats_field_defaults_empty`

---

### Phase 5: Dashboard HTML offline

**Objetivo:** `python scripts/dashboard.py` genera un HTML autocontenido con métricas completas
del sistema, con links a Langfuse por trace_id.

- [x] Leer `app/database/repository.py` métodos: `get_eval_summary`, `get_failure_trend`, `get_score_distribution`, `get_dataset_stats`, `get_latency_percentiles` (ya implementado en Phase 2)
- [x] Leer `app/config.py` para obtener `langfuse_host` desde settings
- [x] Crear `scripts/dashboard.py`:
  - Args: `--db` (default `data/localforge.db`), `--output` (default `reports/dashboard.html`), `--days` (default 30), `--ollama` (no usado, solo para compatibilidad con run_eval)
  - Entry point: `asyncio.run(main())` sin importar FastAPI
  - Secciones del HTML:
    1. **Summary cards**: total trazas, tasa de éxito (%), fallos, tamaño del dataset
    2. **Guardrail pass rates**: tabla `check | pass_rate | n` ordenada por pass_rate asc
    3. **Failure trend**: tabla `día | total | fallos | %fallos` + chart de línea (Chart.js)
    4. **Latencias p50/p95/p99**: tabla por span name (reutiliza `get_latency_percentiles`)
    5. **Dataset composition**: tabla `type | count | %`
    6. **Recent failures**: tabla con `trace_id` (link a Langfuse si `langfuse_host` config), `input_preview`, `min_score`, `failed_checks`
  - HTML autocontenido: CSS inline, Chart.js desde CDN, datos embebidos como JS variables
  - Si `langfuse_host` está en env: cada `trace_id` → `<a href="{host}/trace/{id}">{id[:12]}</a>`
  - Crear directorio `reports/` si no existe (`.gitignore` ya debe ignorarlo)

  ```python
  # Estructura del script
  async def _fetch_all_data(db_path, days) -> dict:
      conn, _ = await init_db(db_path)
      repo = Repository(conn)
      data = {
          "summary": await repo.get_eval_summary(days),
          "trend": await repo.get_failure_trend(days),
          "scores": await repo.get_score_distribution(),
          "dataset": await repo.get_dataset_stats(),
          "latencies": await repo.get_latency_percentiles(None, days),
          "failures": await repo.get_failed_traces(limit=20),
      }
      await conn.close()
      return data

  def _render_html(data: dict, days: int, langfuse_host: str | None) -> str:
      # Retorna string HTML completo con datos embebidos como JSON en <script>
      ...

  def main():
      args = _parse_args()
      data = asyncio.run(_fetch_all_data(args.db, args.days))
      langfuse_host = os.getenv("LANGFUSE_HOST")
      html = _render_html(data, args.days, langfuse_host)
      Path(args.output).parent.mkdir(parents=True, exist_ok=True)
      Path(args.output).write_text(html)
      print(f"Dashboard generado: {args.output}")
  ```

- [x] Agregar `reports/` a `.gitignore` si no está
- [x] Verificar que el script corre sin FastAPI: `python scripts/dashboard.py --db data/localforge.db`

---

### Phase 6: Langfuse enrichment

**Objetivo:** Aprovechar session_id, tags de categorías, platform tag, y dataset sync para
que Langfuse sea útil como herramienta de análisis y no solo de logging.

#### 6a — session_id y platform tag en start_trace

- [x] Leer `app/tracing/recorder.py` completo (ya leído)
- [x] Modificar `start_trace()` para aceptar `platform: str = "whatsapp"`:
  ```python
  async def start_trace(
      self,
      trace_id: str,
      phone_number: str,
      input_text: str,
      message_type: str = "text",
      platform: str = "whatsapp",
  ) -> None:
      ...
      if self.langfuse:
          self.langfuse.trace(
              id=trace_id,
              name="interaction",
              user_id=phone_number,
              session_id=phone_number,          # NEW: agrupa por usuario en Langfuse Sessions
              input=input_text,
              metadata={"message_type": message_type, "platform": platform},  # NEW: platform
          )
  ```
- [x] Actualizar call sites en `router.py` donde se crea `TraceContext` para pasar `platform`:
  - Detectar platform desde `phone_number.startswith("tg_")` o desde `IncomingMessage.platform`
  - Pasar al `TraceContext.__init__()` → `recorder.start_trace()` (requiere agregar `platform` param a `TraceContext`)

#### 6b — update_trace_tags (categorías de intent)

- [x] Agregar método `update_trace_tags(self, trace_id: str, tags: list[str]) -> None` en `recorder.py`:
  ```python
  async def update_trace_tags(self, trace_id: str, tags: list[str]) -> None:
      """Upsert tags on an existing Langfuse trace. Best-effort, no-op if no Langfuse."""
      if not self.langfuse or not tags:
          return
      try:
          self.langfuse.trace(id=trace_id, tags=tags)
      except Exception:
          logger.warning("TraceRecorder.update_trace_tags failed", exc_info=True)
  ```
- [x] En `_run_normal_flow()` en `router.py`, después de resolver `pre_classified` (categorías finales),
  llamar best-effort:
  ```python
  if trace_ctx and pre_classified and pre_classified != ["none"]:
      recorder = get_trace_recorder(request)  # ya disponible en scope
      status_tag = "completed"  # se actualizará en finish_trace
      platform_tag = "telegram" if msg.user_id.startswith("tg_") else "whatsapp"
      await recorder.update_trace_tags(
          trace_ctx.trace_id,
          [platform_tag] + pre_classified,
      )
  ```
  **Nota:** `finish_trace` actualmente ya envía `tags=[status]`. Langfuse hace merge de tags
  en upserts, así que `["math", "time"]` + `["completed"]` = `["math", "time", "completed"]`.

#### 6c — Dataset sync a Langfuse Datasets

- [x] Agregar método `sync_dataset_to_langfuse(self, dataset_name, input_text, expected_output, metadata) -> None` en `recorder.py`:
  ```python
  async def sync_dataset_to_langfuse(
      self,
      dataset_name: str,
      input_text: str,
      expected_output: str | None,
      metadata: dict | None = None,
  ) -> None:
      """Push a dataset entry to Langfuse Datasets. Best-effort."""
      if not self.langfuse:
          return
      try:
          self.langfuse.create_dataset_item(
              dataset_name=dataset_name,
              input={"text": input_text},
              expected_output={"text": expected_output} if expected_output else None,
              metadata=metadata or {},
          )
      except Exception:
          logger.warning("TraceRecorder.sync_dataset_to_langfuse failed", exc_info=True)
  ```
- [x] Modificar `maybe_curate_to_dataset()` en `app/eval/dataset.py` para aceptar `trace_recorder=None`:
  ```python
  async def maybe_curate_to_dataset(
      trace_id, input_text, output_text, repository,
      failed_check_names=None,
      trace_recorder=None,   # NEW: TraceRecorder | None
  ) -> None:
  ```
- [x] Dentro del tier `golden` (confirmed=True), después del `add_dataset_entry`, añadir:
  ```python
  if trace_recorder:
      await trace_recorder.sync_dataset_to_langfuse(
          dataset_name="localforge-eval",
          input_text=input_text,
          expected_output=output_text,
          metadata={"entry_type": "golden", "trace_id": trace_id, "confirmed": True},
      )
  ```
- [x] Dentro del tier `correction`, idem con `entry_type="correction"` y `expected_output=correction_text`
- [x] Actualizar call site en `router.py` para pasar `trace_recorder=recorder`
- [x] Tests en `tests/test_langfuse_enrichment.py`:
  - [x] `test_start_trace_sends_session_id` — mock langfuse, verificar `session_id=phone`
  - [x] `test_update_trace_tags_called_after_classify` — mock recorder, verificar tags
  - [x] `test_update_trace_tags_noop_without_langfuse` — sin langfuse client → no error
  - [x] `test_sync_dataset_golden_to_langfuse` — mock langfuse, verificar `create_dataset_item`
  - [x] `test_sync_dataset_skips_failure_entries` — failures no se sincronizan
  - [x] `test_sync_dataset_noop_without_langfuse` — best-effort

---

### Phase 4: Integración, tests y documentación

- [x] Correr `make check` (lint + typecheck + tests) — target: 0 errores
- [x] Verificar manualmente con un trace real:
  - [x] Log `context.budget` incluye el breakdown (sistema + historial)
  - [x] Tool `get_latency_stats()` desde WhatsApp retorna datos reales
  - [x] Tool `get_search_stats()` retorna datos o mensaje explicativo apropiado
- [x] Actualizar `docs/exec-plans/README.md` con entrada del plan 38
- [x] Actualizar `docs/features/37-metricas_benchmarking.md` — marcar gaps 1-3 como resueltos
- [x] Actualizar `CLAUDE.md` si hay patrones nuevos que preservar

---

## Diagrama de flujo post-implementación

```
process_message_generic(msg, platform_client)
  │
  └── TraceContext(phone, text, recorder, platform=platform)   # NEW: platform param
        │
        └── recorder.start_trace(..., session_id=phone, platform=platform)
              ├── SQLite: save_trace(...)
              └── Langfuse: trace(session_id=phone, metadata={platform})  # NEW

_run_normal_flow()
  │
  ├── ConversationContext.build()
  │     └── _get_memories_with_threshold()
  │           → retorna (memories, search_stats)       # NEW: tuple
  │           → logger.debug("context.search_stats")
  │
  ├── classify_intent() → pre_classified
  │     └── recorder.update_trace_tags(trace_id, [platform] + pre_classified)  # NEW
  │           └── Langfuse: trace(id=trace_id, tags=["whatsapp","math","time"])
  │
  ├── _build_context(...)  → context: list[ChatMessage]
  │
  ├── # Token breakdown (best-effort)                          # NEW
  │   estimate_sections({...}) → log_context_budget_breakdown(sections)
  │
  ├── execute_tool_loop / ollama.chat()
  │     └── [spans existentes sin cambios]
  │
  └── maybe_curate_to_dataset(..., trace_recorder=recorder)   # NEW: param
        ├── SQLite: add_dataset_entry(...)
        └── [si golden/correction] recorder.sync_dataset_to_langfuse(
                dataset_name="localforge-eval", ...)           # NEW
                  └── Langfuse: create_dataset_item(...)

scripts/dashboard.py                                           # NEW
  └── asyncio.run(_fetch_all_data(db))
        → _render_html(data, langfuse_host)
        → reports/dashboard.html
              ├── Summary cards
              ├── Guardrail pass rates (table)
              ├── Failure trend (Chart.js line)
              ├── Latencies p50/p95/p99 (table)
              ├── Dataset composition (table)
              └── Recent failures (table con links a Langfuse)

eval_tools.register()
  ├── get_latency_stats(span_name, days)   # NEW (Phase 2)
  └── get_search_stats(days)              # NEW (Phase 3)
```

---

## Verificación de compleción

```bash
# 1. Tests + lint + typecheck
make check

# 2. Verificar breakdown en logs (requiere mensajes reales en dev)
tail -f logs/app.json | jq 'select(.message == "context.budget.breakdown")'

# 3. Verificar latency tool desde terminal
python -c "
import asyncio
from app.database.db import init_db
from app.database.repository import Repository

async def main():
    conn, _ = await init_db('data/localforge.db')
    repo = Repository(conn)
    print(await repo.get_latency_percentiles(None, days=7))
    await conn.close()

asyncio.run(main())
"

# 4. Dashboard script
python scripts/dashboard.py --db data/localforge.db --output /tmp/dashboard.html --days 30
# → Abrir /tmp/dashboard.html en browser y verificar que todas las secciones renderizan

# 5. Verificar session_id en Langfuse
# → Langfuse UI → Sessions → debe aparecer el phone_number como session

# 6. Verificar category tags en Langfuse
# → Langfuse UI → Traces → Filter by Tag → "math" debe filtrar trazas de cálculo

# 7. Verificar dataset sync en Langfuse
# → Langfuse UI → Datasets → "localforge-eval" debe aparecer con entries golden/correction
# → Si no hay entries aún: add_to_dataset() desde WhatsApp + verificar sync
```
