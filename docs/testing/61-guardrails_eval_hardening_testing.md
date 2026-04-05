# Testing: Guardrails & Eval Hardening (Plan 61)

## Tests automatizados

### Code Security Trace Score (`tests/test_code_security_trace.py`)

| Test | Qué verifica |
|------|-------------|
| `test_security_warning_returns_empty_for_non_code` | No corre checks en archivos no-código (.txt) |
| `test_security_warning_returns_empty_for_safe_code` | Código seguro no genera warning |
| `test_security_warning_detects_unsafe_pattern` | shell=True, eval() detectados |
| `test_security_warning_persists_trace_score` | `asyncio.ensure_future` llamado con `add_score` cuando hay trace activo |
| `test_security_warning_no_trace_still_returns_warning` | Warning retornado aun sin trace context |

### Reaction → Score Pipeline (`tests/test_reaction_scoring.py`)

| Test | Qué verifica |
|------|-------------|
| `test_thumbs_up_scores_1` | 👍 → score 1.0 |
| `test_thumbs_down_scores_0` | 👎 → score 0.0 |
| `test_unknown_message_ignored` | Reaction a mensaje sin trace → no-op |
| `test_non_reaction_object_ignored` | Input inválido → no-op |
| `test_unknown_emoji_defaults_to_05` | 🔥 → score 0.5 |
| `test_negative_reaction_sends_correction_prompt` | 👎 → envía mensaje pidiendo corrección |
| `test_negative_reaction_no_double_prompt` | Si ya se pidió corrección, no repite |
| `test_extract_reactions_from_payload` | Parser extrae reactions correctamente |
| `test_extract_reactions_ignores_non_reactions` | Parser ignora mensajes de texto |

### Guardrails Benchmark

```bash
make eval-guardrails  # Requiere seed dataset + Ollama
```

Verifica que ≥90% de las entries del seed dataset pasan los guardrails deterministicos.

## Testing manual

### LLM Guardrail Checks

```bash
# Activar temporalmente
GUARDRAILS_LLM_CHECKS=true make run

# Enviar mensaje y verificar en logs:
# - check_tool_coherence ejecutado (timeout 3s)
# - check_hallucination ejecutado (timeout 3s)
# - Latencia total no excede 10s
```

### WhatsApp Reactions

1. Enviar un mensaje al bot, esperar respuesta
2. Reaccionar con 👍 al mensaje del bot
3. Verificar en DB: `SELECT * FROM trace_scores WHERE name='user_reaction' ORDER BY created_at DESC LIMIT 1`
4. Reaccionar con 👎 → verificar que el bot pide corrección
5. Responder con la corrección → verificar correction pair guardado
