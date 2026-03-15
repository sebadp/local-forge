# PRD: Regression Eval Suite — LLM-as-Judge Enhancement (Plan 49)

## Objetivo y Contexto

El benchmark offline (`scripts/run_eval.py`, Plan 30) fue implementado con un judge binario
simplista y sin soporte de tool calling en e2e. Los resultados de la evaluación del 2026-03-15
revelan:

| Modo | Score | Estado | Causa raíz |
|------|-------|--------|-----------|
| classify | 89% (73/82) | PASS | selfcode al 42.9%, 1 bug de dataset |
| e2e | 22.4% (13/58) | FAIL | `client.chat()` sin tools + judge binario |

### Gaps confirmados en código

| # | Gap | Evidencia |
|---|-----|-----------|
| 1 | E2E sin tools | `run_eval.py:263` — `client.chat()` raw, no usa `chat_with_tools()` |
| 2 | Judge binario sin rúbrica | `run_eval.py:57-65` — "yes/no" sin CoT ni criterios |
| 3 | Sin detalles en output | `_run_e2e()` no guarda actual_response, judge_reasoning, latencia |
| 4 | Classifier prompt débil para selfcode | `router.py:196-227` — solo 3 ejemplos, 42.9% accuracy |
| 5 | Dataset bug | `seed_eval_dataset.py:78` — "noticias" esperaba `search` pero `news` es categoría separada |

### Research aplicada

Basado en investigación de LLM-as-Judge (2025-2026):
- **QAG pattern** (sub-preguntas YES/NO) > scoring numérico para modelos pequeños
- **CoT antes del veredicto** — mejora ~15% accuracy
- **Reference-guided** — incluir expected output como ancla
- **Tool-aware evaluation** — separar tool_correctness de response quality
- `think=False` obligatorio para prompts de judge

## Alcance (In Scope & Out of Scope)

### In Scope

- Fix `_run_e2e()` para usar `chat_with_tools()` con tool schemas
- Nuevo sistema de judge multi-criteria (QAG: correctness + completeness + tool_usage)
- Flag `--verbose` para output detallado (actual_response, judge_reasoning, latencia, tools)
- Timing (`time.monotonic()`) en los 3 modos (classify, tools, e2e)
- Fix dataset entry "noticias" (search -> news)
- 8 nuevos ejemplos en classifier prompt (selfcode, projects, math, news)
- Langfuse: sync scores por criterio (correctness, completeness, tool_usage)

### Out of Scope

- Ejecución real de tools en e2e (requeriría SkillRegistry completo)
- A/B testing de versiones del judge
- Fine-tuning de modelo evaluador
- Prometheus/modelo especializado como judge
- Cambios a `run_quick_eval` skill (tiene su propio judge inline)

## Casos de Uso Críticos

### 1. E2E evalúa "Que hora es?" — falla porque no hay tools

**Antes:** `client.chat()` -> LLM dice "No tengo acceso a la hora" -> judge dice "no" -> FAIL.
**Después:** `chat_with_tools(tools=[get_current_datetime,...])` -> LLM genera tool_call
`get_current_datetime` -> judge evalúa tool_usage: "Called correct tool? YES" -> PASS.

### 2. E2E evalúa "Cuanto es 15*7+3?" — pasa pero sin métricas

**Antes:** Score binario 1.0, sin detalle.
**Después:** Score 1.0 con criteria `correctness=YES completeness=YES`, latency=850ms.

### 3. Developer ejecuta eval con `--verbose` para diagnosticar fallas

**Antes:** Solo "FAIL 0%" sin contexto.
**Después:**
```
288      selfcode     FAIL     33%          'Mostra el outline de app/config.py'
         correctness=NO completeness=NO tool_usage=YES
         actual: 'Here are the main classes and functions...'
         judge: '1. NO - response lacks file structure | 2. NO - missing line numbers | 3. YES - called get_file_outline'
         tools_called: [get_file_outline]
         latency: 2340ms
```

### 4. Classifier clasifica "sin(pi/2)" como knowledge en vez de math

**Antes:** Sin ejemplo de funciones trigonométricas -> LLM lo ve como pregunta de conocimiento.
**Después:** Nuevo ejemplo `'"sin(pi/2)" -> math\n'` en prompt -> clasificación correcta.

## Restricciones Arquitectónicas

- **Modelo judge**: qwen3.5:9b (self-hosted, 9B params) — prompts deben ser concisos y estructurados
- **Sin dependencias nuevas**: eval script usa solo OllamaClient + httpx (no SkillRegistry)
- **Backward compat**: `--mode classify` y `--mode tools` sin cambios estructurales
- **`think=False`**: Obligatorio para todas las llamadas del judge
- **Fail-open**: errores del judge -> score=0 pero no crashea el eval
