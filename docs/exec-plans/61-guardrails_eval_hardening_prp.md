# PRP: Guardrails & Eval Hardening (Plan 61)

## Archivos a Modificar

| Archivo | Cambio |
|---------|--------|
| `app/skills/tools/selfcode_tools.py` | `_security_warning()` → add trace score |
| `scripts/run_eval.py` | Nuevo `_run_guardrails()` mode |
| `Makefile` | Target `eval-guardrails` |
| `app/webhook/parser.py` | Parsear reaction events (👍/👎) |
| `app/webhook/router.py` | Handler para reactions → trace score + eval curation |
| `app/config.py` | Documentar `guardrails_llm_checks` |
| `docs/EVAL_GUARDRAILS_AUDIT.md` | Actualizar con estado post-implementación |
| `docs/features/61-guardrails_eval_hardening.md` | Feature doc |
| `docs/testing/61-guardrails_eval_hardening_testing.md` | Testing doc |
| `tests/test_reaction_scoring.py` | Tests de reaction → score pipeline |

## Fases de Implementación

### Phase 1: Code Security Tracing
- [x] `_security_warning()` persiste `code_security_warning` score via `get_current_trace()` (best-effort)
- [x] `asyncio.ensure_future` para no bloquear tool execution
- [x] Test: verificar que score se registra cuando pattern detectado (`tests/test_code_security_trace.py`)

### Phase 2: Guardrails Benchmark Mode
- [x] `_run_guardrails()` en `run_eval.py` — corre checks deterministicos sobre responses
- [x] Dispatch en `_run_eval()` para `mode == "guardrails"`
- [x] Argparse: agregar `"guardrails"` a choices
- [x] Filter: entries necesitan `output_text` o `expected_output`
- [x] `Makefile`: target `eval-guardrails` con threshold 90%
- [x] Test: `make eval-guardrails` con seed dataset

### Phase 3: Enable LLM Guardrail Checks
- [ ] Testear `GUARDRAILS_LLM_CHECKS=true` con Ollama local
- [ ] Medir latencia adicional de `check_tool_coherence` (timeout 3s)
- [ ] Medir latencia adicional de `check_hallucination` (timeout 3s)
- [x] Documentar en `.env.example` con recomendación
- [ ] Verificar que timeout no bloquea responses si Ollama es lento

### Phase 4: WhatsApp Reaction → User Score
- [x] `parser.py`: extraer reaction events del webhook payload (`extract_reactions()`)
- [x] `router.py`: handler `_handle_reaction()` que:
  - Busca trace por `wa_message_id` en tabla `traces`
  - Mapea 👍 → `add_score("user_reaction", 1.0, source="user")`
  - Mapea 👎 → `add_score("user_reaction", 0.0, source="user")`
  - Triggerea `maybe_curate_to_dataset()` para re-evaluar tiers
  - Correction prompt para reactions negativas (≤0.2)
- [x] Test: reaction payload → score persistido → dataset curated (`tests/test_reaction_scoring.py`)

### Phase 5: Documentación
- [x] Actualizar `docs/EVAL_GUARDRAILS_AUDIT.md` con estado final
- [x] Crear `docs/features/61-guardrails_eval_hardening.md`
- [x] Crear `docs/testing/61-guardrails_eval_hardening_testing.md`
- [x] Actualizar `docs/testing/README.md` y `docs/features/README.md`
- [x] `make check` (lint + typecheck + tests) — 923 passed
