# LLM-as-Judge: Best Practices for Automated Evaluation (2025-2026)

> Artículo de referencia interna para el proyecto LocalForge.
> Última actualización: 2026-03-15

## Índice

1. Introducción y Motivación
2. Taxonomía de Enfoques
3. G-Eval: Chain-of-Thought Evaluation
4. QAG: Question-Answer Generation
5. Evaluación de Tool Calling y Agentes
6. Sesgos y Mitigación
7. Implementación con Modelos Pequeños (Self-Hosted)
8. Decisiones de Diseño para LocalForge
9. Fuentes

---

## 1. Introducción y Motivación

La evaluación de sistemas LLM es fundamentalmente más simple que la generación: clasificar
contenido requiere menos carga cognitiva que crearlo. LLM-as-Judge aprovecha esta asimetría
usando un LLM para evaluar las salidas de otro modelo (o de sí mismo) siguiendo una rúbrica
en lenguaje natural.

**Datos clave:**
- LLM judges logran **>80% de acuerdo** con preferencias humanas, igualando la consistencia
  inter-anotador humana (Zheng et al., 2023)
- AlpacaEval muestra **0.98 correlación Spearman** con evaluación humana en Chatbot Arena
- Ahorro de **500x-5000x en costos** vs. revisión humana
- GPT-4 como judge muestra **0.514 correlación Spearman** con ratings humanos en summarización

El enfoque es especialmente valioso para sistemas que usan tool calling, donde la evaluación
debe cubrir tanto la calidad de la respuesta como la corrección en el uso de herramientas.

---

## 2. Taxonomía de Enfoques

### 2.1 Comparación Pairwise

Se presentan dos respuestas al mismo query y el LLM selecciona la superior.

**Ventajas:**
- Más estable que scoring absoluto
- Mejor alineación con preferencias humanas
- Correlación de 0.98 con Chatbot Arena

**Desventajas:**
- No escala (requiere todas las combinaciones de modelos)
- No puede evaluar outputs individuales

**Uso ideal:** A/B testing de modelos o prompts durante desarrollo.

### 2.2 Scoring Directo (Pointwise)

Un output individual recibe un rating numérico (típicamente 1-5) contra una rúbrica.

**Variantes:**
- **Sin referencia (reference-free):** Evalúa tono, claridad, seguridad
- **Con referencia (reference-based):** Compara contra ground truth

**Hallazgo clave:** El scoring directo es "menos estable" que pairwise debido a mecanismos
de scoring internos inconsistentes en los LLMs. Requiere calibración con:
- Rúbricas de grading que expliquen cada nivel
- Few-shot examples que calibren el scoring
- Logprob-weighted averages en vez de selección categórica

### 2.3 Clasificación Binaria

La más simple y confiable: YES/NO o PASS/FAIL.

**Hallazgo clave:** "Es más fácil obtener resultados precisos con dos opciones simples que
decidir si una respuesta específica merece 73 vs. 82."

Escalas de 3 opciones (relevant/irrelevant/partially) reducen el false forcing.

### 2.4 QAG (Question-Answer Generation)

Descompone la evaluación en sub-preguntas binarias YES/NO, luego agrega los resultados.

**Ventajas:**
- Más confiable que scoring abierto, especialmente con modelos pequeños
- Cada sub-pregunta tiene CoT implícito
- Fácil de parsear programáticamente

**Ejemplo:**
```
1. Does the response contain a specific time? YES/NO
2. Does it include timezone information? YES/NO
3. Were the correct tools called? YES/NO
VERDICT: PASS/FAIL
```

---

## 3. G-Eval: Chain-of-Thought Evaluation

G-Eval (Liu et al., 2023) es el framework más citado para LLM-as-Judge. Opera en 3 fases:

### 3.1 Generación de Pasos de Evaluación

El LLM recibe criterios en lenguaje natural y genera automáticamente pasos de evaluación
concretos. Ejemplo: el criterio "coherencia" se descompone en:
1. Identificar el tema principal
2. Comparar cobertura y orden lógico
3. Asignar score 1-5

### 3.2 Judging con CoT

Los pasos generados guían al LLM judge para evaluar el output.

**Hallazgo crítico:** Las conclusiones deben generarse ANTES del score. "Las conclusiones
generadas por el modelo no están sustentadas por las explicaciones generadas después."
Generar razonamiento primero mejora tanto la precisión como la explicabilidad.

### 3.3 Scoring con Token Probabilities

En vez de confiar en el score raw, G-Eval pondera los juicios usando log-probabilities
a nivel de token:
- Solicitar 20 scores y ponderar por probabilidad del token
- Provee precisión fina más allá de escalas categóricas
- Reduce sesgo al diferenciar entre outputs de calidad similar

### 3.4 Form-Filling Paradigm

G-Eval usa campos flexibles:
- **Input**: Query del usuario
- **Actual Output**: Respuesta generada
- **Expected Output**: Ground truth
- **Retrieval Context**: Documentos recuperados (para RAG)
- **Context**: Información que el LLM debería usar

