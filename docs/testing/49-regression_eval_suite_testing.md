# Testing: Regression Eval Suite

> Feature doc: [`docs/features/49-regression_eval_suite.md`](../features/49-regression_eval_suite.md)

## 1. Verificar que la infraestructura está lista

```bash
# Ollama corriendo
curl -s http://localhost:11434/api/tags | python3 -m json.tool | grep qwen3

# DB existe y tiene schema
sqlite3 data/localforge.db ".tables" | grep eval_dataset

# Scripts ejecutables
python scripts/seed_eval_dataset.py --dry-run
python scripts/run_eval.py --help
```

## 2. Casos de prueba: Seed

### Seed completo

| Paso | Acción | Resultado esperado |
|---|---|---|
| 1 | `python scripts/seed_eval_dataset.py --db data/localforge.db` | `Inserted: 82, Skipped: 0` |
| 2 | Re-ejecutar el mismo comando | `Inserted: 0, Skipped: 82` (idempotencia) |
| 3 | Verificar en DB: `sqlite3 data/localforge.db "SELECT COUNT(*) FROM eval_dataset WHERE metadata LIKE '%seed%'"` | `82` |

### Seed con filtros

| Paso | Acción | Resultado esperado |
|---|---|---|
| 1 | `python scripts/seed_eval_dataset.py --dry-run --section math` | Muestra 8 cases, no toca DB |
| 2 | `python scripts/seed_eval_dataset.py --dry-run --section inexistente` | Mensaje de error con secciones disponibles |
| 3 | `python scripts/seed_eval_dataset.py --section math` (después de seed completo) | `Inserted: 0, Skipped: 8` |

### Clear y re-seed

| Paso | Acción | Resultado esperado |
|---|---|---|
| 1 | `python scripts/seed_eval_dataset.py --clear` | `Cleared 82 seed entries` |
| 2 | `sqlite3 data/localforge.db "SELECT COUNT(*) FROM eval_dataset WHERE metadata LIKE '%seed%'"` | `0` |
| 3 | Verificar que entries orgánicas no fueron borradas | Entries sin `"source": "seed"` siguen intactas |
| 4 | Re-seed: `python scripts/seed_eval_dataset.py` | `Inserted: 82` |

### Verificar tags

```bash
sqlite3 data/localforge.db "
  SELECT t.tag, COUNT(*)
  FROM eval_dataset_tags t
  JOIN eval_dataset d ON d.id = t.dataset_id
  WHERE d.metadata LIKE '%seed%'
  GROUP BY t.tag
  ORDER BY t.tag
"
```

Esperado: tags como `lang:en`, `lang:es`, `level:classify`, `level:e2e`, `level:tools`, `section:chat`, `section:math`, etc.

## 3. Casos de prueba: Classify (Level 1)

> **Prerequisito**: Ollama corriendo con qwen3.5:9b

| Paso | Acción | Resultado esperado |
|---|---|---|
| 1 | `python scripts/run_eval.py --mode classify --section math --limit 3` | 3 entries evaluadas, score por entry |
| 2 | `python scripts/run_eval.py --mode classify --section chat` | 5 entries, expected=`["none"]` |
| 3 | `python scripts/run_eval.py --mode classify --section multicategory` | 5 entries multi-categoría |
| 4 | `python scripts/run_eval.py --mode classify --threshold 1.0` | FAIL (difícil lograr 100%) |

### Verificar output format

```
id       section      pass     score        input
----------------------------------------------------
NNN      math         PASS     100%         'Cuanto es 15 * 7 + 3?'
         expected=['math'] actual=['math']
```

- Columnas alineadas
- Detalle con `expected=` y `actual=` debajo
- Summary con breakdown por sección al final

## 4. Casos de prueba: Tools (Level 2)

| Paso | Acción | Resultado esperado |
|---|---|---|
| 1 | `python scripts/run_eval.py --mode tools --section math` | `calculate` en selected tools |
| 2 | `python scripts/run_eval.py --mode tools --section time` | Tools variados: `get_current_datetime`, `schedule_task`, etc. |
| 3 | `python scripts/run_eval.py --mode tools --section multicategory` | Score combinado cat+tools |

### Verificar output format

```
         cats: expected=['math'] actual=['math'] (100%) | tools: expected=['calculate'] selected=['calculate'] (100%)
```

## 5. Casos de prueba: E2E (Level 3)

| Paso | Acción | Resultado esperado |
|---|---|---|
| 1 | `python scripts/run_eval.py --mode e2e --section chat --limit 2` | 2 entries con LLM-as-judge |
| 2 | `python scripts/run_eval.py --mode e2e --section math --limit 3` | Respuestas numéricas evaluadas |
| 3 | `python scripts/run_eval.py --mode e2e --section github` | 0 entries (github cases no tienen level:e2e) |

