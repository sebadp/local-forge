# Métricas y Benchmarking en Sistemas LLM — Guía de Onboarding

> **Para quién es esto:** desarrollador junior que se une al proyecto y quiere entender
> cómo medimos la salud y la calidad del sistema, más allá de "¿funciona o no funciona?".
>
> **Prerequisito:** haber leído el `CLAUDE.md` principal y entender la arquitectura básica
> (FastAPI + Ollama + SQLite + WhatsApp/Telegram).

---

## 1. Por qué medir un LLM es diferente a medir software tradicional

En software convencional, un bug es determinista: la misma entrada siempre produce el
mismo error. Si `divide(10, 0)` falla hoy, fallará mañana, y tu test lo captura.

En un sistema LLM, la falla es **probabilística y contextual**:

- El mismo mensaje puede generar una respuesta correcta el 90% de las veces.
- El 10% restante puede ser un error sutil (idioma incorrecto, dato inventado,
  herramienta equivocada) que ningún test unitario captura.
- La calidad de la respuesta depende de **todo el contexto** que el modelo recibió:
  memorias del usuario, historial de la conversación, proyectos activos, instrucciones
  del sistema. Si alguna de esas piezas es irrelevante o está mal ordenada, el modelo
  responde peor aunque el código sea correcto.

Esto significa que necesitamos **dos tipos de métricas**:
1. **Métricas de sistema** (latencia, errores, throughput) — igual que cualquier API.
2. **Métricas de calidad** (¿respondió bien?, ¿en el idioma correcto?, ¿usó la herramienta
   adecuada?) — específicas de LLMs.

---

## 2. Las tres capas del stack de métricas

Podés pensar el sistema como tres capas apiladas. Cada capa tiene sus propias preguntas
y sus propias métricas.

```
┌─────────────────────────────────────────────────────────┐
│  CAPA 3: Eficacia del Agente                            │
│  ¿Cumplió el objetivo? ¿Eligió las herramientas        │
│  correctas? ¿Cuántos pasos necesitó?                    │
├─────────────────────────────────────────────────────────┤
│  CAPA 2: Calidad del Contexto                           │
│  ¿El contexto que le dimos al LLM era relevante?        │
│  ¿Estaba bien ordenado? ¿Se llenó el contexto?          │
├─────────────────────────────────────────────────────────┤
│  CAPA 1: Rendimiento del Pipeline                       │
│  ¿Cuánto tardó? ¿Dónde está el cuello de botella?       │
│  ¿Cuántos tokens consumió?                              │
└─────────────────────────────────────────────────────────┘
```

**Un error común de los equipos nuevos:** medir solo la Capa 1. Si la latencia es de 2s
pero el agente usó 4 herramientas cuando debería haber usado 1, hay un problema de
eficacia que la latencia no te muestra.

---

## 3. Conceptos clave con ejemplos concretos

### 3.1 Trace (Traza)

Una **traza** representa una sola interacción de principio a fin. Cuando un usuario manda
"¿cuánto es 15% de 340?", se crea una traza que cubre todo: recibir el mensaje →
construir el contexto → llamar al LLM → ejecutar la calculadora → devolver la respuesta.

En la base de datos: tabla `traces`.

```
traces
├── id: "a3f9b2e1..."     # UUID único por interacción
├── phone_number: "+54..."  # Quién mandó el mensaje
├── input_text: "cuánto es 15% de 340"
├── output_text: "El 15% de 340 es 51."
├── started_at: "2026-03-08 14:00:00"
├── completed_at: "2026-03-08 14:00:01.850"   # 1.85s end-to-end
└── status: "completed"
```

### 3.2 Span

Un **span** es una operación específica dentro de una traza. La traza anterior se
descompone en spans:

```
traza "a3f9b2e1"
├── phase_ab (420ms)     # Embed query + buscar memorias + historial
│   ├── embed_ms: 180ms  # Fase A: convertir el texto a vector
│   └── searches_ms: 230ms  # Fase B: buscar en SQLite en paralelo
├── llm:classify_intent (890ms)  # ¿Qué tipo de tarea es?
├── tool_loop (1100ms)   # LLM + ejecución de calculadora
│   ├── llm:iteration_1 (980ms)  # LLM decide usar 'calculator'
│   └── tool:calculator (12ms)   # Ejecución de la tool
├── guardrails (8ms)     # ¿La respuesta está bien? Sin PII, idioma correcto
└── delivery (45ms)      # Enviar el mensaje a WhatsApp
```

