# PRP: Eval Stack Hardening (Plan 30)

## Archivos a Modificar

| Archivo | Cambio |
|---------|--------|
| `app/config.py` | Agregar `guardrails_llm_timeout: float = 3.0` |
| `app/guardrails/pipeline.py` | Pasar `timeout` desde settings en `_run_async_check()`; agregar `failed_checks` en metadata del span |
| `app/llm/client.py` | Agregar `think: bool | None = None` a `chat()` — propaga a `chat_with_tools()` |
| `app/webhook/router.py` | Fix `_handle_guardrail_failure()` — prompt bilingüe + span Langfuse; pasar `trace_ctx` + `failed_checks_for_curation` a curation |
| `app/eval/dataset.py` | Agregar parámetro `failed_check_names: list[str] | None = None`; pasar `tags` a `add_dataset_entry()` |
| `app/skills/tools/eval_tools.py` | Reemplazar word overlap por LLM-as-judge en `run_quick_eval()` |
| `scripts/run_eval.py` | **Nuevo** — CLI de benchmark offline |
| `tests/test_guardrails.py` | **Nuevo** — Tests para remediation bilingüe + timeout configurable |
| `tests/test_eval_tools.py` | **Nuevo** — Test del nuevo LLM-as-judge en `run_quick_eval` |

---

## Fases de Implementación

### Phase 1: Fix `language_match` remediation + Langfuse span

**Objetivo:** Que la llamada LLM de remediation sea efectiva Y visible en Langfuse.

- [x] Leer `_handle_guardrail_failure()` completo en `app/webhook/router.py` (líneas 504–580)
- [x] Cambiar el hint de `language_match` a prompt bilingüe:
  ```python
  hint_content = (
      f"IMPORTANTE: El usuario escribió en {lang_name}. "
      f"Reescribe la respuesta SOLO en {lang_name}, sin cambiar el contenido.\n"
      f"IMPORTANT: The user wrote in {lang_name}. "
      f"Rewrite the response ONLY in {lang_name}, do not change the content."
  )
  ```
- [x] Agregar parámetro `trace_ctx=None` a `_handle_guardrail_failure()` (sin romper call sites)
- [x] Wrap la llamada LLM de remediation `language_match` en span Langfuse:
  ```python
  if trace_ctx:
      async with trace_ctx.span("guardrails:remediation", kind="generation") as span:
          span.set_metadata({"check": "language_match", "lang_code": detected_code})
          retry = await ollama_client.chat(context + [hint_msg])
  else:
      retry = await ollama_client.chat(context + [hint_msg])
  ```
- [x] Actualizar call site en `router.py` para pasar `trace_ctx=trace_ctx` a `_handle_guardrail_failure()`
- [x] Agregar `failed_checks` en metadata del span `guardrails` existente
- [x] Tests en `tests/test_guardrails.py`:
  - [x] `test_language_remediation_prompt_is_bilingual`
  - [x] `test_language_remediation_unknown_lang_uses_bilingual_generic`
  - [x] `test_language_remediation_creates_span_when_trace_ctx_provided`
  - [x] `test_language_remediation_no_span_without_trace_ctx`
  - [x] `test_language_match_skip_short_text`

---

### Phase 2: Timeout configurable para LLM judges

**Objetivo:** Que `guardrails_llm_checks=True` realmente funcione con qwen3.5:9b local.

- [x] Agregar en `app/config.py`:
  ```python
  guardrails_llm_timeout: float = 3.0  # segundos; 0.5 era demasiado bajo para qwen3.5:9b
  ```
- [x] Modificar `run_guardrails()` en `pipeline.py`:
  ```python
  llm_timeout = getattr(settings, "guardrails_llm_timeout", 3.0) if settings else 3.0
  await _run_async_check(..., timeout=llm_timeout)
  ```
- [x] Test `test_guardrails_llm_timeout_from_settings`

---

### Phase 3: LLM-as-judge en `run_quick_eval`

**Objetivo:** Métrica semánticamente correcta.

- [x] Agregar `think: bool | None = None` a `OllamaClient.chat()` en `app/llm/client.py`
- [x] Reemplazar el bloque de word overlap en `eval_tools.py` por LLM-as-judge:
  ```python
  judge_resp = await ollama_client.chat(
      [ChatMessage(role="user", content=judge_prompt)],
      think=False,
  )
  passed = str(judge_resp).strip().lower().startswith("yes")
  ```
