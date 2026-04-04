# Feature: Eval & Security Hardening

> **Version**: v1.0
> **Fecha de implementacion**: 2026-04-03
> **Plan**: 60
> **Estado**: Implementada

---

## Que hace?

Tres mejoras de seguridad y calidad de evaluacion:
1. **Credential scrubbing**: los subprocesos ejecutados por el agente ya no heredan tokens ni credenciales del environment.
2. **Code security detection**: cuando el agente escribe codigo, se escanean patrones peligrosos (eval, pickle, SQL injection, XSS) y se advierte al LLM.
3. **Multi-criteria judge**: las evaluaciones usan un judge LLM con 4 criterios numericos (correctness, completeness, conciseness, tool_usage) en vez de binary yes/no.

---

## Arquitectura

```
[Subprocess execution]          [Code writing]              [Eval pipeline]
       |                              |                           |
  _scrubbed_env()              check_code_security()        judge_response()
  removes secrets              regex scan, warning          4-criteria JSON
       |                              |                           |
  env= passed to               warning appended             scores recorded
  create_subprocess_exec       to LLM response              to trace_scores
```

---

## Archivos clave

| Archivo | Rol |
|---|---|
| `app/skills/tools/shell_tools.py` | `_scrubbed_env()`, `_SCRUB_EXACT`, `_SCRUB_SUFFIXES` |
| `app/guardrails/checks.py` | `check_code_security()`, `_CODE_SECURITY_PATTERNS` |
| `app/skills/tools/selfcode_tools.py` | Integra `check_code_security` en write/patch |
| `app/skills/tools/grep_tools.py` | Usa `_scrubbed_env()` en subprocess.run |
| `app/eval/judge.py` | `JudgeResult`, `judge_response()`, `_parse_judge_response()` |
| `app/skills/tools/eval_tools.py` | `run_quick_eval()` usa multi-criteria judge + trace scores |
| `tests/test_credential_scrub.py` | Tests de credential scrubbing |
| `tests/test_code_security.py` | Tests de code security patterns |
| `tests/test_judge.py` | Tests de multi-criteria judge |
| `tests/test_eval_tools.py` | Tests de eval tools con nuevo judge |

---

## Decisiones de diseno

| Decision | Alternativa descartada | Motivo |
|---|---|---|
| Code security es warning, no bloqueo | Bloquear escritura | False positives en articulos/docs que mencionan `eval()` |
| `check_tool_coherence` mantiene binary yes/no | Multi-criteria | Timeout de 3s demasiado ajustado para JSON parsing |
| `_scrubbed_env()` se llama en cada subprocess | Cache del env | O(n) sobre ~50-100 vars es negligible vs costo del subprocess |
| Trace scores via `repository.save_trace_score` | TraceRecorder | eval_tools no tiene acceso a TraceRecorder; repository si |

---

## Gotchas y edge cases

- **`_SCRUB_KEEP`**: `TERM_SESSION_ID` y `COLORTERM` matchean suffix patterns pero se preservan explicitamente.
- **Judge JSON parsing**: 3 niveles de fallback (JSON directo, regex extraction, default scores). `think=False` obligatorio.
- **Code security solo en archivos de codigo**: extensiones `.py`, `.js`, `.ts`, `.jsx`, `.tsx`, `.java`, `.go`, `.rb`, `.php`.
