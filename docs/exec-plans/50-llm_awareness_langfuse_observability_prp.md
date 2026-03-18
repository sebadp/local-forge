# PRP: LLM Tool Awareness & Langfuse Full Observability (Plan 50)

## Archivos Modificados

### Stream A — LLM Tool Awareness
- `app/profiles/prompt_builder.py`: Fecha prominente al inicio del system prompt
- `app/config.py`: Instrucciones anti-alucinación de capacidades (TOOL AWARENESS)
- `app/eval/prompt_registry.py`: Sincronizado con grounding rules de config.py
- `app/skills/executor.py`: Nudge post-`request_more_tools` + helper `_serialize_messages_for_trace`
- `app/webhook/router.py`: Capabilities header reforzado

### Stream B — Langfuse Observability
- `app/skills/executor.py`: Full messages como input en spans `llm:iteration_N`
- `app/webhook/router.py`: Input/output en guardrails span + context metadata + phase_ab enrichment + remediation spans
- `app/skills/router.py`: Prompt completo del classifier en span
- `app/formatting/compaction.py`: Preview de texto original en span de compaction
- `app/agent/loop.py`: Full messages en reactive loop spans + untruncated objective

### Tests
- `tests/test_profiles.py`: Actualizado assert de fecha
- `tests/test_guardrails.py`: Actualizado span name y asserts de remediation

### Docs
- `docs/exec-plans/50-llm_awareness_langfuse_observability_prd.md`: PRD (nuevo)
- `docs/exec-plans/50-llm_awareness_langfuse_observability_prp.md`: PRP (nuevo)
- `docs/exec-plans/README.md`: Entrada del Plan 50

## Fases de Implementación

### Phase 0: Documentación
- [x] Crear PRD
- [x] Crear PRP
- [x] Actualizar `docs/exec-plans/README.md` con Plan 50

### Phase 1: Fecha prominente en system prompt (RC-1)
- [x] Mover fecha al inicio del prompt con formato enfático (`IMPORTANT — Today is ...`)
- [x] Eliminar la línea vieja `Current Date:` al final
- [x] Actualizar tests (`test_profiles.py`: `"Current Date:"` → `"Today is"`)

### Phase 2: Declaración explícita de capacidades (RC-2, RC-3)
- [x] Agregar `TOOL AWARENESS` al system prompt en `config.py`
- [x] Sincronizar `prompt_registry.py` `_SYSTEM_PROMPT` con todas las reglas (grounding + tool awareness)
- [x] Actualizar capabilities header en `_build_capabilities_section` y `_build_capabilities_for_categories`

### Phase 3: Nudge post-request_more_tools (RC-4)
- [x] Actualizar confirmación: `"IMPORTANT: Call these tools NOW..."` en vez de sugerencia suave
- [x] Tests existentes pasan sin cambios

### Phase 4: Langfuse — Full messages input en spans LLM (RC-5)
- [x] Crear helper `_serialize_messages_for_trace()` en `executor.py` (system→2000 chars, otros→500 chars)
- [x] Actualizar span `llm:iteration_N` en executor con `_serialize_messages_for_trace`
- [x] Actualizar span `llm:chat` sin tools en `_run_normal_flow` (router.py) con misma serialización
- [x] Enriquecer span `tool_loop` con context_messages count, system_prompt_chars, token_estimate
- [x] Enriquecer span `phase_ab` con input (user_text, phone) y output (memories/history/notes counts)

### Phase 5: Langfuse — Guardrails input/output (RC-6)
- [x] Agregar `set_input` al span guardrails (user_text[:300], reply[:500], tool_calls_used)
- [x] Agregar `set_output` con resultados detallados por check (name, passed, detail)
- [x] Mantener `set_metadata` existente para backward compat

### Phase 6: Langfuse — Classifier prompt completo (RC-7)
- [x] Actualizar span `llm:classify_intent` con prompt completo[:3000], sticky_categories, has_recent_context
- [x] User message ahora sin truncar (era 200 chars)

### Phase 7: Compaction, agent loop y mejoras adicionales
- [x] Compaction span: agregar `user_request[:200]` y `text_preview[:500]` al input
- [x] Agent reactive loop: agregar full messages via `_serialize_messages_for_trace` al span `reactive:round_N`
- [x] Agent planner: untruncate objective (era [:200])
- [x] Guardrail `not_empty` retry: nuevo span `guardrails:remediation` con input/output
- [x] Guardrail `language_match` remediation: renombrado a `guardrails:remediation_lang`, ahora con `set_input` (check, lang_code, original_reply, hint) y `set_output` (retry_preview)
- [x] Actualizar test `test_language_remediation_creates_span_when_trace_ctx_provided`

### Phase 8: Validación
- [x] `ruff check` pass
- [x] `mypy` pass (todos los archivos)
- [x] `pytest` pass (785/785)
- [ ] Deploy a docker y verificar con sesión real
