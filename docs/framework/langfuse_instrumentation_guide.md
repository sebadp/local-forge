# Guía de Instrumentación Langfuse v3 — Referencia para Implementadores

> **Audiencia**: Desarrolladores senior o expertos que van a instrumentar, configurar evaluadores, diseñar datasets o integrar Langfuse con CI/CD.
> **Fecha de referencia**: Marzo 2026 (Langfuse v3, Python SDK v3+, observation-level evals).

---

## 1. Arquitectura de Evaluación — El Modelo Mental Correcto

### Offline vs Online: Dos Loops Complementarios

```
                    ┌─────────────────────────────┐
                    │     OFFLINE EVALUATION        │
                    │                               │
  Dataset ─────────▶  Experiment Runner ──────────▶ Scores + Comparison
  (golden,          │  (SDK: run_experiment)        │
   correction)      │  (Script: run_eval.py)        │
                    │                               │
                    └──────────────┬────────────────┘
                                   │
                            feeds / curates
                                   │
                    ┌──────────────▼────────────────┐
                    │     ONLINE EVALUATION          │
                    │                               │
  Production ──────▶  Live Evaluators ─────────────▶ Scores + Alerts
  Traces            │  (LLM-as-Judge)               │
                    │  (Observation-level)           │
                    │  (Human Annotation)            │
                    └───────────────────────────────┘
```

**Principio clave**: No son alternativas — son complementarios. Offline evalúa antes de deployar, online monitorea en producción. Los fallos de online alimentan el dataset offline. La calidad mejora en espiral.

### Evaluation Data Model (Langfuse v3)

```
Score ──────────────▶ Trace/Observation  (producción)
                     │
Evaluator ───────────┘  (genera scores automáticamente)

Dataset ──────────┐
  └─ DatasetItem  ├──▶ Experiment Run ──▶ DatasetRunItem ──▶ Trace + Scores
     (input,      │     (versioned)        (por item)
      expected)   │
                  └──▶ Experiment Run 2 (comparación A/B)
```

---

## 2. Prompt Management — Patrones Avanzados

### 2.1 Modelo de Datos: Versions vs Labels

| Concepto | Inmutable | Propósito |
|----------|-----------|-----------|
| **Version** | Sí | Snapshot histórico. Cada cambio = nueva versión (1, 2, 3...) |
| **Label** | No (puntero) | Deployment target. `production` apunta a la versión desplegada |

**Regla de oro**: El código siempre referencia **labels**, nunca versions. Esto desacopla deploys de código de deploys de prompts.

### 2.2 Cadena de Resolución en LocalForge

```python
# app/eval/prompt_manager.py — get_active_prompt()
1. Cache en memoria (_active_prompts dict)        ← O(1), sin I/O
2. DB local (prompt_versions tabla, is_active=1)   ← async, persistente
3. Langfuse API (label="production")               ← red, editable desde UI
4. Registry hardcoded (PROMPT_DEFAULTS)             ← código fuente
5. Default param explícito                          ← backward compat
6. ValueError                                       ← fallo explícito
```

**Langfuse como fallback extra** (paso 3): Permite editar prompts desde el dashboard sin tocar código. Pero el cache local persiste hasta `invalidate_prompt_cache()` o reinicio de la app.

### 2.3 Sync Bidireccional

```
/approve-prompt system_prompt 3
    │
    ├── DB: activate_prompt_version("system_prompt", 3)
    ├── Cache: invalidate_prompt_cache("system_prompt")
    └── Langfuse: upsert_prompt(name="system_prompt", labels=["production", "v3"])

Startup (main.py lifespan):
    └── Langfuse: upsert_prompt(cada PROMPT_DEFAULTS, labels=["default"])
```

### 2.4 Config Versionada (Patrón Avanzado)

Langfuse v3 permite versionar **config junto al prompt** — modelo, temperature, tools, response_format. Esto es útil para:

```python
# Ejemplo: Prompt con config versionado
langfuse.create_prompt(
    name="system_prompt",
    prompt="You are a helpful assistant...",
    type="text",
    config={
        "model": "qwen3.5:9b",
        "temperature": 0.7,
        "response_format": {"type": "json_schema", "strict": True, "schema": {...}},
    },
    labels=["production"],
)
```