### 3.5 Limitaciones

- Auto-generación de pasos introduce variabilidad probabilística entre ejecuciones
- Para producción, usar pasos explícitos (hardcoded) en vez de auto-generados
- Token probabilities no disponibles en todos los providers (Ollama sí las expone)

---

## 4. QAG: Question-Answer Generation

### 4.1 Fundamento

Descompone evaluaciones complejas en determinaciones binarias YES/NO. Es más confiable
que scoring abierto porque:
- Cada sub-pregunta es atómica y verificable
- Reduce la carga cognitiva del judge
- El CoT está implícito en la justificación de cada respuesta

### 4.2 Patrón de Implementación

```
Evaluate this response. Answer each criterion.

Input: {query}
Expected: {expected_output}
Actual: {actual_output}

1. CORRECTNESS: Does the response answer correctly? (YES/NO + reason)
2. COMPLETENESS: Does it address the full question? (YES/NO + reason)
3. TOOL_USAGE: Were the correct tools called? (YES/NO + reason)

VERDICT: PASS or FAIL
```

### 4.3 Parsing

Format line-based (no JSON) para confiabilidad con modelos pequeños:
- Buscar "YES" o "NO" en cada línea numerada
- Extraer VERDICT de la línea final
- Score = promedio de criterios binarios
- Fallback: si parsing falla, usar score >= 0.5 como verdict

### 4.4 Cuándo usar QAG vs G-Eval

| Criterio | QAG | G-Eval |
|----------|-----|--------|
| Modelo judge | Pequeño (<=13B) | Grande (GPT-4, Claude) |
| Confiabilidad de parsing | Alta (line-based) | Media (puede requerir JSON) |
| Granularidad | Binaria por criterio | Numérica (1-5) |
| CoT | Implícito (reason) | Explícito (evaluation steps) |
| Token probabilities | No requerido | Mejora significativa |

---

## 5. Evaluación de Tool Calling y Agentes

### 5.1 Métricas del Framework DeepEval

DeepEval organiza las métricas en 3 capas:

#### Capa de Razonamiento
- **PlanQualityMetric**: El plan es lógico, completo y eficiente?
  `Score = AlignmentScore(Task, Plan)`
- **PlanAdherenceMetric**: El agente siguió su propio plan?
  `Score = AlignmentScore((Task, Plan), Execution Steps)`

#### Capa de Acción
- **ToolCorrectnessMetric**: Seleccionó las herramientas correctas?
  `Score = Correctly Used Tools / Total Tools Called`
  Niveles de strictness: nombre, parámetros, output, secuencia, exacto
- **ArgumentCorrectnessMetric**: Generó argumentos correctos?
  `Score = Correctly Generated Parameters / Total Tool Calls`
  Evaluación LLM-based y referenceless

#### Capa de Ejecución
- **TaskCompletionMetric**: Logró el objetivo?
  `Score = AlignmentScore(Task, Outcome)`
- **StepEfficiencyMetric**: Lo logró sin pasos innecesarios?
  `Score = AlignmentScore(Task, Execution Steps)`

### 5.2 Aplicación a LocalForge

Para nuestro eval offline, implementamos una versión simplificada:

| Criterio DeepEval | Nuestra implementación |
|-------------------|----------------------|
| ToolCorrectness | `tool_usage` criterion en QAG (expected vs called tools) |
| TaskCompletion | `correctness` criterion (responde correctamente?) |
| Completeness | `completeness` criterion (cubre toda la pregunta?) |
| ArgumentCorrectness | Out of scope (schemas de eval son stubs) |
| StepEfficiency | Out of scope (no ejecutamos tools) |

---

## 6. Sesgos y Mitigación

### 6.1 Position Bias

**Severidad:** Extrema. La win-rate de Vicuna vs ChatGPT varía de **2.5% a 82.5%** dependiendo
de la posición en comparación pairwise.

**Mitigación:**
- Position switching: evaluar en ambos órdenes (A,B) y (B,A)
- Solo contar winners consistentes
- Para scoring directo (nuestro caso): no aplica (un solo output)

### 6.2 Verbosity Bias

**Severidad:** ~15% de inflación en scores. Respuestas más largas reciben mejores scores
independientemente de la calidad.

**Mitigación:**
- Length-controlled evaluation (regresión que elimina el término de longitud)
- Criterios explícitos que penalicen verbosidad o premien concisión
- En QAG: las sub-preguntas binarias son menos susceptibles a verbosity

### 6.3 Self-Enhancement Bias

**Severidad:** 5-7% de boost cuando el judge es el mismo modelo que el evaluado.

**Mitigación:**
- Usar diferente familia de modelos como judge (ideal)
- LLM jury: 3-5 modelos con majority voting (reduce sesgos 30-40%, pero 3-5x costo)
- Para nuestro caso: usamos qwen3.5:9b como judge Y como modelo evaluado,
  pero el QAG pattern reduce el impacto (sub-preguntas factuales, no subjetivas)

