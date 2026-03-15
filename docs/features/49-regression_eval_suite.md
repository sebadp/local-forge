# Regression Eval Suite

> **Versión**: 1.0
> **Fecha**: 2026-03-15
> **Fase**: Eval
> **Estado**: ✅ Completo

## Qué hace

Sistema de evaluación automatizada que reemplaza el testing manual con ~82 test cases golden ejecutables en 3 niveles de profundidad: clasificación de intent, selección de tools, y evaluación end-to-end con LLM-as-judge. Se ejecuta offline sin levantar FastAPI y produce exit codes CI-compatibles.

## Arquitectura

```
                    make eval
                       │
           ┌───────────┼───────────┐
           ▼           ▼           ▼
      eval-seed   eval-classify  eval-e2e
           │           │           │
           ▼           ▼           ▼
  seed_eval_dataset  run_eval    run_eval
       .py          --mode       --mode
                   classify       e2e
           │           │           │
           ▼           ▼           ▼
     ┌──────────┐  ┌────────┐  ┌─────────────┐
     │eval_     │  │classify│  │chat() +      │
     │dataset   │  │_intent │  │LLM-as-judge  │
     │(SQLite)  │  │(router)│  │(2 LLM calls) │
     └──────────┘  └────────┘  └─────────────┘
                       │
                       ▼
                   select_tools
                   (Level 2 only)
```

## Archivos clave

| Archivo | Rol |
|---|---|
| `scripts/seed_eval_dataset.py` | Pobla eval_dataset con 82 golden cases |
| `scripts/run_eval.py` | Runner de evals con 3 modos |
| `Makefile` | Targets `eval-seed`, `eval-classify`, `eval-tools`, `eval-e2e`, `eval` |
| `app/skills/router.py` | `classify_intent()` y `select_tools()` — sujetos de test |
| `app/database/repository.py` | `get_dataset_entries()` — lectura de entries |
| `app/database/db.py` | Schema de `eval_dataset` + `eval_dataset_tags` |

## Walkthrough técnico

### 1. Seed: poblar el dataset

`seed_eval_dataset.py` define 82 `EvalCase` dataclasses organizados en 14 secciones:

```
chat(5) math(8) time(8) weather(4) search(4) notes(7) projects(12)
selfcode(7) github(3) tools(3) expand(3) evaluation(4) automation(3)
knowledge(2) multicategory(5) edge(4)
```

Cada case especifica:
- `input_text`: mensaje del usuario
- `expected_output`: respuesta esperada (para LLM-as-judge)
- `expected_categories`: categorías de `classify_intent` (e.g., `["math"]`)
- `expected_tools`: tools que `select_tools` debería incluir (e.g., `["calculate"]`)
- `eval_types`: en qué modos se evalúa (`["classify", "tools", "e2e"]`)

La inserción usa raw SQL con `trace_id=NULL` y `entry_type="golden"`. Tags se insertan en `eval_dataset_tags` para filtrado:
- `section:math`, `section:time`, etc.
- `level:classify`, `level:tools`, `level:e2e`
- `lang:es`, `lang:en`

**Idempotencia**: busca entries existentes con `metadata LIKE '%"source": "seed"%'` y compara `input_text`.

### 2. Nivel 1 — Classify (`--mode classify`)

Para cada entry con `expected_categories`:

1. Llama `classify_intent(input_text, ollama_client)` — una sola LLM call (~1-2s)
2. Scoring por recall: `len(expected ∩ actual) / len(expected)`
3. Caso especial: `expected=["none"]` y `actual=["none"]` → 1.0
4. Pass si score ≥ 0.5

Ejemplo: si `expected=["math", "search"]` y `actual=["math"]` → score = 0.5 (1/2) → PASS

### 3. Nivel 2 — Tools (`--mode tools`)

Para cada entry con `expected_tools`:

1. Llama `classify_intent()` → obtiene categorías
2. Construye `eval_tools_map` con schemas fake desde `TOOL_CATEGORIES` (sin necesitar registry real)
3. Llama `select_tools(categories, eval_tools_map)` → obtiene tools seleccionadas
4. Tool score: `len(expected_tools ∩ selected) / len(expected_tools)`
5. Score combinado: `(cat_score + tool_score) / 2`
6. Pass si score combinado ≥ 0.5

### 4. Nivel 3 — E2E (`--mode e2e`)

Para cada entry con `expected_output`:

1. Genera respuesta: `client.chat([user_message])` — LLM call completa
2. Evalúa con LLM-as-judge: pregunta binaria "Does the actual answer correctly answer the question? yes/no"
3. Pass si el juez responde "yes"

### 5. Reporting

La salida muestra:
- Tabla por entry: id, sección, pass/fail, score, input preview
- Detalle (classify/tools): categorías/tools esperadas vs obtenidas
- Resumen total con breakdown por sección
- Exit code: 0 (PASS) si accuracy ≥ threshold, 1 (FAIL), 2 (sin entries)

### 6. Langfuse sync (`--langfuse`)

Crea dataset `localforge-eval-{mode}` en Langfuse con:
- Trace por entry con ID determinístico: `eval-{mode}-{entry_id}`
- Score `"correctness"` con valor 0.0 o 1.0
- Metadata: model, mode, section

## Cómo usar

### Quickstart

```bash
# Poblar dataset (idempotente)
make eval-seed

# Correr nivel 1 (rápido, ~2 min para 82 entries)
make eval-classify

# Correr nivel 2
make eval-tools

# Correr nivel 3 (lento, ~10-20 min)
make eval-e2e

# Pipeline completo: seed + classify + e2e
make eval
```

### Filtrar por sección

```bash
# Solo test cases de math
python scripts/run_eval.py --mode classify --section math

# Solo test cases de projects
python scripts/run_eval.py --mode tools --section projects

# Por tag arbitrario
python scripts/run_eval.py --mode classify --tag lang:en
```

### Ajustar thresholds

```bash
# Más estricto
python scripts/run_eval.py --mode classify --threshold 0.9

# Más permisivo para e2e (LLM-as-judge tiene más varianza)
python scripts/run_eval.py --mode e2e --threshold 0.6
```

### Dry run del seed

```bash
# Ver qué insertaría sin tocar la DB
python scripts/seed_eval_dataset.py --dry-run

# Solo una sección
python scripts/seed_eval_dataset.py --dry-run --section multicategory
```

### Limpiar y re-seedear

```bash
# Borra solo entries con source=seed (preserva orgánicas)
python scripts/seed_eval_dataset.py --clear
python scripts/seed_eval_dataset.py
```

## Cómo extender

### Agregar un test case

Agregar un `EvalCase` a la lista `CASES` en `seed_eval_dataset.py`:

```python
EvalCase(
    "Cuánto es log(100)?",               # input_text
    "Aproximadamente 4.605",              # expected_output
    "math",                               # section
    ["math"],                             # expected_categories
    ["calculate"],                        # expected_tools
    eval_types=["classify", "tools", "e2e"],
),
```

Luego re-seedear:
```bash
python scripts/seed_eval_dataset.py --db data/localforge.db
```

Solo inserta el nuevo (idempotencia por `input_text`).

### Agregar una sección nueva

1. Agregar los `EvalCase` con el `section` nuevo
2. Si la sección corresponde a una nueva categoría de `TOOL_CATEGORIES`, agregarla en `app/skills/router.py`
3. Re-seedear

### Agregar un modo de evaluación

1. Crear función `_run_<mode>(entries, client) -> list[dict]` en `run_eval.py`
2. Agregar al dispatch en `_run_eval()`:
   ```python
   elif mode == "mymode":
       results = await _run_mymode(filtered, client)
   ```
3. Agregar `"mymode"` a `choices` en argparse
4. Agregar target al Makefile

### Integrar con CI

El exit code permite usar en GitHub Actions:

```yaml
- name: Eval Classify
  run: make eval-classify
  # Falla el job si accuracy < threshold
```

## Interpretando resultados

### Classify mode output