**Beneficio**: Cambiar modelo o temperatura desde la UI sin tocar código. El config se versiona junto al prompt.

> **En LocalForge**: Aún no usamos config versionado. Los parámetros vienen de `Settings`. Pero es un patrón a considerar cuando se migre a APIs pagas donde cada modelo tiene pricing diferente.

### 2.5 Caching — Gotcha Crítico

Langfuse SDK cachea prompts localmente. Si cambias un prompt en la UI, la app puede seguir usando la versión cacheada. Opciones:

1. **Invalidar cache manualmente**: `invalidate_prompt_cache("name")`
2. **TTL del SDK**: Langfuse SDK tiene cache con TTL (configurable)
3. **Reiniciar la app**: Limpia todo

**Nuestra implementación** usa cache propio (`_active_prompts` dict) + `_try_langfuse_prompt()` como fallback sync. El cache se invalida solo en `/approve-prompt`. Para cambios hechos desde la UI de Langfuse, necesitas reiniciar la app o extender la invalidación.

---

## 3. Evaluadores — Diseño e Implementación

### 3.1 Principio: Un Evaluador = Un Failure Mode

El error más común es crear evaluadores "generales" que miden todo. En cambio:

```
❌ "¿Es buena esta respuesta?"           → ambiguo, inconsistente
✅ "¿Contiene información no presente    → específico, medible
    en el contexto proporcionado?"
```

**Patrón recomendado**: Identificar failure modes concretos, crear un evaluador por cada uno:

| Failure Mode | Evaluador | Score Name | Tipo |
|-------------|-----------|------------|------|
| Respuesta vacía | Regex/deterministic | `guardrail_not_empty` | System |
| Idioma incorrecto | Regex/deterministic | `guardrail_language_match` | System |
| Hallucination | LLM-as-Judge | `hallucination` | LLM Judge |
| Fuera de scope | LLM-as-Judge | `out_of_scope` | LLM Judge |
| Respuesta genérica | LLM-as-Judge | `specificity` | LLM Judge |
| Tool incorrecto | LLM-as-Judge | `tool_selection` | LLM Judge |

### 3.2 Diseño de Rubrics para LLM-as-Judge

Un buen rubric tiene:

1. **Criterio claro**: Qué mide, una sola dimensión
2. **Definiciones de escala**: Qué significa cada score
3. **Ejemplos concretos**: Al menos 2-3 positivos y negativos
4. **Variables template**: `{{input}}`, `{{output}}`, `{{context}}` (si aplica)

```
Evalúa si la respuesta del asistente aborda directamente la pregunta del usuario.

Criterio: La respuesta debe ser específica al caso del usuario, no genérica.

Score 1.0 (PASS): La respuesta menciona datos concretos del contexto del usuario
  y responde su pregunta directamente.
  Ejemplo: "Tu reunión del lunes 14 es a las 10am en la sala B."

Score 0.0 (FAIL): La respuesta es genérica, evasiva, o no responde la pregunta.
  Ejemplo: "Las reuniones suelen ser a diferentes horas según el día."

Input: {{input}}
Output: {{output}}

Responde SOLO con un JSON: {"score": 0.0 o 1.0, "reasoning": "breve explicación"}
```

### 3.3 Validación Contra Juicio Humano

Antes de confiar en un evaluador, **calibrarlo**:

1. **Development set**: 20-50 traces anotados manualmente (pass/fail)
2. **Correr evaluador**: Aplicar el LLM-as-judge a los mismos traces
3. **Medir acuerdo**:
   - **TPR** (True Positive Rate): ¿Detecta los buenos correctamente?
   - **TNR** (True Negative Rate): ¿Detecta los malos correctamente?
   - **Target**: Ambos > 90%
4. **Iterar**: Ajustar rubric, examples, modelo judge hasta alcanzar target
5. **Test set**: Validar con un set separado (held-out) para confirmar

### 3.4 Observation-Level Evals (Febrero 2026)

**Cambio de paradigma**: En vez de evaluar traces completos, evaluar **operaciones individuales**:

| Target | Ejemplo | Ventaja |
|--------|---------|---------|
| Generation | Solo la respuesta final del chatbot | No evalúa classify ni tools |
| Retrieval | Solo el resultado de búsqueda semántica | Mide relevancia del RAG |
| Tool call | Solo la ejecución de `get_weather` | Detecta hallucinations de params |

**Configuración en Langfuse UI**:
1. Crear evaluator → target: "Live Observations"
2. Filtros de observation: type=`generation`, name=`llm:iteration_*`
3. Filtros de trace: tags contiene `tools` (solo cuando usa tools)
4. Sampling: 5-10% para costo razonable
5. Variable mapping: `{{input}}` = observation.input, `{{output}}` = observation.output

**Requisitos SDK**: Python v3+ (OTel-based). Ya cumplimos esto en LocalForge.

**Ventajas sobre trace-level**:
- **Velocidad**: Segundos vs minutos
- **Precisión**: Evalúa exactamente lo que importa
- **Costo**: Menos tokens procesados por eval
- **Composicional**: Diferentes evaluadores en diferentes operaciones simultáneamente

### 3.5 Sampling y Gestión de Costos

Para producción con volumen alto:

```
Evaluators de guardrails (determinísticos): 100% — zero cost
Evaluators LLM-as-judge (observation-level): 5-10%
Evaluators LLM-as-judge (trace-level): 1-5%
```

**Costo estimado por eval**: $0.01-0.10 con modelos cloud. $0 con Ollama self-hosted (pero consume GPU).

---

## 4. Datasets — Curación y Mantenimiento

### 4.1 Estrategia de Curación de 3 Tiers

LocalForge implementa curación automática en `maybe_curate_to_dataset()`:

```python
# Prioridad de curación (app/eval/dataset.py):
1. FAILURE: any system_score < 0.3 OR any user_score < 0.3
   → Tags: ["guardrail:language_match", "category:weather"]
   → No se sincroniza a Langfuse (ruidoso)

2. GOLDEN (confirmed): all system_scores ≥ 0.8 AND user_score ≥ 0.8
   → Metadata: {confirmed: true, primary_category: "time"}
   → Sincroniza a Langfuse con source_trace_id

3. GOLDEN (candidate): all system_scores ≥ 0.8, no user signal
   → Metadata: {confirmed: false, primary_category: "weather"}
   → Espera confirmación humana para promover
```

### 4.2 Composición Ideal del Dataset

Un golden dataset sano para eval debe tener:

| Dimensión | Recomendación |
|-----------|--------------|
| **Tamaño mínimo** | 50+ entries con `expected_output` |
| **Diversidad** | Cubrir todas las categorías de intent (time, weather, notes, math, etc.) |
| **Balance** | No más de 30% de una sola categoría |
| **Edge cases** | Al menos 10% de queries difíciles/ambiguas |
| **Correcciones** | Al menos 20% de correction pairs (input + bad_output + good_output) |
| **Freshness** | Renovar 10-20% cada mes desde producción |

### 4.3 De Silver a Gold: Pipeline de Promoción

```
Production traces
    │
    ▼
Scores automáticos (guardrails, context_fill_rate)
    │
    ├── Scores bajos → failure entry (silver, auto-curated)
    │
    ├── Scores altos + sin user signal → golden candidate (silver)
    │       │
    │       ├── Annotation Queue → human review → score humano
    │       │       │
    │       │       ├── Score ≥ 0.8 → golden confirmed (gold) ★
    │       │       └── Score < 0.3 → reclassify as failure
    │       │
    │       └── User /rate 5 → golden confirmed (gold) ★
    │
    └── Scores altos + user 👍 → golden confirmed (gold, auto) ★
```

### 4.4 Metadata y Trazabilidad

Cada dataset entry incluye:

```python
{
    "trace_id": "abc123",           # Trace original
    "source_trace_id": "abc123",    # Link en Langfuse Datasets
    "primary_category": "weather",   # Categoría de intent
    "confirmed": True,               # Silver vs Gold
    "entry_type": "golden",          # golden | correction | failure
}
```

**`source_trace_id`** permite navegar desde el dataset item al trace completo en Langfuse, viendo el contexto exacto de la interacción.

