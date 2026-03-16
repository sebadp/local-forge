# Guía de Onboarding: Evaluaciones, Prompts y Experiments en Langfuse

> **Audiencia**: Desarrolladores que se incorporan al proyecto y necesitan entender cómo funciona el sistema de calidad y observabilidad.
> **Prerequisito**: Haber leído `docs/features/48-langfuse_v3.md` (arquitectura base del stack).

---

## 1. El Panorama General

LocalForge usa **Langfuse v3** como plataforma de observabilidad. Pero Langfuse no es solo "ver logs" — es un sistema completo de **calidad continua** con tres pilares:

```
┌─────────────────────────────────────────────────────┐
│              Ciclo de Calidad Continua               │
│                                                      │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐     │
│   │  Prompt   │───▶│  Deploy  │───▶│ Observar │     │
│   │ Management│    │ (labels) │    │ (traces) │     │
│   └────▲─────┘    └──────────┘    └────┬─────┘     │
│        │                               │            │
│   ┌────┴─────┐    ┌──────────┐    ┌────▼─────┐     │
│   │  Iterar  │◀───│Experiment│◀───│ Evaluar  │     │
│   │ (mejoras)│    │ (dataset)│    │ (scores) │     │
│   └──────────┘    └──────────┘    └──────────┘     │
└─────────────────────────────────────────────────────┘
```

**En resumen**: los prompts se versionan → se despliegan via labels → las interacciones generan traces → los traces se evalúan automáticamente → los resultados alimentan datasets → los datasets permiten experiments comparativos → los experiments guían mejoras al prompt → el ciclo se repite.

---

## 2. Conceptos Clave (Glosario)

| Concepto | Qué es | Analogía |
|----------|--------|----------|
| **Trace** | Registro completo de una interacción (input→spans→output) | Un request log enriquecido |
| **Span** | Sub-operación dentro de un trace (LLM call, tool call, etc.) | Un span de OpenTelemetry |
| **Score** | Calificación numérica de un trace/span (0.0–1.0) | Un test assertion |
| **Prompt Version** | Snapshot inmutable de un prompt | Un commit de git |
| **Label** | Puntero movible a una versión (`production`, `staging`) | Una branch/tag de git |
| **Dataset** | Colección de test cases (input + expected_output) | Una test suite |
| **Experiment** | Corrida de un dataset contra una configuración | Un `pytest` run |
| **Evaluator** | Función que asigna scores (LLM-as-judge, regex, humano) | Un test assertion function |
| **Annotation Queue** | Cola de revisión humana para traces | Un code review queue |

---

## 3. Prompt Management

### Cómo funcionan los prompts en LocalForge

Los prompts se almacenan en **cuatro capas** (en orden de prioridad):

1. **Cache en memoria** (`_active_prompts` dict) — más rápido
2. **SQLite** (`prompt_versions` tabla) — persistente, con historial
3. **Langfuse** (label `production`) — fallback extra, editable desde la UI web
4. **Registry hardcoded** (`PROMPT_DEFAULTS`) — defaults de código

```python
# Cadena de fallback (app/eval/prompt_manager.py)
content = cache → DB → Langfuse → registry → default_param → ValueError
```

### Workflow para cambiar un prompt

```
1. Crear nueva versión:    /prompts system_prompt       ← ver versiones
2. Evaluar candidato:      /approve-prompt system_prompt 3
   → Corre eval automático (LLM-as-judge contra dataset)
   → Activa la versión
   → Sincroniza a Langfuse con label "production"
3. Invalidar cache:        (automático)
```

### Qué NO hacer

- **No editar prompts hardcoded** en el código fuente. Usar `/approve-prompt`.
- **No cambiar prompts en Langfuse UI** sin entender que el cache local persiste hasta reinicio o invalidación.
- **No usar `think: True`** en prompts binarios/JSON (guardrails, summarizer, consolidator).

---

## 4. Scores — Las Métricas del Sistema

Los scores son la unidad básica de medición. LocalForge emite scores automáticos y acepta scores humanos:

### Scores automáticos (source=`system`)

