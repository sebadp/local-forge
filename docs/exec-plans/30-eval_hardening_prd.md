# PRD: Eval Stack Hardening (Plan 30)

## Objetivo y Contexto

Audit de 2026-02-27 reveló que el stack de eval existe pero tiene 5 gaps que lo hacen ineficaz
en producción. El objetivo es cerrar esos gaps para que el eval sea observable (Langfuse), accionable
(fixes reales, no métricas falsas) y ejecutable offline (CI).

### Gaps confirmados en código

| # | Gap | Evidencia en código |
|---|-----|---------------------|
| 1 | `language_match` remediation débil | `_handle_guardrail_failure()` en router.py:554–570 — hint en inglés → qwen3 puede ignorarlo |
| 2 | LLM judges inutilizables | `pipeline.py:103` — `timeout: float = 0.5` hardcodeado; qwen3.5:9b tarda 2–5s → siempre timeout |
| 3 | `run_quick_eval` mide word overlap | `eval_tools.py:221–244` — set intersection entre palabras, no semánticamente correcto |
| 4 | Tags nunca se populan | `dataset.py:39–71` — `add_dataset_entry()` llamado sin `tags=` → `eval_dataset_tags` vacía |
| 5 | Sin benchmark offline | No existe `scripts/run_eval.py` |
| 6 | Remediation invisible en Langfuse | `_handle_guardrail_failure()` no recibe `trace_ctx` → llamada LLM de remediation no aparece en Langfuse |

---

## Alcance (In Scope & Out of Scope)

### In Scope

- **Fix language_match remediation**: prompt en el idioma del usuario + instrucción directa al modelo
- **Timeout configurable para LLM judges**: `guardrails_llm_timeout: float = 3.0` en `Settings`
- **LLM-as-judge en `run_quick_eval`**: reemplazar word overlap con prompt binario yes/no
- **Tags automáticos en curation**: `guardrail:{check_name}` para failures en `eval_dataset_tags`
- **Benchmark offline `scripts/run_eval.py`**: CLI sin levantar el servidor completo
- **Tracing de remediation en Langfuse**: `_handle_guardrail_failure` recibe `trace_ctx`, crea span `guardrails:remediation`

### Out of Scope

- Evolución automática de prompts sin aprobación humana
- A/B testing de versiones de prompt (requiere routing de tráfico)
- Scores por tool individual (requiere refactor de executor + schema)
- Tool selection accuracy metric

---

## Casos de Uso Críticos

### 1. Usuario escribe en español, LLM responde en inglés

**Antes:** `language_match` falla → `_handle_guardrail_failure` hace retry con hint en inglés
("Respond in Spanish only") → qwen3 puede ignorar la instrucción → se envía respuesta en inglés.

**Después:** Prompt de remediation bilingüe explícito:
```
"IMPORTANTE: El usuario escribió en español.
Reescribe SOLO en español, sin cambiar el contenido.
IMPORTANT: The user wrote in Spanish. Rewrite ONLY in Spanish."
```
La llamada LLM de remediation aparece en Langfuse como span hijo de `guardrails`.

### 2. `guardrails_llm_checks=True` pero siempre fail-open

**Antes:** `_run_async_check(timeout=0.5)` → qwen3 tarda >0.5s → TimeoutError → warning → pass (fail-open).
No hay visibilidad en Langfuse de que el check ni siquiera corrió.

**Después:** `guardrails_llm_timeout=3.0` en Settings → checks realmente corren y detectan problemas.

### 3. `run_quick_eval` con overlap=80% pero respuesta incorrecta

**Antes:** Bot responde "No puedo calcular eso" con 80% overlap de palabras contra expected "No, eso no funciona".
Métrica dice "correcto" aunque semánticamente distintos.

**Después:** LLM-as-judge evalúa semánticamente: "Does the actual answer correctly answer the question? Reply ONLY yes or no."

### 4. Dataset de failures sin contexto de causa

**Antes:** `eval_dataset_tags` vacía → no se puede filtrar por check que falló.

**Después:** Failure curado automáticamente con tags `["guardrail:language_match"]` → filtrable.

### 5. Validación en CI sin levantar servidor

**Antes:** No hay forma de correr eval sin el servidor completo levantado.

**Después:** `python scripts/run_eval.py --db data/localforge.db --ollama http://localhost:11434`
→ imprime tabla de resultados y exit code 0/1 según threshold de accuracy.

---

## Visibilidad en Langfuse (Requisito Clave)

Todos los flujos del eval stack deben aparecer en Langfuse con detalle suficiente para diagnosticar:

| Flujo | Span actual | Span objetivo |
|-------|-------------|---------------|
| Run guardrails | `guardrails` (kind=guardrail) con `passed`, `latency_ms` | + `failed_checks` list en metadata |
| Remediation LLM call | ❌ invisible | `guardrails:remediation` (kind=generation) hijo del span guardrail, con `lang_code`, `prompt` |
| LLM judge (tool_coherence) | ❌ dentro de span `guardrails` pero sin detalle | Span `guardrails:llm_judge:tool_coherence` o al menos resultado en metadata |
| `run_quick_eval` | ❌ llamada directa, sin trace | Se ejecuta dentro de tool loop → ya tiene trace context; el `ollama_client.chat()` interno genera span si está dentro de `execute_tool_loop` con `get_current_trace()` |

### Implementación: trace en `_handle_guardrail_failure`

La función necesita recibir `trace_ctx` (opcional) para crear el span de remediation:

```python
async def _handle_guardrail_failure(
    report,
    context: list[ChatMessage],
    ollama_client: OllamaClient,
    original_reply: str,
    trace_ctx=None,          # NEW: TraceContext | None
) -> str:
    ...
    elif "language_match" in failed_names:
        ...
        # Span en Langfuse si hay trace activo
        if trace_ctx:
            async with trace_ctx.span("guardrails:remediation", kind="generation") as span:
                span.set_metadata({"lang_code": detected_code, "hint": hint_content})
                retry = await ollama_client.chat(context + [hint_msg])
        else:
            retry = await ollama_client.chat(context + [hint_msg])
```

---

## Restricciones Arquitectónicas / Requerimientos Técnicos

- **Fail-open**: Todos los cambios en guardrails deben mantener fail-open — errores → pass, nunca bloquear respuesta
- **Best-effort**: Tags, curation, benchmark → nunca propaguen excepciones
- **Settings-driven**: Timeout LLM configurable via `.env`, no hardcodeado
- **Retrocompatible**: Firma de `maybe_curate_to_dataset()` puede agregar `failed_check_names=[]` como kwarg opcional
- **Sin recursión**: `run_guardrails` no se llama sobre la respuesta remediada
- **`think=False`** en LLM-as-judge: prompt binario, no queremos chain-of-thought
- **Script offline**: `scripts/run_eval.py` usa `asyncio.run()` con `init_db()` + `OllamaClient` directo — SIN importar la app FastAPI completa