### 4.5 Organización de Datasets en Langfuse

```
localforge-eval              ← Dataset único, filtrable
  ├── golden entries          → Filtrar por metadata.confirmed=true
  ├── correction entries      → Filtrar por entry_type=correction
  ├── failure entries         → Solo local (no sincronizado, ruidoso)
  └── category:weather        → Filtrar por metadata.primary_category
```

**Alternativa**: Usar dataset names con `/` para crear carpetas virtuales:
```
localforge-eval/weather
localforge-eval/agent
localforge-eval/corrections
```

---

## 5. Experiments — Benchmarking Sistemático

### 5.1 Experiment Runner vs Manual Loop

**Langfuse SDK v3 ofrece `run_experiment()`** — un runner de alto nivel que maneja concurrencia, tracing, y evaluación automáticamente.

**LocalForge usa un loop manual** en `scripts/run_eval.py` — más control, integrado con nuestro OllamaClient.

### 5.2 Anatomy de un Experiment Run

```python
# scripts/run_eval.py --langfuse
async def _run_eval(..., use_langfuse=True):
    # 1. Fetch entries con expected_output
    entries = await repo.get_dataset_entries(limit=20)

    # 2. Por cada entry:
    for entry in entries:
        # a. Generar respuesta actual
        actual = await client.chat([ChatMessage(role="user", content=entry["input_text"])])

        # b. LLM-as-judge (binary yes/no)
        judge_resp = await client.chat([judge_prompt], think=False)
        passed = judge_resp.startswith("yes")

    # 3. Si --langfuse: crear traces + scores en Langfuse
    for r in results:
        lf.start_span(trace_context={"trace_id": lf_trace_id}, name="eval_run", ...)
        lf.create_score(trace_id=lf_trace_id, name="correctness", value=1.0)  # or 0.0 if failed

    # 4. Resultados: tabla + accuracy + exit code
    #    Exit 0 = pass, Exit 1 = fail (para CI)
```

### 5.3 Integración con CI/CD

```yaml
# .github/workflows/eval.yml (ejemplo)
eval:
  runs-on: self-hosted  # necesita Ollama
  steps:
    - run: python scripts/run_eval.py --threshold 0.7 --langfuse
    # Exit code 1 si accuracy < threshold → CI fail
```

### 5.4 Comparación A/B de Prompts

Workflow para comparar dos versiones de prompt:

```bash
# 1. Baseline: correr con prompt actual
python scripts/run_eval.py --langfuse  # → run "baseline-2026-03-14"

# 2. Cambiar prompt (en DB o Langfuse)
/approve-prompt system_prompt 4

# 3. Candidate: correr con prompt nuevo
python scripts/run_eval.py --langfuse  # → run "candidate-2026-03-14"

# 4. Comparar en Langfuse UI:
#    Datasets → localforge-eval → Runs → comparar side-by-side
```

### 5.5 Evaluators Avanzados para Experiments

Más allá del binary yes/no, se pueden implementar:

```python
# Item-level evaluator (para Langfuse SDK run_experiment)
def semantic_similarity_evaluator(*, input, output, expected_output, **kwargs):
    """Compara output vs expected con embeddings."""
    sim = cosine_similarity(embed(output), embed(expected_output))
    return Evaluation(
        name="semantic_similarity",
        value=round(sim, 3),
        comment=f"cosine={sim:.3f}",
    )

# Run-level evaluator (agrega métricas)
def regression_detector(*, item_results, **kwargs):
    """Detecta si accuracy bajó vs run anterior."""
    current_accuracy = mean(r.evaluations["correctness"] for r in item_results)
    return Evaluation(
        name="regression_check",
        value=1.0 if current_accuracy >= 0.8 else 0.0,
        comment=f"accuracy={current_accuracy:.1%}",
    )
```

---

## 6. Instrumentación — Spans Best Practices

### 6.1 Patrón de Span en LocalForge

```python
from app.tracing.context import get_current_trace

trace = get_current_trace()
if trace:
    async with trace.span("memory:flush", kind="generation") as span:
        span.set_input({"message_count": len(messages)})
        span.set_model("qwen3.5:9b")  # convenience method
        result = await ollama_client.chat(messages, think=False)
        span.set_output({"facts_added": len(facts)})
```

