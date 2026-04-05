# Guardrails & Eval Hardening (Plan 61)

## Qué hace

Extiende la infraestructura de guardrails y evaluación con:

1. **Code Security Tracing** — `_security_warning()` persiste un `code_security_warning` score en trace_scores cuando detecta patrones inseguros en código escrito por el agente
2. **Guardrails Benchmark Mode** — Nuevo modo `--mode guardrails` en `run_eval.py` que corre checks deterministicos sobre el seed dataset
3. **LLM Guardrail Checks** — Documentación y configuración de `GUARDRAILS_LLM_CHECKS=true` (check_tool_coherence + check_hallucination, opt-in)
4. **WhatsApp Reaction → User Score** — Reacciones 👍/👎 se convierten en trace scores + triggean dataset curation + correction prompts

## Cómo funciona

### Code Security Tracing (`selfcode_tools.py:47`)

Cuando `write_source_file` o `apply_patch` escriben código, `_security_warning()` corre `check_code_security()`. Si detecta patrones inseguros (shell=True, eval(), hardcoded secrets, etc.), además de retornar un warning al agente, persiste `code_security_warning=0.0` como trace score via `get_current_trace().add_score()`. Usa `asyncio.ensure_future()` para no bloquear.

### Guardrails Benchmark (`run_eval.py`, mode=guardrails)

`_run_guardrails()` itera entries del seed dataset que tienen `output_text` o `expected_output`. Para cada uno, ejecuta `run_guardrails()` (el mismo pipeline de producción: not_empty, language_match, no_pii, no_raw_tool_json, excessive_length). Score = proporción de checks que pasan.

```bash
make eval-guardrails  # threshold 90%
```

### WhatsApp Reaction → User Score (`router.py:749`)

El webhook ya parsea reactions (`parser.py:extract_reactions()`). El handler `_handle_reaction()`:

1. Busca el `trace_id` del mensaje reaccionado via `get_trace_id_by_wa_message_id()`
2. Mapea emoji → score numérico (👍=1.0, 👎=0.0, 😂=0.8, etc.) via `_REACTION_SCORE_MAP`
3. Persiste como `user_reaction` score en trace_scores
4. Si `eval_auto_curate=True`, triggea `maybe_curate_to_dataset()`
5. Si score ≤ 0.2 (👎, 😢), envía prompt pidiendo corrección al usuario

## Archivos clave

| Archivo | Qué contiene |
|---------|-------------|
| `app/skills/tools/selfcode_tools.py:47` | `_security_warning()` con trace score |
| `scripts/run_eval.py` | `_run_guardrails()` benchmark mode |
| `app/webhook/router.py:671` | `_REACTION_SCORE_MAP` |
| `app/webhook/router.py:749` | `_handle_reaction()` |
| `app/webhook/parser.py:46` | `extract_reactions()` |
| `Makefile` | Target `eval-guardrails` |

## Configuración

| Variable | Default | Descripción |
|----------|---------|-------------|
| `GUARDRAILS_LLM_CHECKS` | `false` | Activa check_tool_coherence + check_hallucination (requiere Ollama, +3s latencia) |
| `EVAL_AUTO_CURATE` | `false` | Reactions triggean auto-curation del dataset |

## Decisiones de diseño

- **Fail-open**: Si la persistencia del trace score falla, la warning sigue apareciendo al agente. Nunca bloquea.
- **Fire-and-forget reactions**: Reactions se procesan como background tasks, sin dedup (idempotentes por naturaleza).
- **Correction prompt**: Solo se envía una vez por trace (guarda `correction_prompted` score como flag).
- **LLM checks disabled by default**: Agregan ~3s de latencia cada uno y requieren Ollama disponible.

## Testing

→ [`docs/testing/61-guardrails_eval_hardening_testing.md`](../testing/61-guardrails_eval_hardening_testing.md)