En la base de datos: tabla `trace_spans`, con `latency_ms` para cada span.

### 3.3 Score (Puntuación)

Un **score** es una señal de calidad asociada a una traza. Puede venir del sistema o del
usuario:

| Score | Fuente | Valor | Significado |
|---|---|---|---|
| `not_empty` | sistema (guardrail) | 1.0 | La respuesta no estaba vacía |
| `language_match` | sistema (guardrail) | 0.0 | Respondió en idioma incorrecto ❌ |
| 👍 (reacción) | usuario | 1.0 | Usuario aprobó la respuesta |
| `user_correction` | sistema (detección) | 0.0 | Usuario corrigió al bot |
| `/rate 5` | usuario explícito | 1.0 | Rating máximo |

En la base de datos: tabla `trace_scores`.

### 3.4 Guardrails

Los **guardrails** son checks determinísticos (sin LLM) que se ejecutan sobre cada
respuesta antes de enviarla. Funcionan como una red de seguridad:

```
Respuesta del LLM
      │
      ▼
  check_not_empty        → ¿Hay contenido?
  check_language_match   → ¿Mismo idioma que el usuario?
  check_no_pii           → ¿Sin números de teléfono/DNI?
  check_excessive_length → ¿Menos de 8000 chars?
  check_no_raw_tool_json → ¿Sin JSON crudo de herramientas?
      │
      ▼
  ¿Alguno falló? → Remediation (un solo reintento al LLM)
  ¿Todos pasaron? → Enviar respuesta
```

**Política de fail-open:** si un guardrail lanza una excepción (ej. `langdetect` falla en
texto muy corto), se considera "pasado". La alternativa (fail-closed) haría que un bug
en los checks bloquee todas las respuestas — inaceptable para un sistema de mensajería.

### 3.5 Context Rot (Degradación de Contexto)

Un fenómeno documentado en 2025 por Chroma Research y Stanford: **todos los modelos
frontier empeoran a medida que crece el contexto**, incluso cuando el contexto cabe
dentro de la ventana.

Hay dos efectos documentados:

**"Lost in the Middle"** (Stanford, TACL 2024): el modelo ignora información que está
enterrada en el medio del contexto. Recuerda bien lo que está al principio y al final.
Performance cae >30% cuando la info relevante queda en el medio.

**Context Rot** (Chroma, 2025): rendimiento decae monotónicamente a medida que crece
el total de tokens, aunque la info clave esté al principio. Los 18 modelos frontier
testeados exhiben este comportamiento.

**Implicación para LocalForge:** el `ContextBuilder` ordena el system message así:
```
[system_prompt] → [memorias] → [daily_logs] → [notas] → [proyectos] → [historial]
```
Si las memorias son irrelevantes o el historial es muy largo, context rot activa.
Por eso medimos `token_budget`, `search_mode`, y (próximamente) `context_fill_rate`.

### 3.6 Tool Selection Accuracy vs Tool Parameter Accuracy

Cuando el LLM invoca una herramienta, hay dos cosas que pueden salir mal:

1. **Tool selection**: eligió la herramienta equivocada. Pidió "clima de Buenos Aires"
   y el LLM llamó a `calculator` en vez de `get_weather`.

2. **Tool parameter accuracy**: eligió bien pero llenó mal los parámetros. Llamó a
   `get_weather` pero pasó `city: "Bs As"` en vez de `city: "Buenos Aires"` y la
   API falló.

Amazon (2026) identifica estas como las dos métricas más importantes para agentes en
producción. LocalForge las mide indirectamente hoy (errores de ejecución de tools),
y está planificado medirlas directamente (Plan 39).

---

## 4. El stack actual de LocalForge

### 4.1 Qué se mide hoy

