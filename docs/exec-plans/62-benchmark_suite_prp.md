# PRP: Benchmark Suite Expansion (Plan 62)

## Archivos a Modificar

| Archivo | Cambio |
|---------|--------|
| `scripts/run_eval.py` | Nuevos modos: `memory`, `plan` |
| `scripts/seed_eval_dataset.py` | ~38 nuevos cases (language, tool hallucination, remediation, agent) |
| `scripts/context_saturation_analysis.py` | **Nuevo** — correlación fill rate vs quality |
| `app/main.py` | APScheduler job para eval nightly |
| `app/config.py` | Settings: `eval_scheduled_enabled`, `eval_scheduled_hour`, `eval_alert_threshold` |
| `Makefile` | Targets: `eval-memory`, `eval-plan`, `eval-all` |
| `docs/features/62-benchmark_suite.md` | Feature doc |
| `docs/testing/62-benchmark_suite_testing.md` | Testing doc |
| `tests/test_eval_memory.py` | Tests del memory benchmark |
| `tests/test_eval_plan.py` | Tests del plan benchmark |
| `tests/test_scheduled_eval.py` | Tests del scheduled eval job |

## Fases de Implementación

### Phase 1: Seed Dataset Expansion (~38 cases)
- [x] 10 cases `section:language` — multi-turn conversations que mezclan idiomas
- [x] 10 cases `section:tool_hallucination` — queries donde NO hay tool apropiado
- [x] 8 cases `section:remediation` — responses que desafían guardrails
- [x] 10 cases `section:agent` — objectives complejos con expected_plan metadata
- [x] Marcar `eval_types` apropiados para cada case

### Phase 2: Memory Retrieval Benchmark
- [x] Crear `_run_memory()` en `run_eval.py`: embed query → search_similar_memories → P@5 + Recall
- [x] Argparse: agregar `"memory"` a choices
- [x] Filter: entries necesitan `expected_memory_keywords`
- [x] `Makefile`: `eval-memory`
- [x] Tests (`tests/test_eval_memory.py`)

### Phase 3: Agent Plan Benchmark
- [x] Crear `_run_plan()` en `run_eval.py`: create_plan → deterministic + LLM judge
- [x] Argparse: agregar `"plan"` a choices
- [x] Filter: entries necesitan `expected_plan_tasks` o `expected_plan_categories`
- [x] `Makefile`: `eval-plan`
- [x] Tests (`tests/test_eval_plan.py`)

### Phase 4: Context Saturation Analysis
- [x] `scripts/context_saturation_analysis.py`: bucket analysis + inflection detection
- [x] `Makefile`: `eval-saturation`

### Phase 5: Scheduled Eval
- [x] `app/config.py`: `eval_scheduled_enabled`, `eval_scheduled_hour`, `eval_scheduled_threshold`, `eval_scheduled_mode`
- [x] `app/main.py`: APScheduler cron job + WhatsApp alert on failure
- [x] `.env.example`: documentar settings
- [x] Tests (`tests/test_scheduled_eval.py`)

### Phase 6: Makefile & CI Integration
- [x] `eval-memory`: `--mode memory --threshold 0.6`
- [x] `eval-plan`: `--mode plan --threshold 0.5`
- [x] `eval-saturation`: `python scripts/context_saturation_analysis.py`
- [x] `eval-all`: seed + classify + tools + e2e + guardrails + memory

### Phase 7: Documentación
- [x] Crear `docs/features/62-benchmark_suite.md`
- [x] Crear `docs/testing/62-benchmark_suite_testing.md`
- [x] Actualizar `docs/features/README.md` y `docs/testing/README.md`
- [x] `make check` (lint + typecheck + tests) — 931 passed
