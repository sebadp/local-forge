# Feature: Eval Stack Hardening (Plan 30)

## Problema que resuelve

El stack de eval de LocalForge existía pero tenía 5 gaps que lo hacían ineficaz:
1. `language_match` remediation débil — hint en inglés que qwen3 podía ignorar
2. LLM judges inutilizables — timeout de 0.5s (siempre timeout con qwen3.5:9b local)
3. `run_quick_eval` medía word overlap (métrica semánticamente inválida)
4. Dataset tags nunca se populaban — sin filtrado por causa de fallo
5. Sin benchmark offline — no se podía evaluar en CI

Se agrega un 6º fix transversal: visibilidad en Langfuse de la llamada LLM de remediation.

---

## Cambios implementados

### 1. Language match remediation — prompt bilingüe

**Archivo:** `app/webhook/router.py` — `_handle_guardrail_failure()`

El hint de remediation ahora es bilingüe (target language + English fallback) para que qwen3.5:9b
lo entienda independientemente del idioma en que esté "pensando":

```
IMPORTANTE: El usuario escribió en español.
Reescribe la respuesta SOLO en español, sin cambiar el contenido.
IMPORTANT: The user wrote in español.
Rewrite the response ONLY in español, do not change the content.
```

### 2. Span Langfuse para remediation

**Archivo:** `app/webhook/router.py` — `_handle_guardrail_failure(trace_ctx=None)`

La función ahora acepta `trace_ctx` opcional. Si se provee, la llamada LLM de remediation
se wrappea en un span hijo `guardrails:remediation` (kind=generation) con metadata:
- `check`: nombre del check que falló (`"language_match"`)
- `lang_code`: código ISO del idioma detectado (ej. `"es"`)

El span `guardrails` existente ahora incluye `failed_checks: list[str]` en su metadata.

### 3. Timeout configurable para LLM judges

**Archivos:** `app/config.py` + `app/guardrails/pipeline.py`

```python
# Settings:
guardrails_llm_timeout: float = 3.0  # antes hardcodeado a 0.5s
```

`run_guardrails()` lee `settings.guardrails_llm_timeout` y lo pasa a `_run_async_check()`.
Los checks `tool_coherence` y `hallucination_check` (opt-in vía `guardrails_llm_checks=True`)
ahora realmente corren con qwen3.5:9b local.

### 4. `OllamaClient.chat()` acepta `think: bool | None`

**Archivo:** `app/llm/client.py`

```python
async def chat(self, messages, model=None, think: bool | None = None) -> str
```

El parámetro se propaga a `chat_with_tools()`. Permite pasar `think=False` para prompts
binarios donde no se quiere chain-of-thought (ej. LLM-as-judge).

### 5. LLM-as-judge en `run_quick_eval`

**Archivo:** `app/skills/tools/eval_tools.py`

Reemplaza el word overlap por un prompt binario yes/no:

```
Question: {input_text[:300]}
Expected answer: {expected[:300]}
Actual answer: {actual[:300]}

Does the actual answer correctly and completely answer the question?
Reply ONLY 'yes' or 'no'.
```

Output: `Correct: 3/5 (60%)` + `✅`/`❌` por entrada.

### 6. Tags automáticos en auto-curation

**Archivos:** `app/eval/dataset.py` + `app/webhook/router.py`

`maybe_curate_to_dataset()` acepta `failed_check_names: list[str] | None = None`.
En el tier "failure", inserta tags `guardrail:{check_name}` en `eval_dataset_tags`.

Ejemplo: si `language_match` falla → tag `"guardrail:language_match"` → filtrable con:
```sql
SELECT * FROM eval_dataset d
JOIN eval_dataset_tags t ON t.dataset_id = d.id
WHERE t.tag = 'guardrail:language_match';
```

### 7. Script de benchmark offline

**Archivo:** `scripts/run_eval.py`

```bash
python scripts/run_eval.py [--db data/localforge.db] [--ollama http://localhost:11434]
                           [--model qwen3.5:9b] [--entry-type all] [--limit 20]
                           [--threshold 0.7]
```

- Sin FastAPI — solo `init_db()` + `OllamaClient`
- LLM-as-judge idéntico al de `run_quick_eval`
- Tabla de resultados por entrada + resumen por tipo
- Exit code 0/1 según accuracy vs threshold (útil para CI)

---

## Visibilidad en Langfuse

| Flujo | Antes | Después |
|-------|-------|---------|
| Span `guardrails` | `{passed, latency_ms}` | `{passed, latency_ms, failed_checks: [...]}` |
| Remediation LLM call | ❌ invisible | Span `guardrails:remediation` con `{check, lang_code}` |
| LLM judge timeout | 0.5s (siempre timeout) | Configurable, default 3.0s |

---

## Archivos modificados

```
app/config.py                        + guardrails_llm_timeout
app/guardrails/pipeline.py           timeout desde settings
app/llm/client.py                    chat() acepta think=
app/webhook/router.py                remediation bilingüe, trace_ctx, failed_checks_for_curation
app/eval/dataset.py                  failed_check_names → tags
app/skills/tools/eval_tools.py       LLM-as-judge
scripts/run_eval.py                  [NUEVO] benchmark offline
tests/test_guardrails.py             [NUEVO] 10 tests
tests/test_eval_tools.py             [NUEVO] 5 tests
```