### 6.4 Limitaciones de Razonamiento

Los LLMs tienen dificultad evaluando respuestas que ellos mismos encontrarían difíciles.
Son vulnerables a información incorrecta en el contexto.

**Mitigación:**
- Reference-guided: incluir expected output como ancla
- Simplificar preguntas del judge (QAG)
- No pedir evaluación en dimensiones ambiguas ("likability")

---

## 7. Implementación con Modelos Pequeños (Self-Hosted)

### 7.1 Desafíos Específicos

Los modelos <13B tienen limitaciones como judges:
- Inconsistencia en seguir formatos estructurados (JSON malformado)
- Dificultad con scoring numérico (escala 1-5 inconsistente)
- Mayor susceptibilidad a verbosity bias
- Reasoning más débil para evaluaciones complejas

### 7.2 Recomendaciones para Modelos Pequeños

1. **QAG sobre G-Eval**: Sub-preguntas YES/NO son más confiables que escalas numéricas
2. **Reference guidance**: Proveer expected output para compensar reasoning limitado
3. **Formato line-based**: NO JSON — usar "1. YES - reason" que es más robusto
4. **`think=False`**: Deshabilitar chain-of-thought externo (el CoT va inline en reasons)
5. **Prompts cortos**: Menos instrucciones = mejor adherencia
6. **Parsing tolerante**: Buscar YES/NO en la línea, no requerir formato exacto
7. **Fallback determinístico**: Si parsing falla, score >= 0.5 -> PASS

### 7.3 Calibración

- **Temperature baja (0.1)** para resultados determinísticos al generar un solo score
- **Caveat**: temperaturas más bajas sesgan hacia scores más bajos
- Validar contra set humano-labeled (30-50 mínimo, 100-200 para producción)
- Target: **>75% agreement** con humanos antes de deployment

### 7.4 Híbrido LLM + Reglas

Para maximizar confiabilidad:
1. **Checks determinísticos primero**: formato, contenido vacío, keywords
2. **LLM judge para evaluación semántica**: correctness, completeness
3. **Revisión humana para edge cases**: failures que el judge no puede resolver

---

## 8. Decisiones de Diseño para LocalForge

### 8.1 Por qué QAG y no G-Eval?

| Factor | G-Eval | QAG (elegido) |
|--------|--------|---------------|
| Modelo | Requiere GPT-4+ | Funciona con qwen3.5:9b |
| Token probabilities | Requeridas para scoring fino | No necesarias |
| Formato de output | JSON/structured | Line-based (más robusto) |
| Complejidad de parsing | Alta | Baja |
| Granularidad | Score 1-5 por dimensión | YES/NO por criterio |

### 8.2 Por qué no ejecutar tools en e2e?

- `execute_tool_loop()` requiere `SkillRegistry` con handlers registrados
- Los handlers necesitan DB, APIs externas (weather, web search)
- Eval debe ser offline y sin dependencias externas
- **Compromiso**: `chat_with_tools()` con schemas stub -> LLM intenta llamar tools
  pero no se ejecutan. Judge evalúa si llamó los tools correctos.

### 8.3 Criterios del Judge

| Criterio | Qué evalúa | Cuándo aplica |
|----------|-----------|---------------|
| `correctness` | Respuesta factualmente correcta? | Siempre |
| `completeness` | Cubre toda la pregunta? | Siempre |
| `tool_usage` | Llamó los tools correctos? | Solo si entry espera tools |

**Score final** = promedio de criterios aplicables. VERDICT del LLM como override, score como fallback.

---

## 9. Fuentes

- [Confident AI — G-Eval: The Definitive Guide](https://www.confident-ai.com/blog/g-eval-the-definitive-guide)
- [Confident AI — LLM-as-Judge Complete Guide](https://www.confident-ai.com/blog/why-llm-as-a-judge-is-the-best-llm-evaluation-method)
- [Monte Carlo — LLM-As-Judge: 7 Best Practices & Evaluation Templates](https://www.montecarlodata.com/blog-llm-as-judge/)
- [Cameron R. Wolfe — Using LLMs for Evaluation (research deep dive)](https://cameronrwolfe.substack.com/p/llm-as-a-judge)
- [DeepEval — AI Agent Evaluation Metrics](https://deepeval.com/guides/guides-ai-agent-evaluation-metrics)
- [Label Your Data — LLM-as-a-Judge: 2026 Guide](https://labelyourdata.com/articles/llm-as-a-judge)
- [Evidently AI — LLM-as-a-Judge: Complete Guide](https://www.evidentlyai.com/llm-guide/llm-as-a-judge)
- [Comet — LLM-as-a-Judge: How to Build Reliable Evaluation](https://www.comet.com/site/blog/llm-as-a-judge/)
- [Towards Data Science — LLM-as-a-Judge: A Practical Guide](https://towardsdatascience.com/llm-as-a-judge-a-practical-guide/)
- [Zheng et al. 2023 — MT-Bench: Judging LLM-as-a-Judge](https://arxiv.org/abs/2306.05685)