| Score | Rango | Cuándo se emite |
|-------|-------|----------------|
| `guardrail_not_empty` | 0/1 | Cada respuesta — ¿está vacía? |
| `guardrail_language_match` | 0/1 | Cada respuesta — ¿idioma correcto? |
| `guardrail_no_pii` | 0/1 | Cada respuesta — ¿contiene PII? |
| `context_fill_rate` | 0–1 | Cada request — % del context window usado |
| `classify_upgrade` | 0/1 | Cuando el classifier base dice "none" y se re-clasifica |
| `goal_completion` | 0/1 | Fin de sesión agéntica — ¿completó el objetivo? |

### Scores humanos (source=`human`/`user`)

| Score | Cómo se genera |
|-------|---------------|
| `human_rating` | Comando `/rate 1-5` del usuario |
| `human_feedback` | Comando `/feedback <texto>` (análisis de sentimiento) |
| `user_reaction` | Emoji en WhatsApp (👍=1.0, 👎=0.0, otros=0.5) |

### Por qué importan los scores

Los scores alimentan **tres sistemas automáticos**:

1. **Dataset Curation**: Scores bajos (<0.3) → failure entry. Scores altos (≥0.8 system + ≥0.8 user) → golden entry.
2. **Annotation Queues**: Filtros en Langfuse por scores para revisión humana.
3. **Dashboards**: Tendencias de calidad, detección de regresiones.

---

## 5. Evaluaciones — LLM-as-Judge

### Qué es LLM-as-Judge

En vez de revisar cada respuesta manualmente, usamos **otro LLM** como juez. Le damos el input, el output, y un criterio de evaluación, y nos dice si la respuesta es buena.

```
┌─────────────┐     ┌─────────────┐     ┌──────────┐
│ User Input  │────▶│ Bot Output  │────▶│ LLM Judge│
│             │     │             │     │ "¿Es     │
│ "¿Cuándo    │     │ "El lunes   │     │  correcto│
│  es el      │     │  14 de      │     │  ?"      │
│  partido?"  │     │  marzo..."  │     │ → yes/no │
└─────────────┘     └─────────────┘     └──────────┘
```

### Dónde se usa en LocalForge

1. **`activate_with_eval()`** — Antes de aprobar un prompt, se corre eval contra el dataset.
2. **`run_eval.py`** — Script offline que corre benchmark completo.
3. **Langfuse Evaluators** — Evaluadores configurables desde la UI (sin código).

### Limitaciones a tener en cuenta

- Los LLM judges tienen ~80-90% de acuerdo con humanos. No son perfectos.
- `think=False` es **obligatorio** para prompts de judge (evita divagar).
- El modelo judge debe ser al menos tan capaz como el modelo evaluado.

---

## 6. Datasets — La Test Suite

### Tipos de entries en el dataset

| Tipo | Qué representa | Cómo se genera |
|------|----------------|----------------|
| **golden** | Respuesta buena (confirmada o candidata) | Automático: scores altos |
| **correction** | Par input/output corregido por usuario | `/feedback` + detección de corrección |
| **failure** | Respuesta problemática | Automático: scores bajos o guardrail fallo |

### Cómo crece el dataset

El dataset crece **automáticamente** via `maybe_curate_to_dataset()`:

```
Interacción → Scores → Evaluación automática:
  - Scores bajos?     → failure entry (con tags guardrail:X)
  - Scores altos + 👍? → golden (confirmed=True)
  - Scores altos, sin señal? → golden (confirmed=False, candidate)
```

También crece manualmente:
- Usuario corrige respuesta → `add_correction_pair()`
- Annotation queues → revisión humana → scores que promueven candidates a golden

### Sync a Langfuse

Los entries `golden` y `correction` se sincronizan a Langfuse Datasets automáticamente, con `source_trace_id` para linkear cada item al trace original. Esto permite ver el contexto completo de cada test case.

---

## 7. Experiments — Comparar Versiones

### Qué es un experiment

Un experiment corre **todo el dataset** (o un subset) contra una configuración específica, y mide la accuracy.

### Cómo correr un experiment