```
app/
├── tracing/
│   ├── context.py       # TraceContext: crea y cierra trazas, abre spans
│   └── recorder.py      # Persiste en SQLite + Langfuse (best-effort)
├── guardrails/
│   ├── checks.py        # 5 checks determinísticos
│   └── pipeline.py      # Orquesta checks, fail-open
├── context/
│   ├── token_estimator.py      # chars/4 proxy, log de budget
│   └── conversation_context.py # build_timing: embed_ms, searches_ms
├── eval/
│   └── dataset.py       # 3-tier curation: failure / golden / correction
└── database/
    └── repository.py    # get_e2e_latency_percentiles, get_latency_percentiles,
                         # get_search_hit_rate, get_eval_summary, ...

scripts/
├── baseline.py   # Snapshot pre-optimización: latencias + volumen
└── dashboard.py  # HTML autocontenido con Chart.js

skills/eval/SKILL.md  # Tools conversacionales: get_latency_stats, get_search_stats, ...
```

### 4.2 El ciclo de vida de una traza

```
mensaje llega
    │
    ├── [Background] classify_intent asyncio.create_task() ─────────────────────┐
    │                                                                             │
    ▼                                                                             │
TraceContext.__aenter__()   ← recorder.start_trace(id, phone, input, platform)  │
    │                                                                             │
    ├── span("phase_ab")                                                          │
    │       ├── ConversationContext.build()    ← embed_ms + searches_ms          │
    │       │       ├── _get_query_embedding()   [Phase A]                        │
    │       │       └── asyncio.gather(          [Phase B]                        │
    │       │               search_memories, get_history, search_notes,          │
    │       │               get_daily_logs, get_projects_summary)                │
    │       └── save_message() [concurrente]                                     │
    │           ← span metadata: embed_ms, searches_ms, search_mode             │
    │                                                                             │
    ├── [await classify_task] ◄───────────────────────────────────────────────┘  │
    │       ↳ si base_result="none" → re-classify con contexto                   │
    │                                                                             │
    ├── span("tool_loop")                                                         │
    │       ├── span("llm:iteration_1")  ← input/output tokens en metadata      │
    │       │       ├── span("tool:calculator")                                  │
    │       │       └── span("tool:get_weather")                                 │
    │       └── span("llm:iteration_2")  ← si hubo más tool calls               │
    │                                                                             │
    ├── span("guardrails")  ← passed, failed_checks en metadata                 │
    │                                                                             │
    └── span("delivery")                                                          │
            │
            ▼
    TraceContext.__aexit__()  ← recorder.finish_trace(status, output, wa_id)
    [background] maybe_curate_to_dataset()
    [background] _save_self_correction_memory() (si guardrail falló)
```

### 4.3 Cómo usar las herramientas de métricas desde WhatsApp

Una vez conectado al bot de WhatsApp/Telegram, podés consultar métricas directamente:

```
"dame las latencias del pipeline de los últimos 7 días"
→ get_latency_stats(span_name="all", days=7)

"cómo está el hit rate de búsqueda semántica?"
→ get_search_stats(days=7)

"muéstrame las últimas 5 fallas"
→ list_recent_failures(limit=5)

"qué score tiene la traza a3f9b2?"
→ diagnose_trace(trace_id="a3f9b2")

"evaluá los últimos 10 pares de corrección"
→ run_quick_eval(limit=10)
```

### 4.4 Cómo correr el baseline desde terminal

```bash
# Capturar el estado actual como snapshot pre-optimización
python scripts/baseline.py --db data/localforge.db --days 7

# Genera:
# - Output en terminal con tablas formateadas
# - reports/baseline_plan36_20260308_143000.json  (para comparar después)

# Dashboard HTML con Chart.js
python scripts/dashboard.py --db data/localforge.db --days 30 --output reports/dashboard.html
# → Abrir reports/dashboard.html en el browser
```

---

## 5. Cómo interpretar los datos

### 5.1 Latencias: qué es normal

Para un sistema local (Ollama + qwen3:8b), los rangos típicos son:

| Span | p50 normal | p50 preocupante | Causa común |
|---|---|---|---|
| `end_to_end` | < 3s | > 5s | LLM lento o N+1 en queries |
| `phase_ab` (total) | < 600ms | > 1.5s | Embed lento o DB sin índices |
| `phase_a` (embed) | < 200ms | > 500ms | Ollama saturado |
| `phase_b` (searches) | < 300ms | > 800ms | Queries secuenciales o sin índice |
| `llm:classify_intent` | < 1.5s | > 3s | Model load o cola en Ollama |
| `tool_loop` (1 iter) | < 2s | > 4s | LLM lento para tools |
| `guardrails` | < 20ms | > 100ms | `langdetect` bloqueando event loop |
| `delivery` | < 200ms | > 500ms | WhatsApp API lenta |