**Reglas**:
- `kind="generation"` para LLM calls (muestra modelo + tokens en Langfuse)
- `kind="span"` para operaciones sin LLM (búsquedas, I/O, cálculos)
- `kind="tool"` para tool executions
- Siempre `if trace:` guard — la app funciona sin tracing
- Best-effort: nunca propagar errores del tracing

### 6.2 Modelo Fallback para Generations

Cuando un span tiene `kind="generation"` pero no se setea `gen_ai.request.model` en metadata, el recorder aplica fallback a `Settings().ollama_model`:

```python
# app/tracing/recorder.py — finish_span()
model = md.pop("gen_ai.request.model", None)
kind = md.pop("_span_kind", None)
if model is None and kind == "generation":
    model = Settings().ollama_model  # "qwen3.5:9b" default
```

Esto garantiza que **todas** las generations muestren modelo en Langfuse, incluso las que no setean metadata explícitamente.

### 6.3 SpanData.set_model() Convenience

```python
async with trace.span("llm:summarize", kind="generation") as span:
    span.set_model("qwen3.5:9b")  # → metadata["gen_ai.request.model"]
    # ...
```

### 6.4 Trace Metadata Enrichment

El `TraceContext` ahora acepta `metadata` en el constructor:

```python
async with TraceContext(
    phone_number=phone,
    input_text=text,
    recorder=recorder,
    platform="telegram",
    metadata={"app_version": "1.2.3", "feature_flags": ["rag_v2"]},
) as trace:
    # metadata se envía a Langfuse en start_trace()
```

### 6.5 Tags para Filtrado

Tags se actualizan post-classify con:
```python
tags = [platform_tag] + categories + [f"project:{project_name}"]
# Ejemplo: ["whatsapp", "weather", "time", "project:MiProyecto"]
```

Útil para filtrar en Langfuse UI: `tags contains "project:MiProyecto"`.

---

## 7. Annotation Queues — Setup Operativo

### 7.1 Configuración de Queues

**Prerequisito**: Crear Score Configs en Langfuse (`Settings → Score Configs`):

| Config Name | Type | Scale | Descripción |
|-------------|------|-------|-------------|
| `quality` | Numeric | 1-5 | Calidad general de la respuesta |
| `accuracy` | Categorical | pass/fail | ¿Factualmente correcta? |
| `helpfulness` | Categorical | pass/fail | ¿Responde lo que el usuario necesita? |
| `corrected_output` | Text | free-text | Output corregido (para promover a correction pair) |

**Crear queues** (`Human Annotation → New Queue`):

1. **Guardrail Failures**: Score configs = [quality, accuracy], filtrar por scores `guardrail_*` < 0.5
2. **Agent Review**: Score configs = [quality, helpfulness], filtrar por tag `agent`
3. **Daily Sample**: Score configs = [quality], sampling aleatorio de 5%

### 7.2 Poblar Queues

Dos métodos:
- **Bulk**: En `Traces`, seleccionar con checkboxes → Actions → "Add to queue"
- **Automático**: Configurar evaluator que asigna a queue basado en scores (futuro)

### 7.3 Feedback Loop: Annotations → Dataset

Los scores humanos de annotations alimentan `maybe_curate_to_dataset()`:
- `source="human"`, `value ≥ 0.8` → promueve candidate a golden confirmed
- `source="human"`, `value < 0.3` → degrada a failure

Si el annotator provee un `corrected_output`, se puede crear un correction pair programáticamente.

---

## 8. Producción — Checklist de Madurez

### Nivel 1: Observabilidad Básica (ya implementado ✅)
- [x] Tracing de todas las interacciones
- [x] Spans para LLM calls, tool calls, guardrails
- [x] Scores automáticos (guardrails, context_fill_rate)
- [x] Session grouping por phone_number
- [x] Tags de platform y categorías

### Nivel 2: Evaluación Continua (en progreso 🚧)
- [x] Dataset auto-curado (golden, correction, failure)
- [x] LLM-as-judge offline (`run_eval.py`)
- [x] Prompt sync a Langfuse
- [ ] Evaluators LLM-as-judge configurados en Langfuse UI
- [ ] Observation-level evals para generations
- [ ] Annotation queues operativas con reviewers asignados

