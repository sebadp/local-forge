# PRP: Regression Eval Suite — LLM-as-Judge Enhancement (Plan 49)

## Archivos a Modificar

- `scripts/run_eval.py`: Rewrite `_run_e2e()`, nuevo judge system, verbose output, timing (~180 líneas)
- `scripts/seed_eval_dataset.py`: Fix 1 entry (news category) (~3 líneas)
- `app/skills/router.py`: Agregar 8 ejemplos al classifier prompt (~10 líneas)
- `docs/exec-plans/49-regression_eval_suite_prd.md`: PRD (nuevo)
- `docs/exec-plans/49-regression_eval_suite_prp.md`: PRP (nuevo)
- `docs/research/llm_as_judge_best_practices.md`: Artículo de investigación (nuevo)
- `docs/exec-plans/README.md`: Agregar entrada del Plan 49

## Fases de Implementación

### Phase 0: Documentación
- [x] Crear `docs/exec-plans/49-regression_eval_suite_prd.md`
- [x] Crear `docs/exec-plans/49-regression_eval_suite_prp.md`
- [x] Crear `docs/research/llm_as_judge_best_practices.md`
- [x] Actualizar `docs/exec-plans/README.md` con Plan 49

### Phase 1: Quick Wins (Classifier + Dataset)
- [x] Fix `seed_eval_dataset.py:78`: `expected_categories=["search"]` -> `["news"]`, `expected_tools=["web_search"]` -> `["search_news"]`
- [x] Agregar 8 ejemplos al `_CLASSIFIER_PROMPT_TEMPLATE` en `router.py`:
  - 4 selfcode: "configuracion del runtime", "salud del sistema", "logs de error", "outline de un archivo"
  - 2 projects: "marca como hecha la tarea 1", "borra la tarea 2"
  - 1 math: "sin(pi/2)"
  - 1 news: "noticias sobre inteligencia artificial"

### Phase 2: E2E Architecture Fix
- [x] Agregar `import time` a `run_eval.py`
- [x] Rewrite `_run_e2e()`:
  - Usar `_build_eval_tools_map()` para obtener tool schemas
  - Bifurcar: `needs_tools` -> `chat_with_tools()`, else -> `client.chat()`
  - Capturar `tool_calls_made` del response
  - Agregar timing con `time.monotonic()`
  - Pasar todo a `_judge_response()`
  - Incluir `actual_response`, `judge_reasoning`, `latency_ms`, `tool_calls` en result dict

### Phase 3: Enhanced Judge (QAG Multi-Criteria)
- [x] Crear constantes `_JUDGE_PROMPT`, `_JUDGE_TOOL_CRITERION`, `_JUDGE_TOOL_FORMAT`
- [x] Implementar `_judge_response(client, input_text, expected, actual, meta, tool_calls_made) -> dict`
  - Build tool_section condicionalmente
  - Llamar `client.chat(think=False)` con el prompt formateado
  - Parsear con `_parse_judge_response()`
- [x] Implementar `_parse_judge_response(raw, has_tools) -> dict`
  - Parsear líneas "1. YES/NO - reason"
  - Extraer VERDICT
  - Computar score como promedio de criterios
  - Fallback: `verdict = score >= 0.5` si parsing falla
- [x] Renombrar viejo `_build_judge_prompt()` -> `_build_judge_prompt_simple()` (backward compat)

### Phase 4: Verbose Output + Diagnostics
- [x] Agregar `--verbose` / `-v` flag al argparser
- [x] Thread `verbose` a través de `_run_eval()` -> `_print_results()`
- [x] En `_print_results()` con verbose:
  - Mostrar `actual_response` (truncado 150 chars)
  - Mostrar `judge_reasoning`
  - Mostrar `tool_calls` list
  - Mostrar `latency_ms`
- [x] Agregar latencia agregada al summary: avg, max, total
- [x] Agregar timing a `_run_classify()` y `_run_tools()`
- [x] Enhanced Langfuse sync: scores por criterio (`correctness`, `completeness`, `tool_usage`)

### Phase 5: Validación Final
- [x] `make lint` pass
- [x] `make typecheck` pass
- [ ] Re-seed y correr eval completo: `make eval`
- [ ] Verificar que classify >= 90% y e2e mejore significativamente