### 5.2 Guardrails: tasas de pass rate

```sql
-- Consultar directamente en SQLite:
SELECT name, AVG(value) as pass_rate, COUNT(*) as n
FROM trace_scores
WHERE source = 'system'
GROUP BY name ORDER BY pass_rate ASC;
```

| Pass rate | Interpretación | Acción |
|---|---|---|
| > 0.95 | Normal | Ninguna |
| 0.85 - 0.95 | Revisar | Analizar fallas recientes |
| < 0.85 | Problema | Revisar system prompt o remediation prompt |

Un `language_match` en 0.80 significa que el 20% de las respuestas salen en el idioma
incorrecto — eso es una señal clara para ajustar el system prompt.

### 5.3 Search modes: qué signfica cada uno

El campo `search_mode` en los spans de `phase_ab` indica cómo se recuperaron las memorias:

| Modo | Qué pasó | Implicación |
|---|---|---|
| `semantic` | Búsqueda por similitud vectorial, resultados pasaron el threshold | Ideal |
| `fallback_threshold` | Búsqueda vectorial, pero ningún resultado pasó el threshold → forzó top-3 | Umbral muy estricto o memorias poco relevantes |
| `full_fallback` | No había embedding (vec_available=False) o Ollama falló | Respuesta basada en memorias no filtradas |

Si `fallback_threshold` > 30%, considerar bajar `memory_similarity_threshold` en config.

### 5.4 Dataset: el asset más valioso

El dataset vivo tiene tres tipos de entradas:

```
eval_dataset
├── entry_type="golden"     # Respuesta confirmada como correcta
│   └── confirmed=True      # Usuario positivo + guardrails OK
├── entry_type="golden"     # Candidato a validar
│   └── confirmed=False     # Guardrails OK, sin señal de usuario
├── entry_type="failure"    # Guardrail falló o usuario negativo
│   └── tags: ["guardrail:language_match", ...]
└── entry_type="correction" # Par (respuesta mala, respuesta esperada)
    └── expected_output: "respuesta correcta que quería el usuario"
```

Los `correction` pairs son los más valiosos: tienen tanto la respuesta incorrecta como
la correcta. Son el insumo del `run_quick_eval` (LLM-as-judge offline).

---

## 6. Fenómenos comunes y cómo diagnosticarlos

### 6.1 "El bot respondió en inglés a un mensaje en español"

```bash
# 1. Ver las últimas fallas de language_match
diagnose_trace <trace_id>

# 2. Ver tendencia
SELECT DATE(created_at), COUNT(*) as failures
FROM trace_scores
WHERE name = 'language_match' AND value = 0.0
GROUP BY DATE(created_at) ORDER BY 1 DESC;

# 3. Si es frecuente: revisar system prompt o prompt de remediation
/prompts system_prompt
```

### 6.2 "El bot tardó 8 segundos en responder"

```bash
# 1. Capturar la traza lenta
diagnose_trace <trace_id>

# 2. Ver qué span fue el cuello de botella
# (buscar el span con latency_ms más alto)

# 3. Si fue tool_loop: ¿cuántas iteraciones?
SELECT name, latency_ms FROM trace_spans
WHERE trace_id = '<id>' AND name LIKE 'llm:iteration_%'
ORDER BY started_at;

# 4. Si fue phase_ab: ¿embed o searches?
SELECT json_extract(metadata, '$.embed_ms'),
       json_extract(metadata, '$.searches_ms')
FROM trace_spans
WHERE trace_id = '<id>' AND name = 'phase_ab';
```

### 6.3 "El bot llamó 3 herramientas para algo que debería hacer con 1"

Esto es **tool inefficiency** — el agente necesitó más pasos de los necesarios. Señales:
- `tool_loop` tiene 3+ iteraciones cuando la tarea es simple
- El usuario corrigió algo ("no, eso no era lo que pedía")

