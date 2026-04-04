# PRP: Eval & Security Hardening — Multi-Criteria Judge, Code Security Patterns, Credential Scrubbing (Plan 60)

## Archivos a Modificar

| Archivo | Cambio |
|---------|--------|
| `app/eval/judge.py` | **Nuevo** — Multi-criteria LLM-as-judge con rubric y JSON output |
| `app/guardrails/checks.py` | Agregar `check_code_security(content, file_path)` |
| `app/guardrails/pipeline.py` | Integrar code security check en el pipeline (opcional, post-write) |
| `app/skills/tools/eval_tools.py` | Refactorizar `run_quick_eval()` para usar nuevo judge |
| `app/skills/tools/selfcode_tools.py` | Invocar `check_code_security()` después de `write_source_file` y `apply_patch` |
| `app/skills/tools/shell_tools.py` | Agregar `_scrubbed_env()` y pasar `env=` a subprocess |
| `app/eval/dataset.py` | Actualizar `maybe_curate_to_dataset()` para usar scores multi-criteria |
| `tests/test_judge.py` | **Nuevo** — Tests para multi-criteria judge |
| `tests/test_code_security.py` | **Nuevo** — Tests para code security patterns |
| `tests/test_credential_scrub.py` | **Nuevo** — Tests para credential scrubbing |

## Análisis de Impacto en Tests Existentes

| Test existente | Afectado? | Razón |
|----------------|-----------|-------|
| `tests/test_guardrails.py` | NO | Los checks existentes no cambian. `check_code_security` es nuevo. |
| `tests/test_shell_tools.py` | MÍNIMO | `_run_sync` ahora pasa `env=`. Tests que mockean `create_subprocess_exec` necesitan aceptar `env` kwarg. |
| `tests/test_selfcode.py` | MÍNIMO | `write_source_file` y `apply_patch` agregan warning al final. Tests que assertean output exacto necesitan ajuste. |
| `tests/test_eval_tools.py` | SI | `run_quick_eval` cambia formato de output (incluye scores por criterio). |
| `tests/test_budget_compaction.py` | NO | Sin cambios. |

---

## Fases de Implementación

### Phase 1: Credential Scrubbing (security fix — prioridad máxima)

- [x] **1.1** Crear `_scrubbed_env()` en `app/skills/tools/shell_tools.py`:
  ```python
  import os

  _SCRUB_EXACT: frozenset[str] = frozenset({
      "WHATSAPP_ACCESS_TOKEN",
      "WHATSAPP_APP_SECRET",
      "WHATSAPP_VERIFY_TOKEN",
      "GITHUB_TOKEN",
      "LANGFUSE_SECRET_KEY",
      "LANGFUSE_PUBLIC_KEY",
      "AUDIT_HMAC_KEY",
      "TELEGRAM_BOT_TOKEN",
      "TELEGRAM_WEBHOOK_SECRET",
      "NGROK_AUTHTOKEN",
      "OPENAI_API_KEY",
      "ANTHROPIC_API_KEY",
  })

  _SCRUB_SUFFIXES: tuple[str, ...] = (
      "_SECRET", "_TOKEN", "_KEY", "_PASSWORD", "_CREDENTIAL",
      "_API_KEY", "_ACCESS_KEY", "_SECRET_KEY",
  )

  def _scrubbed_env() -> dict[str, str]:
      """Return a copy of os.environ with sensitive variables removed."""
      env = os.environ.copy()
      to_remove: list[str] = []
      for key in env:
          if key in _SCRUB_EXACT:
              to_remove.append(key)
          elif any(key.upper().endswith(s) for s in _SCRUB_SUFFIXES):
              to_remove.append(key)
      for key in to_remove:
          del env[key]
      return env
  ```

- [x] **1.2** Pasar `env=_scrubbed_env()` en ambos puntos de creación de subprocess:
  ```python
  # En _run_sync:
  proc = await asyncio.create_subprocess_exec(
      *tokens,
      stdout=asyncio.subprocess.PIPE,
      stderr=asyncio.subprocess.PIPE,
      stdin=asyncio.subprocess.DEVNULL,
      cwd=str(_PROJECT_ROOT),
      env=_scrubbed_env(),
  )

  # En _start_background (si existe subprocess creation):
  # Mismo cambio.
  ```