- [x] Output: `"Correct: X/Y (Z%)"` + iconos ✅/❌ por entrada
- [x] Tests en `tests/test_eval_tools.py`:
  - [x] `test_run_quick_eval_uses_llm_judge_yes`
  - [x] `test_run_quick_eval_uses_llm_judge_no`
  - [x] `test_run_quick_eval_judge_uses_think_false`
  - [x] `test_run_quick_eval_skips_entries_without_expected_output`
  - [x] `test_run_quick_eval_no_entries_returns_helpful_message`

---

### Phase 4: Tags automáticos en auto-curation

**Objetivo:** `eval_dataset_tags` poblada con `guardrail:{check_name}` para filtrar por causa.

- [x] Verificar `add_dataset_tags()` en `repository.py` — ya existe; `add_dataset_entry()` ya acepta `tags` y retorna `int`
- [x] Modificar firma de `maybe_curate_to_dataset()` en `dataset.py`:
  ```python
  async def maybe_curate_to_dataset(
      ...
      failed_check_names: list[str] | None = None,
  ) -> None:
  ```
- [x] Pasar `tags=[f"guardrail:{name}" for name in failed_check_names]` en tier "Failure"
- [x] Inicializar `failed_checks_for_curation: list[str] = []` antes del bloque guardrails en `router.py`
- [x] Propagar `failed_checks_for_curation` desde el bloque `if not guardrail_report.passed` al call site de `maybe_curate_to_dataset`

---

### Phase 5: Script de benchmark offline

**Objetivo:** `python scripts/run_eval.py` corre el dataset sin levantar el servidor.

- [x] Crear `scripts/run_eval.py`:
  - Args: `--db`, `--ollama`, `--model`, `--entry-type`, `--limit`, `--threshold`
  - `asyncio.run(main())` entry point
  - Mismo prompt LLM-as-judge que Phase 3
  - Tabla de resultados con entrada por fila
  - Resumen por tipo de entrada (correction/golden/failure)
  - Exit code 0 si accuracy >= threshold, 1 si below, 2 si no hay entradas evaluables
- [x] Verificar que el script NO importa `app.main` ni `FastAPI`

---

### Phase 6: Tests, lint y documentación

- [x] Correr `make check` (lint + typecheck + tests): **485 passed, lint clean, mypy clean**
- [x] Actualizar `docs/exec-plans/README.md` con entrada plan 30
- [x] Crear `docs/features/30-eval_hardening.md`
- [x] Crear `docs/testing/30-eval_hardening_testing.md`
- [x] Actualizar `CLAUDE.md` con patrones nuevos

---

## Diagrama de flujo post-fix (guardrails)

```
_handle_message()
  └── _run_normal_flow()
        └── run_guardrails()                    ← span: "guardrails" {passed, latency_ms, failed_checks}
              ├── check_not_empty()             ← sync, determinístico
              ├── check_language_match()        ← sync, determinístico
              ├── check_no_pii()               ← sync, determinístico
              └── [opt] check_tool_coherence() ← async, timeout=guardrails_llm_timeout (3.0s)
                   check_hallucination()

        if not guardrail_report.passed:
          _handle_guardrail_failure(trace_ctx=trace_ctx)
            └── [language_match] span: "guardrails:remediation" {lang_code, hint}
                  └── ollama_client.chat()      ← LLM call visible en Langfuse

        for gr in guardrail_report.results:
          trace_ctx.add_score(name=gr.check_name, value=1.0/0.0)

        if not guardrail_report.passed:
          maybe_curate_to_dataset(failed_check_names=["language_match"])
            └── add_dataset_entry(tags=["guardrail:language_match"])
```

---

## Verificación de compleción

```bash
# 1. Tests — 485 passed ✅
PYTHONPATH=... python3.12 -m pytest tests/ --ignore=test_tracing.py --ignore=test_agent_tracing.py

# 2. Lint — all checks passed ✅
ruff check app/ scripts/run_eval.py tests/test_guardrails.py tests/test_eval_tools.py

# 3. Typecheck — Success: no issues in 102 source files ✅
python3.12 -m mypy app/

# 4. Benchmark offline
python scripts/run_eval.py --db data/localforge.db --ollama http://localhost:11434

# 5. Verificar tags en dataset
sqlite3 data/localforge.db "SELECT tag, count(*) FROM eval_dataset_tags GROUP BY tag ORDER BY count(*) DESC;"

# 6. Verificar timeout configurable
python3.12 -c "from app.config import Settings; s = Settings(...); print(s.guardrails_llm_timeout)"
# → 3.0
```