```
id       section      pass     score        input
----------------------------------------------------
157      math         PASS     100%         'Cuanto es 15 * 7 + 3?'
         expected=['math'] actual=['math']
158      chat         FAIL     0%           'Hola, como estas?'
         expected=['none'] actual=['time', 'math', 'weather', 'search', 'documentation']
```

- **PASS 100%**: clasificación perfecta
- **FAIL 0%**: el classifier devolvió categorías cuando debería devolver `["none"]` (o viceversa)
- **PASS 50%**: para multi-categoría, acertó al menos la mitad

### Tools mode output

```
id       section      pass     score        input
----------------------------------------------------
157      math         PASS     100%         'Cuanto es 15 * 7 + 3?'
         cats: expected=['math'] actual=['math'] (100%) | tools: expected=['calculate'] selected=['calculate'] (100%)
```

El score combina clasificación + selección de tools. Ambos deben funcionar para score alto.

### Qué hacer cuando falla

| Modo falla | Causa probable | Acción |
|---|---|---|
| classify | Classifier prompt necesita más ejemplos | Agregar ejemplo a `_CLASSIFIER_PROMPT_TEMPLATE` en `router.py` |
| classify | Categoría nueva no reconocida | Agregar ejemplo de esa categoría al prompt |
| tools | Tool no está en `TOOL_CATEGORIES` | Verificar que el tool name está registrado en la categoría correcta |
| tools | Budget insuficiente (max_tools) | Verificar `per_cat` distribution en `select_tools()` |
| e2e | LLM genera respuesta incorrecta | Puede ser issue del model, no del sistema — verificar manualmente |
| e2e | Judge es inconsistente | Normal con LLM-as-judge; correr múltiples veces |

## Decisiones de diseño

| Decisión | Alternativa descartada | Motivo |
|---|---|---|
| Recall-based scoring (no precision) | F1 score | Queremos que el classifier incluya la categoría correcta; categorías extra son aceptables |
| `_build_eval_tools_map()` con schemas fake | Levantar skill registry completa | Evita dependencia de SKILL.md files y MCP servers; solo testea routing |
| Threshold diferente por nivel (0.8 classify, 0.7 tools/e2e) | Threshold único | Classify es más determinístico; e2e tiene varianza natural del LLM |
| Idempotencia por `input_text` match | UUID por case | Permite re-seedear sin duplicados incluso si se modifica metadata |
| `make eval` incluye seed | Solo classify + e2e | Garantiza que el dataset existe antes de correr evals |

## Gotchas y edge cases

- **Ollama no corriendo**: `classify_intent` cae a `DEFAULT_CATEGORIES` (fallback), lo que puede hacer que tests pasen falsamente. Siempre verificar que Ollama está up.
- **Entries sin metadata**: Las 77 entries orgánicas (pre-seed) no tienen `expected_categories`, así que son filtradas automáticamente en modo classify/tools. Solo participan en e2e si tienen `expected_output`.
- **Multi-categoría scoring**: Un case con `expected=["time", "weather"]` donde el classifier devuelve solo `["time"]` tiene score 0.5 — es un PASS (threshold 0.5). Para exigir ambas categorías, subir el threshold.
- **`--clear` solo borra seed**: Entries orgánicas (curadas en producción) nunca se borran con `--clear`. Esto es intencional.
- **Tags vs metadata**: `eval_types` vive en metadata JSON (filtrado en Python) y también como tags `level:classify` (filtrado en SQL). Ambos mecanismos coexisten para flexibilidad.

## Variables de configuración

| Variable | Default | Descripción |
|---|---|---|
| `--db` | `data/localforge.db` | Path a la base de datos SQLite |
| `--ollama` | `http://localhost:11434` | URL base de Ollama |
| `--model` | `qwen3.5:9b` | Modelo para classify y e2e |
| `--threshold` | `0.7` | Threshold de accuracy para exit code 0 |
| `--limit` | `100` | Máximo de entries a evaluar |

## Guía de testing

Ver [`docs/testing/49-regression_eval_suite_testing.md`](../testing/49-regression_eval_suite_testing.md)
