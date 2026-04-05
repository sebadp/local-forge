# Benchmark Suite Expansion (Plan 62)

## Qué hace

Extiende el regression eval suite con nuevas dimensiones de evaluación, dataset expandido y ejecución automática programada.

## Nuevos modos de evaluación

### Memory Retrieval (`--mode memory`)

Evalúa la calidad de búsqueda semántica en memorias. Para cada entry con `expected_memory_keywords`:

1. Embede el query del usuario
2. Busca top-5 memorias similares en la DB
3. Calcula **Precision@5** (memorias relevantes / memorias retornadas)
4. Calcula **Recall** (keywords encontrados / keywords esperados)
5. Score = (P + R) / 2

```bash
make eval-memory  # threshold 60%
```

### Agent Plan (`--mode plan`)

Evalúa la calidad de planes generados por el planner. Para cada entry con `expected_plan_tasks` o `expected_plan_categories`:

1. Ejecuta `create_plan(objective)` con Ollama
2. **Scoring determinístico**: min tasks, categories esperadas
3. **Scoring LLM**: judge evalúa coherence, completeness, feasibility
4. Score = 50% determinístico + 50% LLM

```bash
make eval-plan  # threshold 50%
```

### Context Saturation Analysis

Script standalone que correlaciona `context_fill_rate` con calidad:

```bash
make eval-saturation
```

Agrupa traces en buckets (0-50%, 50-70%, ..., 90-100%) y muestra guardrail pass rate y judge scores por bucket. Identifica el inflection point donde la calidad degrada.

## Seed Dataset Expansion (82 → ~120 cases)

| Sección | Cases | Qué evalúa |
|---------|:-----:|------------|
| `language` | 10 | Consistencia de idioma en multi-turn, code-switching |
| `tool_hallucination` | 10 | Queries donde NO hay tool apropiado |
| `remediation` | 8 | Responses que desafían guardrails |
| `agent` | 10 | Objectives complejos para calidad de planificación |

## Scheduled Eval (APScheduler)

Configurado via env vars:

| Variable | Default | Descripción |
|----------|---------|-------------|
| `EVAL_SCHEDULED_ENABLED` | `false` | Activa eval nightly |
| `EVAL_SCHEDULED_HOUR` | `4` | Hora UTC |
| `EVAL_SCHEDULED_THRESHOLD` | `0.7` | Umbral de alerta |
| `EVAL_SCHEDULED_MODE` | `classify` | Modo de eval |

Cuando accuracy < threshold, envía alerta WhatsApp al `AUTOMATION_ADMIN_PHONE`.

## Makefile targets

| Target | Qué hace |
|--------|---------|
| `eval-memory` | Memory retrieval benchmark (threshold 60%) |
| `eval-plan` | Agent plan benchmark (threshold 50%) |
| `eval-saturation` | Context saturation analysis |
| `eval-all` | seed + classify + tools + e2e + guardrails + memory |

## Archivos clave

| Archivo | Qué contiene |
|---------|-------------|
| `scripts/run_eval.py` | `_run_memory()`, `_run_plan()` |
| `scripts/seed_eval_dataset.py` | 38 nuevos cases en 4 secciones |
| `scripts/context_saturation_analysis.py` | Análisis de saturación |
| `app/main.py` | Scheduled eval job |
| `app/config.py` | Settings: `eval_scheduled_*` |

## Testing

→ [`docs/testing/62-benchmark_suite_testing.md`](../testing/62-benchmark_suite_testing.md)
