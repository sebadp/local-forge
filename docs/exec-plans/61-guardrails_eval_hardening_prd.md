# PRD: Guardrails & Eval Hardening (Plan 61)

## Objetivo y Contexto

El audit de eval/guardrails (`docs/EVAL_GUARDRAILS_AUDIT.md`) identificó gaps concretos en el stack de calidad de LocalForge. Este plan cierra los gaps de mayor impacto/esfuerzo:

1. Los 2 guardrail checks más valiosos (coherence + hallucination) están implementados pero deshabilitados
2. Code security warnings no se persisten — no hay métricas de código inseguro generado por el agent
3. No hay benchmark de regression para guardrails — solo para classify/tools/e2e
4. No hay mecanismo de user feedback desde WhatsApp (thumbs up/down) para activar el tier "golden confirmed" del eval

**Motivación**: Sin estos, no podemos medir la calidad real de las respuestas ni detectar regresiones en guardrails entre deploys.

## Alcance

### In Scope
- **A. Code security tracing**: Persistir `code_security_warning` scores a `trace_scores` cuando se detectan patrones peligrosos
- **B. Guardrails benchmark mode**: Nuevo `--mode guardrails` en `run_eval.py` que corre checks deterministicos sobre responses del dataset sin LLM
- **C. Habilitar LLM guardrail checks**: Documentar y testear `guardrails_llm_checks=True` con `check_tool_coherence` y `check_hallucination`
- **D. WhatsApp reaction → user score**: Mapear 👍/👎 reactions a `trace_scores` con source="user" para activar auto-curation tier "golden confirmed"

### Out of Scope
- Nuevos checks de guardrails (solo habilitar los existentes)
- Scheduled regression eval (Plan 62)
- Prompt A/B testing framework
- Memory retrieval quality benchmark (Plan 62)

## Casos de Uso Críticos

1. **DevOps**: `make eval-guardrails` antes de cada deploy — exit code 1 si pass rate < 90%
2. **Observability**: Query `SELECT name, AVG(value) FROM trace_scores WHERE name='code_security_warning'` para medir código inseguro
3. **User feedback**: Usuario reacciona 👎 a una respuesta → entry creada como "failure" en eval_dataset → visible en `list_recent_failures`
4. **Quality gate**: Con LLM checks activos, respuestas incoherentes o con hallucinations se detectan pre-envío y se remedian

## Restricciones Arquitectónicas

- Tracing es best-effort: nunca bloquear tool execution ni message delivery
- Guardrails son fail-open: un check que falla no bloquea la respuesta
- WhatsApp reactions llegan como webhook events separados — necesitan correlación con el mensaje original via `wa_message_id`
- `check_tool_coherence` y `check_hallucination` tienen timeout de 3s (configurable) — NO deben agregar latencia perceptible si Ollama es lento