## 6. Edge cases y validaciones

| Escenario | Acción | Resultado esperado |
|---|---|---|
| DB vacía (sin seed) | `python scripts/run_eval.py --mode classify` | Exit code 2, mensaje sugiriendo seed |
| Ollama apagado | `python scripts/run_eval.py --mode classify --section math` | Cae a DEFAULT_CATEGORIES, tests pasan con falso positivo |
| Sección inexistente | `python scripts/run_eval.py --mode classify --section xyz` | 0 entries, exit code 2 |
| Tag inexistente | `python scripts/run_eval.py --mode classify --tag section:xyz` | 0 entries, exit code 2 |
| Limit 0 | `python scripts/run_eval.py --mode classify --limit 0` | 0 entries, exit code 2 |
| Entry sin expected_categories | Entries orgánicas | Filtradas automáticamente en classify/tools |
| Entry sin expected_output | Cases con solo classify/tools eval_types | Filtradas en e2e mode |

## 7. Makefile targets

| Target | Comando equivalente | Resultado |
|---|---|---|
| `make eval-seed` | `python scripts/seed_eval_dataset.py --db data/localforge.db` | Seed idempotente |
| `make eval-classify` | `--mode classify --threshold 0.8 --limit 100` | Exit 0 si accuracy >= 80% |
| `make eval-tools` | `--mode tools --threshold 0.7 --limit 100` | Exit 0 si accuracy >= 70% |
| `make eval-e2e` | `--mode e2e --threshold 0.7 --limit 100` | Exit 0 si accuracy >= 70% |
| `make eval` | seed + classify + e2e | Pipeline completo |

## 8. Langfuse integration

```bash
# Requiere env vars
export LANGFUSE_PUBLIC_KEY=pk-...
export LANGFUSE_SECRET_KEY=sk-...
export LANGFUSE_HOST=https://cloud.langfuse.com

python scripts/run_eval.py --mode classify --langfuse --section math
```

| Verificación | Esperado |
|---|---|
| Output en terminal | `[Langfuse] Synced N results to dataset 'localforge-eval-classify'` |
| Dashboard Langfuse | Dataset `localforge-eval-classify` con traces |
| Scores | Score `correctness` con valor 0.0 o 1.0 por trace |
| Metadata | model, mode, section en cada trace |

## 9. Verificar en DB

```bash
# Conteo por sección
sqlite3 data/localforge.db "
  SELECT json_extract(metadata, '$.section') as section, COUNT(*)
  FROM eval_dataset
  WHERE metadata LIKE '%seed%'
  GROUP BY section ORDER BY section
"

# Entries con tool expectations
sqlite3 data/localforge.db "
  SELECT json_extract(metadata, '$.section'), json_extract(metadata, '$.expected_tools')
  FROM eval_dataset
  WHERE json_extract(metadata, '$.expected_tools') != '[]'
  LIMIT 10
"

# Tags por entry
sqlite3 data/localforge.db "
  SELECT d.id, d.input_text, GROUP_CONCAT(t.tag, ', ')
  FROM eval_dataset d
  JOIN eval_dataset_tags t ON t.dataset_id = d.id
  WHERE d.metadata LIKE '%seed%'
  GROUP BY d.id
  LIMIT 5
"
```

## 10. Troubleshooting

| Problema | Causa | Solución |
|---|---|---|
| `No evaluatable entries found` | No se corrió seed, o filtro de tag/section no matchea | `make eval-seed` primero; verificar nombre de sección |
| `Evaluating N entries... No entries could be evaluated` | Entries no tienen metadata requerida para el modo | Verificar que entries tienen `expected_categories` (classify) o `expected_output` (e2e) |
| Classify siempre devuelve defaults | Ollama no está corriendo o modelo no existe | `curl http://localhost:11434/api/tags` para verificar |
| Score 0% en chat/none cases | Classifier devuelve categorías en vez de `"none"` | Revisar classifier prompt, agregar más ejemplos de "none" |
| E2E muy lento | 2 LLM calls por entry | Limitar con `--limit 10` o `--section` para iterar |
| `Inserted: 0` en seed (sin --clear previo) | Todas las entries ya existen | Comportamiento correcto (idempotencia) |

## Variables relevantes para testing

| Variable | Valor para test | Descripción |
|---|---|---|
| `--db` | `data/test_eval.db` | Usar DB separada para no contaminar producción |
| `--threshold` | `0.5` | Más permisivo para desarrollo |
| `--limit` | `5` | Iteración rápida |
| `--section` | `math` | Sección más determinística para test |