### Nivel 3: Regression Prevention (siguiente fase)
- [ ] CI/CD con eval benchmark (threshold gate)
- [ ] Webhooks de alerta en degradación de scores
- [ ] Dataset balanceado con 50+ entries por categoría
- [ ] Comparación A/B automatizada de prompts
- [ ] Calibración de evaluadores contra juicio humano (TPR/TNR > 90%)

### Nivel 4: Optimización Continua (aspiracional)
- [ ] Cost tracking con modelos custom registrados
- [ ] Token efficiency dashboards por feature
- [ ] Auto-prompt improvement via annotation feedback loops
- [ ] Session-level eval (multi-turn coherence)
- [ ] Evaluators específicos por dominio (RAG faithfulness, tool selection)

---

## 9. Referencia Rápida de APIs

### TraceRecorder (app/tracing/recorder.py)

| Método | Propósito | Langfuse API |
|--------|-----------|-------------|
| `start_trace()` | Iniciar trace + root span | `start_span()` + `update_trace()` |
| `finish_trace()` | Cerrar trace | `update_trace()` + `end()` + `flush()` |
| `start_span()` | Crear span hijo | `start_span()` / `start_generation()` |
| `finish_span()` | Cerrar span con metadata | `update()` + `end()` |
| `add_score()` | Agregar score a trace/span | `create_score()` |
| `update_trace_tags()` | Actualizar tags | `update_trace(tags=...)` |
| `sync_dataset_to_langfuse()` | Crear dataset item | `create_dataset()` + `create_dataset_item()` |
| `upsert_prompt()` | Sync prompt a Langfuse | `create_prompt()` |
| `get_or_create_dataset()` | Ensure dataset exists | `create_dataset()` |

### Prompt Manager (app/eval/prompt_manager.py)

| Función | Propósito |
|---------|-----------|
| `get_active_prompt(name, repo, default)` | Resolver prompt con fallback chain |
| `_try_langfuse_prompt(name)` | Fallback a Langfuse (label=production) |
| `activate_with_eval(name, version, repo, ollama)` | Eval LLM-as-judge antes de activar |
| `invalidate_prompt_cache(name)` | Limpiar cache después de activar |

### Dataset (app/eval/dataset.py)

| Función | Propósito |
|---------|-----------|
| `maybe_curate_to_dataset(trace_id, ..., primary_category)` | Auto-curar trace a dataset |
| `add_correction_pair(trace_id, input, bad_output, correction)` | Guardar par de corrección |

---

## 10. Fuentes

- [Langfuse Prompt Management — Get Started](https://langfuse.com/docs/prompt-management/get-started)
- [Langfuse Prompt Data Model](https://langfuse.com/docs/prompt-management/data-model)
- [Langfuse LLM-as-a-Judge](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge)
- [Langfuse Evaluation Core Concepts](https://langfuse.com/docs/evaluation/core-concepts)
- [Langfuse Experiments via SDK](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk)
- [Langfuse Annotation Queues](https://langfuse.com/docs/evaluation/evaluation-methods/annotation-queues)
- [Langfuse Observation-Level Evals (Feb 2026)](https://langfuse.com/changelog/2026-02-13-observation-level-evals)
- [Langfuse Automated Evaluations Blog](https://langfuse.com/blog/2025-09-05-automated-evaluations)
- [Langfuse Agent Skills for Prompt Improvement](https://langfuse.com/blog/2026-02-16-prompt-improvement-claude-skills)
- [LLM Evaluation 101 — Best Practices](https://langfuse.com/blog/2025-03-04-llm-evaluation-101-best-practices-and-challenges)
- [Langfuse Datasets](https://langfuse.com/docs/evaluation/experiments/datasets)
- [Complete Guide to LLM Observability 2026](https://portkey.ai/blog/the-complete-guide-to-llm-observability/)
- [Building a Golden Dataset for AI Evaluation](https://www.getmaxim.ai/articles/building-a-golden-dataset-for-ai-evaluation-a-step-by-step-guide/)