- [x] **1.3** Pasar `env=_scrubbed_env()` también en `grep_tools.py` (`subprocess.run`):
  ```python
  result = subprocess.run(
      cmd, capture_output=True, text=True, timeout=15,
      cwd=str(root),
      env=_scrubbed_env(),
  )
  ```
  **Nota**: `_scrubbed_env` se puede mover a un módulo compartido (`app/security/env_scrub.py`) o importar desde shell_tools. Evaluar cuál es más limpio.

- [x] **1.4** Tests en `tests/test_credential_scrub.py`:
  - `test_scrub_removes_exact_matches` — set env vars, verify removed
  - `test_scrub_removes_suffix_matches` — set `MY_CUSTOM_SECRET_KEY`, verify removed
  - `test_scrub_preserves_safe_vars` — `PATH`, `HOME`, `LANG` sobreviven
  - `test_scrub_preserves_ollama_vars` — `OLLAMA_HOST`, `OLLAMA_NUM_PARALLEL` sobreviven
  - `test_subprocess_receives_scrubbed_env` — mock `create_subprocess_exec`, verify `env` kwarg excludes tokens

### Phase 2: Code Security Pattern Detection

- [x] **2.1** Agregar `check_code_security()` en `app/guardrails/checks.py`:
  ```python
  import re

  _CODE_SECURITY_PATTERNS: list[tuple[re.Pattern, str, str]] = [
      # (compiled regex, pattern name, recommendation)
      (re.compile(r"\beval\s*\("), "eval()", "Use ast.literal_eval() or a parser"),
      (re.compile(r"\bexec\s*\("), "exec()", "Avoid dynamic code execution"),
      (re.compile(r"\bos\.system\s*\("), "os.system()", "Use subprocess.run() with shell=False"),
      (re.compile(r"\bos\.popen\s*\("), "os.popen()", "Use subprocess.run() with shell=False"),
      (re.compile(r"subprocess\.call\s*\([^)]*shell\s*=\s*True"),
       "subprocess(shell=True)", "Use shell=False with explicit args list"),
      (re.compile(r"\bpickle\.loads?\s*\("), "pickle.load()", "Use json or a safe serializer"),
      (re.compile(r"\byaml\.load\s*\([^)]*\)(?!.*Loader)"),
       "yaml.load() without Loader", "Use yaml.safe_load()"),
      (re.compile(r"dangerouslySetInnerHTML"), "dangerouslySetInnerHTML", "Sanitize HTML input first"),
      (re.compile(r"\.innerHTML\s*="), ".innerHTML =", "Use textContent or a sanitizer"),
      (re.compile(r"\bdocument\.write\s*\("), "document.write()", "Use DOM manipulation instead"),
      (re.compile(r"""(?:f["']|["'].*%s|["'].*\+).*\b(?:SELECT|INSERT|UPDATE|DELETE)\b""", re.IGNORECASE),
       "SQL string concatenation", "Use parameterized queries"),
      (re.compile(r"\bnew\s+Function\s*\("), "new Function()", "Avoid dynamic code construction"),
  ]


  def check_code_security(content: str, file_path: str = "") -> GuardrailResult:
      """Scan written content for dangerous code patterns. Deterministic, no LLM."""
      start = time.monotonic()
      findings: list[str] = []

      for pattern, name, recommendation in _CODE_SECURITY_PATTERNS:
          matches = list(pattern.finditer(content))
          if matches:
              # Find line numbers
              for match in matches[:3]:  # Cap at 3 per pattern
                  line_no = content[:match.start()].count("\n") + 1
                  findings.append(f"{name} (line {line_no}): {recommendation}")

      passed = len(findings) == 0
      latency_ms = (time.monotonic() - start) * 1000
      return GuardrailResult(
          passed=passed,
          check_name="code_security",
          details="; ".join(findings) if findings else "",
          latency_ms=latency_ms,
      )
  ```

- [x] **2.2** Integrar en `write_source_file` y `apply_patch` en `selfcode_tools.py`:
  ```python
  # Al final de _write() y _patch(), después de escribir exitosamente:
  from app.guardrails.checks import check_code_security

  result_msg = f"✅ Wrote '{path}' ..."  # existing success message

  # Only check code files
  code_extensions = {".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rb", ".php"}
  if target.suffix.lower() in code_extensions:
      security_check = check_code_security(content, path)
      if not security_check.passed:
          result_msg += (
              f"\n\n⚠️ **Security warning** — potentially unsafe patterns detected:\n"
              + "\n".join(f"- {d}" for d in security_check.details.split("; "))
              + "\n\nConsider reviewing and fixing these before proceeding."
          )

  return result_msg
  ```