```bash
# Offline benchmark (sin levantar la app)
python scripts/run_eval.py --db data/localforge.db --threshold 0.7

# Con sync a Langfuse (para tracking visual)
python scripts/run_eval.py --db data/localforge.db --langfuse
```

### Qué pasa internamente

1. Fetch entries con `expected_output` del dataset
2. Por cada entry: generar respuesta con el modelo actual
3. LLM-as-judge: ¿la respuesta actual == expected?
4. Calcular accuracy = correct / total
5. Exit code 0 si accuracy ≥ threshold, 1 si no (útil para CI)

### En Langfuse UI

Ir a `Datasets → localforge-eval → Runs` para ver experiments comparados side-by-side.

---

## 8. Annotation Queues — Revisión Humana

### Qué son

Colas de trabajo donde humanos revisan traces del bot y les asignan scores.

### Queues configuradas en LocalForge

| Queue | Filtro | Propósito |
|-------|--------|-----------|
| Guardrail Failures | `guardrail_*` < 0.5 | Revisar respuestas que fallaron checks |
| Agent Review | tag `agent`, `goal_completion` < 1.0 | Revisar sesiones agénticas incompletas |
| New Users | < 5 traces previos | Mejorar primera impresión |

### Workflow de anotación

1. Abrir la queue en Langfuse UI (`Human Annotation`)
2. Revisar el trace: leer input → output → spans intermedios
3. Calificar con la escala definida (pass/fail o 1-5)
4. Opcionalmente agregar un comentario
5. Click "Complete + next" para avanzar

### Por qué importa

Los scores humanos (`source="human"`) son los más valiosos:
- Un score humano ≥ 0.8 promueve un candidate a golden confirmed
- Un score humano < 0.3 degrada un entry a failure
- Estos feeds realimentan el dataset y mejoran evaluaciones futuras

---

## 9. Spans — Qué Se Instrumenta

Cada interacción genera un árbol de spans. Los spans con nombre `llm:*` son LLM calls, los `tool:*` son tool executions:

```
interaction (root trace)
├── phase_ab (context loading)
├── llm:classify_intent (generation)
├── ontology:enrich (span)
├── llm:iteration_0 (generation) ← LLM principal
│   ├── tool:get_weather (tool)
│   └── tool:search_notes (tool)
├── llm:iteration_1 (generation) ← segunda iteración si hubo tools
├── guardrails (span)
├── memory:flush (generation) ← extracción de hechos pre-summarize
├── llm:summarize (generation) ← resumen de conversación
├── memory:consolidation (generation) ← dedup de memorias
├── embedding:backfill (span)
└── automation:evaluate (span)
```

### Cómo leer spans en Langfuse

1. Ir a `Traces` → click en un trace
2. Ver el waterfall de spans (tiempos, latencias)
3. Click en un span para ver input/output/metadata
4. Los generations muestran tokens de entrada/salida y modelo usado

---

## 10. Tu Checklist de Primeros Pasos

- [ ] Levanta el stack: `docker compose --profile dev up -d`
- [ ] Crea cuenta en `http://localhost:3000`
- [ ] Copia las API keys al `.env` (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`)
- [ ] Envía un mensaje al bot y verifica que aparece un trace en Langfuse
- [ ] Explora el trace: spans, scores, tiempos
- [ ] Usa `/rate 5` y verifica que aparece un score `human_rating`
- [ ] Corre `python scripts/run_eval.py --db data/localforge.db` y lee los resultados
- [ ] Crea una Annotation Queue de prueba en la UI
- [ ] Revisa los prompts: ve a `Prompts` tab en Langfuse

---

## 11. Recursos

- **Código**: `app/tracing/` (recorder, context), `app/eval/` (dataset, prompt_manager)
- **Docs**: `docs/features/48-langfuse_v3.md` (arquitectura completa)
- **Dashboard**: `http://localhost:3000` (Langfuse UI)
- **Script de eval**: `scripts/run_eval.py`
- **Patrones**: `docs/PATTERNS.md` § Tracing, § Eval & Prompt Engineering
