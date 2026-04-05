# Testing Manual: Eval & Security Hardening (Plan 60)

> **Feature documentada**: [`docs/features/60-eval_security_hardening.md`](../features/60-eval_security_hardening.md)
> **Requisitos previos**: Container corriendo, Ollama disponible.

---

## Casos de prueba: Credential Scrubbing

| Escenario | Resultado esperado |
|---|---|
| Agent ejecuta `run_command("env")` | Output NO contiene `WHATSAPP_TOKEN`, `GITHUB_TOKEN`, `OPENAI_API_KEY`, etc. |
| Agent ejecuta `run_command("echo $WHATSAPP_TOKEN")` | Variable vacía — scrubbed del env del subprocess |
| `grep_code` ejecuta subprocess | También usa `_scrubbed_env()` |

### Verificar manualmente

```bash
# Desde dentro del container, simular lo que ve un subprocess
docker compose exec localforge python -c "
from app.skills.tools.shell_tools import _scrubbed_env
env = _scrubbed_env()
sensitive = ['WHATSAPP_TOKEN', 'GITHUB_TOKEN', 'OPENAI_API_KEY', 'META_APP_SECRET']
for k in sensitive:
    assert k not in env, f'{k} should be scrubbed!'
print('All sensitive vars scrubbed ✓')
"
```

---

## Casos de prueba: Code Security Detection

| Escenario | Resultado esperado |
|---|---|
| Agent escribe archivo `.py` con `eval(user_input)` | Warning en tool result: patrón peligroso detectado (eval con input) |
| Agent escribe archivo `.js` con `innerHTML = user_data` | Warning: posible XSS |
| Agent escribe archivo `.py` con `eval("2+2")` (safe) | Warning igual — es regex, no semántico. LLM decide si ignorar |
| Agent escribe archivo `.md` con texto mencionando `eval()` | NO se escanea — solo extensiones de código (.py, .js, .ts, etc.) |
| Agent usa `apply_patch` en archivo `.py` con SQL injection pattern | Warning en el resultado del patch |

### Verificar

```bash
docker compose logs -f localforge 2>&1 | grep -i "code_security\|security_warning\|dangerous_pattern"
```

---

## Casos de prueba: Multi-Criteria Judge

| Escenario | Resultado esperado |
|---|---|
| `run_quick_eval` en agent mode | Judge evalúa con 4 criterios: correctness, completeness, conciseness, tool_usage. Scores 1-5 |
| Judge con LLM que retorna JSON inválido | Fallback: regex extraction, luego default scores |
| Scores guardados en tracing | `trace_scores` contiene los 4 criterios |

### Verificar en DB

```bash
# Ver scores de evaluación recientes
sqlite3 data/localforge.db "SELECT * FROM trace_scores ORDER BY created_at DESC LIMIT 10;" 2>/dev/null || echo "Table may not exist yet"
```

---

## Edge cases y validaciones

| Escenario | Resultado esperado |
|---|---|
| `_SCRUB_KEEP` vars (`TERM_SESSION_ID`, `COLORTERM`) | Se preservan a pesar de matchear suffix patterns |
| Code security en archivo sin extensión | NO se escanea |
| Judge con `think=False` | Obligatorio — output es JSON, no razonamiento |
| `check_tool_coherence` (binary check existente) | Mantiene yes/no, NO usa multi-criteria (timeout 3s) |
| Subprocess con env vars custom del usuario | Solo se scrubban las conocidas — custom vars pasan |

---

## Tests automatizados

```bash
.venv/bin/python -m pytest tests/test_credential_scrub.py tests/test_code_security.py tests/test_judge.py tests/test_eval_tools.py -v
```

---

## Troubleshooting

| Problema | Causa probable | Solución |
|---|---|---|
| Subprocess tiene acceso a tokens | `_scrubbed_env()` no se llama | Verificar que shell_tools usa env= param |
| Code security no detecta patrón | Extensión no soportada o patrón no en `_CODE_SECURITY_PATTERNS` | Revisar lista de extensiones y patterns |
| Judge siempre retorna default scores | LLM no genera JSON válido | Verificar prompt, `think=False` obligatorio |
| Warning de security en código legítimo | False positive (regex-based, no semántico) | Normal — es warning, no bloqueo. LLM decide |
