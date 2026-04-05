# PRD: Benchmark Suite Expansion (Plan 62)

## Objetivo y Contexto

LocalForge tiene un regression eval suite (`scripts/run_eval.py`) con 82 seed cases y 4 modos (classify, tools, e2e, guardrails). Pero hay dimensiones críticas que no se miden:

- **Memory retrieval quality**: ¿las memorias recuperadas son relevantes para el query?
- **Agent plan quality**: ¿los planes generados son razonables para el objetivo?
- **Context saturation impact**: ¿la calidad degrada cuando el context fill > 80%?
- **Language consistency**: ¿el modelo mantiene el idioma correcto en conversaciones largas?
- **Tool hallucination**: ¿el LLM inventa tools que no existen?
- **Remediation effectiveness**: cuando un guardrail falla y se re-genera, ¿la 2da respuesta pasa?

Además, los benchmarks existentes son manuales (`make eval`). No hay ejecución automática programada.

**Motivación**: Sin benchmarks de estas dimensiones, las regresiones se detectan solo cuando un usuario reporta un problema. Con benchmarks scheduled, se detectan antes del deploy.

## Alcance

### In Scope

**A. Memory Retrieval Benchmark** (`--mode memory`)
- Golden set de 30 queries con expected memories (IDs o keywords)
- Mide Precision@5 y Recall
- Requiere DB con memorias seeded
- CI-compatible (exit code)

**B. Agent Plan Benchmark** (`--mode plan`)
- Dataset de 15 objectives complejos con expected plan structure (min tasks, expected categories)
- LLM-as-judge para plan quality (coherence, completeness, feasibility)
- No ejecuta el plan — solo evalúa la planificación

**C. Scheduled Eval** (APScheduler job)
- Corre `--mode classify` diariamente a las 04:00 UTC
- Persiste accuracy como trace score `eval_regression_{mode}`
- Alerta via WhatsApp si accuracy baja del threshold
- Integra con `operational_automation` (metric alert rule)

**D. Context Saturation Analysis** (script)
- Correlaciona `context_fill_rate` > 0.8 con guardrail pass rate y judge scores
- Identifica punto de quiebre donde calidad degrada significativamente
- Output: reporte con recomendación de max context fill target

**E. Seed Dataset Expansion** (82 → ~120 cases)
- 10 cases de language consistency (conversaciones multi-turn, code-switching)
- 10 cases de tool hallucination (queries ambiguos donde el LLM podría inventar tools)
- 8 cases de remediation (responses que fallan guardrails con expected remediado)
- 10 cases de agent mode (objectives → expected plans)

### Out of Scope
- Prompt A/B testing framework (requiere infra de split traffic)
- Dream consolidation quality (requiere human eval)
- Session memory precision (requiere human eval)
- Subagent vs direct execution A/B
- Langfuse Experiments integration (ya existe, solo falta usar)

## Casos de Uso Críticos

1. **Pre-deploy**: `make eval` corre 4 modos (classify + tools + e2e + guardrails) con ~120 cases. Si alguno falla threshold → no deploy
2. **Nightly**: APScheduler corre classify eval, persiste score. Si baja → alerta WhatsApp al admin
3. **Post model change**: Después de cambiar modelo Ollama, correr `make eval --mode e2e -v` para comparar con baseline
4. **Memory debugging**: `make eval-memory` identifica queries donde semantic search falla → guía tuning de threshold/embeddings
5. **Context tuning**: Script de saturation analysis determina que >85% fill rate degrada calidad → se ajusta `CONTEXT_WINDOW_TOKENS` o se poda contexto

## Restricciones Arquitectónicas

- Benchmarks offline: no requieren FastAPI corriendo, solo DB + Ollama
- Memory benchmark necesita memorias en la DB — el seed script debe insertarlas
- Agent plan benchmark genera LLM calls — lento (~30s por case con Ollama)
- Scheduled eval no debe competir por recursos con requests de usuarios (corre a las 04:00)
- Exit codes CI: 0 = pass, 1 = fail, 2 = no entries
- Los benchmarks nuevos se agregan como modos en el `run_eval.py` existente, no como scripts separados

## Métricas de Éxito

| Benchmark | Threshold inicial | Justificación |
|-----------|:-----------------:|---------------|
| classify | 80% | Ya calibrado con 82 cases |
| tools | 70% | Tool selection tiene varianza |
| e2e | 50% | LLM-as-judge con mismo modelo tiene bias |
| guardrails | 90% | Checks deterministicos deben ser estables |
| memory | 60% | Nuevo — se calibrará con datos reales |
| plan | 50% | Nuevo — evaluación subjetiva, threshold conservador |