- [x] **2.3** Registrar score en audit trail (si audit disponible):
  ```python
  # Dentro del check, loguear findings
  if findings:
      logger.warning(
          "code_security.findings",
          extra={"file_path": file_path, "findings": findings},
      )
  ```

- [x] **2.4** Tests en `tests/test_code_security.py`:
  - `test_detects_eval` — `eval(user_input)` → fail con line number
  - `test_detects_os_system` — `os.system("rm -rf /")` → fail
  - `test_detects_pickle_load` — `pickle.loads(data)` → fail
  - `test_detects_inner_html` — `.innerHTML = userInput` → fail
  - `test_detects_sql_concat` — `f"SELECT * FROM users WHERE id = {user_id}"` → fail
  - `test_detects_subprocess_shell_true` — `subprocess.call(cmd, shell=True)` → fail
  - `test_detects_yaml_unsafe` — `yaml.load(data)` sin Loader → fail
  - `test_passes_safe_code` — `ast.literal_eval()`, `subprocess.run(args)` → pass
  - `test_passes_non_code_files` — `.md`, `.txt` no se verifican (check en selfcode, no en guardrail)
  - `test_multiple_findings` — archivo con eval + pickle → ambos reportados
  - `test_line_numbers_correct` — verify line numbers match
  - `test_dangerously_set_inner_html` — React JSX pattern → fail

### Phase 3: Multi-Criteria LLM-as-Judge