```bash
# Ver promedio de iteraciones por trace
SELECT AVG(iter_count) FROM (
  SELECT trace_id, COUNT(*) as iter_count FROM trace_spans
  WHERE name LIKE 'llm:iteration_%' GROUP BY trace_id
);

# Ver distribución: ¿cuántas trazas tuvieron 3+ iteraciones?
SELECT iter_count, COUNT(*) FROM (
  SELECT trace_id, COUNT(*) as iter_count FROM trace_spans
  WHERE name LIKE 'llm:iteration_%' GROUP BY trace_id
) GROUP BY iter_count ORDER BY iter_count;
```

### 6.4 "El contexto está llegando casi al límite"

Los logs estructurados emiten una alerta:
```json
{"level": "WARNING", "message": "context.budget", "status": "near_limit",
 "token_estimate": 28500, "limit": 32000, "pct": 89.0}
```

Acciones:
1. Ver qué sección ocupa más (`estimate_sections` en token_estimator.py)
2. Si es `history`: reducir `history_verbatim_count` en config
3. Si son `memories`: bajar `semantic_search_top_k`
4. Si es `daily_logs`: reducir `daily_log_days`

---

## 7. Preguntas frecuentes

**¿Por qué chars/4 y no el tokenizador real de qwen3?**
Ejecutar el tokenizador de qwen3 sobre cada mensaje añade ~50-100ms de latencia. Para
alertas de presupuesto, una aproximación de ±20% es suficiente. Si el contexto está al
90% según chars/4, casi seguro está entre 72% y 108% real — suficiente para actuar.

**¿Por qué fail-open en guardrails?**
Si un check lanza una excepción (bug en `langdetect`, regex malformado), fail-closed
bloquea 100% de las respuestas. Eso es catastrófico para un sistema de mensajería.
Fail-open deja pasar una respuesta potencialmente mala en ese caso específico — mucho
mejor que silenciar a todos los usuarios.

**¿Por qué LLM-as-judge con `think=False`?**
qwen3:8b tiene un modo de "chain-of-thought" que a veces lleva al modelo a razonar hacia
una conclusión y luego contradecirla en el texto final. Para prompts binarios (yes/no),
ese razonamiento adicional hace que el parseo sea poco confiable. `think=False` desactiva
el CoT y da respuestas más directas.

**¿Por qué el dataset tiene tres tiers en vez de solo "buenas" y "malas" respuestas?**
Porque "buena" tiene grados. Una respuesta que pasó guardrails y recibió 👍 es confiable
como golden. Una que pasó guardrails pero sin señal de usuario es candidata —
potencialmente buena pero no confirmada. Un failure es útil para análisis de regresi pero
no como ground truth de entrenamiento. Los tres tiers permiten priorizar: correction >
confirmed golden > candidate golden > failure.

**¿Qué es `contextvars.ContextVar` y por qué se usa?**
Es una variable de contexto de asyncio: cuando se crea una task con `asyncio.create_task()`,
hereda automáticamente el contexto del padre, incluyendo el trace activo. Sin esto,
habría que pasar `trace_ctx` como parámetro a cada función del pipeline — rompiendo todas
las firmas existentes.

**¿Cómo sé si una métrica nueva que agrego es confiable?**
Regla práctica: si la métrica depende de un proceso no-determinista (LLM), necesita al
menos 30 muestras para ser estable. Si depende de queries SQL sobre spans, es tan
confiable como la cantidad de trazas en el rango de tiempo. Con <10 trazas, los
percentiles son ruidosos — no tomes decisiones con eso.

---

## 8. Recursos del proyecto

| Documento | Dónde encontrarlo | Para qué sirve |
|---|---|---|
| Arquitectura general | `CLAUDE.md` | Patrones y decisiones de diseño |
| Métricas y benchmarking | `docs/features/37-metricas_benchmarking.md` | Por qué medimos lo que medimos |
| Plan Metrics Hardening | `docs/exec-plans/38-metrics_hardening_prp.md` | Qué se implementó en Plan 38 |
| Plan Performance | `docs/exec-plans/36-performance_optimization_prd.md` | Próximas optimizaciones |
| Plan Agent Metrics v2 | `docs/exec-plans/39-agent_metrics_prd.md` | Lo que viene (Plan 39) |
| Script baseline | `scripts/baseline.py` | Snapshot pre-optimización |
| Dashboard HTML | `scripts/dashboard.py` | Vista general con charts |
