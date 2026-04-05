# Testing: Benchmark Suite Expansion (Plan 62)

## Tests automatizados

### Memory Benchmark (`tests/test_eval_memory.py`)

| Test | Qué verifica |
|------|-------------|
| `test_run_memory_with_matching_keywords` | P@5 + Recall scoring funciona con keywords |
| `test_run_memory_skips_entries_without_keywords` | Entries sin metadata se filtran |
| `test_run_memory_handles_embedding_failure` | Embedding failure → score 0 |

### Plan Benchmark (`tests/test_eval_plan.py`)

| Test | Qué verifica |
|------|-------------|
| `test_run_plan_scores_task_count` | Scoring determinístico + LLM judge |
| `test_run_plan_skips_without_metadata` | Entries sin plan metadata se filtran |
| `test_run_plan_handles_planner_error` | Planner error → score 0 |

### Scheduled Eval (`tests/test_scheduled_eval.py`)

| Test | Qué verifica |
|------|-------------|
| `test_eval_scheduled_settings_defaults` | Defaults correctos |
| `test_eval_scheduled_settings_override` | Override via constructor/env |

## Testing manual

### Seed dataset expansion

```bash
python scripts/seed_eval_dataset.py --dry-run
# Debería mostrar ~120 cases (82 originales + ~38 nuevos)

python scripts/seed_eval_dataset.py --section language --dry-run
# 10 cases de language consistency

python scripts/seed_eval_dataset.py --section agent --dry-run
# 10 cases de agent objectives
```

### Memory benchmark

```bash
# Requiere memorias en la DB + Ollama + embeddings
make eval-memory
```

### Plan benchmark

```bash
# Requiere Ollama (genera planes con LLM)
make eval-plan
```

### Context saturation

```bash
make eval-saturation
# Debería mostrar tabla con buckets y métricas
```

### Scheduled eval

```bash
# Activar temporalmente
EVAL_SCHEDULED_ENABLED=true EVAL_SCHEDULED_HOUR=$(date -u +%H) make run
# Verificar en logs que el job se registra
# Esperar al minuto exacto o verificar manualmente
```