- [x] **3.1** Crear `app/eval/judge.py`:
  ```python
  """Multi-criteria LLM-as-judge for eval quality scoring.

  Returns structured scores across 4 dimensions with reasoning.
  Uses think=False for deterministic JSON output.
  """

  from __future__ import annotations

  import json
  import logging
  from dataclasses import dataclass, field

  logger = logging.getLogger(__name__)

  _JUDGE_PROMPT = """\
  You are an expert quality evaluator. Score the assistant's response on these criteria:

  1. **correctness** (0.0-1.0): Is the response factually correct and does it solve the problem?
  2. **completeness** (0.0-1.0): Does it cover all aspects of the question?
  3. **conciseness** (0.0-1.0): Is it appropriately concise without losing information? Penalize excessive verbosity, unnecessary disclaimers, or repetition.
  4. **tool_usage** (0.0-1.0): Were tools used correctly and efficiently? Set to 1.0 if no tools were involved.

  User question: {question}
  Expected answer: {expected}
  Actual answer: {actual}

  Respond with ONLY a JSON object (no markdown, no explanation outside the JSON):
  {{"correctness": 0.0, "completeness": 0.0, "conciseness": 0.0, "tool_usage": 0.0, "reasoning": "Brief explanation"}}
  """

  _CRITERIA = ("correctness", "completeness", "conciseness", "tool_usage")


  @dataclass
  class JudgeResult:
      correctness: float = 0.0
      completeness: float = 0.0
      conciseness: float = 0.0
      tool_usage: float = 1.0
      reasoning: str = ""
      raw_response: str = ""
      parse_error: bool = False

      @property
      def average(self) -> float:
          return (self.correctness + self.completeness + self.conciseness + self.tool_usage) / 4

      @property
      def passed(self) -> bool:
          """Binary pass: average >= 0.6 and no criterion below 0.3."""
          return self.average >= 0.6 and all(
              getattr(self, c) >= 0.3 for c in _CRITERIA
          )

      def to_dict(self) -> dict:
          return {
              "correctness": self.correctness,
              "completeness": self.completeness,
              "conciseness": self.conciseness,
              "tool_usage": self.tool_usage,
              "average": round(self.average, 2),
              "passed": self.passed,
              "reasoning": self.reasoning,
          }


  async def judge_response(
      question: str,
      expected: str,
      actual: str,
      ollama_client,
  ) -> JudgeResult:
      """Run multi-criteria judge on a response. Fail-open: returns default scores on error."""
      from app.models import ChatMessage

      prompt = _JUDGE_PROMPT.format(
          question=question[:500],
          expected=expected[:500],
          actual=actual[:500],
      )

      try:
          response = await ollama_client.chat(
              [ChatMessage(role="user", content=prompt)],
              think=False,
          )
          raw = str(response).strip()
          return _parse_judge_response(raw)
      except Exception as e:
          logger.warning("judge_response failed: %s", e)
          return JudgeResult(
              correctness=0.5, completeness=0.5,
              conciseness=0.5, tool_usage=1.0,
              reasoning=f"Judge error: {e}",
              parse_error=True,
          )


  def _parse_judge_response(raw: str) -> JudgeResult:
      """Parse JSON response from judge LLM. Tolerant of markdown fences."""
      # Strip markdown code fences if present
      text = raw.strip()
      if text.startswith("```"):
          text = text.split("\n", 1)[-1]
          if text.endswith("```"):
              text = text[:-3]
          text = text.strip()

      try:
          data = json.loads(text)
      except json.JSONDecodeError:
          # Try to extract JSON from the response
          import re
          match = re.search(r'\{[^{}]+\}', text)
          if match:
              try:
                  data = json.loads(match.group())
              except json.JSONDecodeError:
                  return JudgeResult(
                      reasoning="Failed to parse judge response",
                      raw_response=raw,
                      parse_error=True,
                  )
          else:
              return JudgeResult(
                  reasoning="No JSON found in judge response",
                  raw_response=raw,
                  parse_error=True,
              )

      def _clamp(val, lo=0.0, hi=1.0) -> float:
          try:
              return max(lo, min(hi, float(val)))
          except (TypeError, ValueError):
              return 0.5

      return JudgeResult(
          correctness=_clamp(data.get("correctness", 0.5)),
          completeness=_clamp(data.get("completeness", 0.5)),
          conciseness=_clamp(data.get("conciseness", 0.5)),
          tool_usage=_clamp(data.get("tool_usage", 1.0)),
          reasoning=str(data.get("reasoning", ""))[:500],
          raw_response=raw,
      )
  ```

- [x] **3.2** Refactorizar `run_quick_eval()` en `app/skills/tools/eval_tools.py`:
  ```python
  # Reemplazar el judge binary por:
  from app.eval.judge import judge_response

  # En el loop de entries:
  judge_result = await judge_response(
      question=entry["input_text"],
      expected=expected,
      actual=actual,
      ollama_client=ollama_client,
  )
  results.append({
      "entry_id": entry["id"],
      "passed": judge_result.passed,
      "scores": judge_result.to_dict(),
  })

  # En el reporte final, agregar promedios por criterio:
  avg_scores = {}
  for criterion in ("correctness", "completeness", "conciseness", "tool_usage"):
      values = [r["scores"][criterion] for r in results if not r["scores"].get("parse_error")]
      if values:
          avg_scores[criterion] = round(sum(values) / len(values), 2)

  report_lines.append(f"\n*Scores by criterion:*")
  for criterion, avg in avg_scores.items():
      emoji = "✅" if avg >= 0.7 else "⚠️" if avg >= 0.4 else "❌"
      report_lines.append(f"  {emoji} {criterion}: {avg}")
  ```

- [x] **3.3** (Omitido — `check_tool_coherence` mantiene binary yes/no por timeout de 3s) Actualizar `check_tool_coherence()` en `app/guardrails/checks.py`:
  ```python
  # Reemplazar el prompt binary por una versión simplificada del judge:
  # Solo evaluar correctness + completeness (2 criterios, más rápido)
  # Mantener timeout de 3s — si no responde, fail-open

  prompt = (
      f"User question: {user_text[:300]}\n"
      f"Assistant reply: {reply[:500]}\n\n"
      "Score the reply on two criteria (0.0-1.0):\n"
      "1. correctness: Is it factually correct?\n"
      "2. completeness: Does it address the question?\n\n"
      'Respond ONLY with JSON: {{"correctness": 0.0, "completeness": 0.0}}'
  )
  ```
  **Nota**: Este cambio es opcional. Si el timeout de 3s es demasiado ajustado para JSON output, mantener el binary yes/no aquí y solo usar el judge completo en `run_quick_eval()`.

- [x] **3.4** Grabar scores multi-criteria en traces:
  ```python
  # En run_quick_eval, después de cada judge_result:
  if trace_recorder and entry.get("trace_id"):
      for criterion in ("correctness", "completeness", "conciseness", "tool_usage"):
          await trace_recorder.add_score(
              trace_id=entry["trace_id"],
              name=f"eval:{criterion}",
              value=getattr(judge_result, criterion),
              source="system",
              comment=judge_result.reasoning[:200],
          )
  ```

- [x] **3.5** Tests en `tests/test_judge.py`:
  - `test_parse_valid_json` — well-formed JSON → correct JudgeResult
  - `test_parse_json_with_markdown_fences` — ```json ... ``` → parsed correctly
  - `test_parse_invalid_json_fallback` — garbage → parse_error=True, default scores
  - `test_parse_json_embedded_in_text` — "Here's my eval: {...}" → extracts JSON
  - `test_clamp_out_of_range` — scores >1.0 and <0.0 → clamped
  - `test_judge_result_average` — (0.8+0.6+0.4+1.0)/4 = 0.7
  - `test_judge_result_passed` — avg >= 0.6 and no criterion < 0.3
  - `test_judge_result_failed_low_criterion` — one criterion at 0.2 → failed
  - `test_judge_response_integration` — mock ollama_client, verify full flow
  - `test_judge_response_error_failopen` — ollama raises → returns default scores

### Phase 4: Test Adjustments & Integration

- [x] **4.1** Revisar `tests/test_shell_tools.py`:
  - Tests que mockean `create_subprocess_exec` necesitan aceptar `env` kwarg
  - Agregar assertions que verifican que `env` no contiene tokens

- [x] **4.2** (No fue necesario — selfcode_tools tests no assertean output exacto) Revisar `tests/test_selfcode.py`:
  - Tests de `write_source_file` y `apply_patch`: si escriben código con `eval()`, ahora recibirán warning adicional
  - Ajustar assertions si es necesario

- [x] **4.3** Revisar `tests/test_eval_tools.py`:
  - `run_quick_eval` ahora retorna scores por criterio
  - Mock de `judge_response` para tests existentes

### Phase 5: Documentación & QA

- [x] **5.1** `make test` pasa sin regresiones
- [x] **5.2** `make lint` pasa sin errores
- [x] **5.3** Crear `docs/features/60-eval_security_hardening.md`
- [x] **5.4** Actualizar `AGENTS.md`:
  - Agregar `app/eval/judge.py` al mapa de código
  - Documentar credential scrubbing en dominio Security
  - Documentar code security check en dominio Guardrails
- [x] **5.5** Actualizar `CLAUDE.md`:
  - Agregar `judge.py` a la estructura de archivos
  - Agregar patrón: "Subprocesos reciben env scrubbed — credenciales no disponibles"
  - Agregar patrón: "write_source_file y apply_patch corren check_code_security en archivos de código"
- [x] **5.6** Actualizar `docs/exec-plans/README.md` con Plan 60

---

## Mapa de Dependencias entre Fases

```
Phase 1 (Credential Scrub) ──┐
Phase 2 (Code Security)    ──┼──> Phase 4 (Test Adjustments) ──> Phase 5 (Docs)
Phase 3 (Multi-Criteria)   ──┘
```

- Phases 1, 2, 3 son completamente independientes — pueden implementarse en paralelo.
- Phase 4 depende de 1-3 (los cambios de comportamiento afectan tests existentes).
- Phase 5 depende de todo.

---

## Invariantes — Lo que NO Cambia

- `check_not_empty`, `check_excessive_length`, `check_no_raw_tool_json`, `check_no_pii`, `check_language_match` — intactos.
- `run_guardrails()` en `pipeline.py` — intacto. `check_code_security` se invoca desde selfcode_tools, no desde el pipeline principal.
- `_validate_command()` en `shell_tools.py` — intacto.
- `_is_safe_path()` en `selfcode_tools.py` — intacto.
- `PolicyEngine` y `AuditTrail` — intactos.
- `maybe_curate_to_dataset()` sigue usando la misma lógica de 3 tiers (failure/golden/candidate) basada en scores `system:*`.
- `TraceRecorder` y `TraceContext` — intactos.
- `/health` y `/ready` endpoints — intactos.

## Riesgos

| Riesgo | Mitigación |
|--------|-----------|
| Judge JSON no parsea con qwen3.5:9b | `_parse_judge_response` tiene 3 niveles de fallback: JSON directo → regex extraction → default scores. Plus: `think=False` mejora compliance. |
| Code security false positives (ej: artículo que menciona `eval()` en texto) | Solo se verifica en archivos con extensiones de código. Y es warning, no bloqueo. |
| Credential scrub rompe comandos que necesitan tokens (ej: `gh` CLI) | `GITHUB_TOKEN` se scrubea. Si el agente necesita autenticarse, debe usar HITL. Esto es el comportamiento correcto — no queremos que el agente use credenciales automáticamente. |
| Performance: judge LLM agrega latencia a `run_quick_eval` | Judge solo corre en eval offline, no en el hot path de mensajes. `check_tool_coherence` mantiene el prompt simple de 2 criterios. |
| `_scrubbed_env()` se llama en cada subprocess creation | Es O(n) sobre env vars (~50-100). Negligible vs el costo del subprocess. Cacheable si se quiere optimizar. |
