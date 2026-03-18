# PRD: LLM Tool Awareness & Langfuse Full Observability (Plan 50)

## Objetivo y Contexto

Dos problemas detectados en sesión real (2026-03-18) con usuario final:

### Problema A: El LLM no es consciente de sus propias capacidades

El usuario pidió "investigues en la web el fixture de Rosario Central 2026". El LLM:
1. **Usó `web_search` exitosamente** y recibió 5 resultados
2. **Ignoró sus propios resultados** y respondió: "Fecha actual del sistema: Agosto 2025" (incorrecta)
3. Cuando el usuario insistió, dijo: "no tengo capacidad para navegar por internet en tiempo real"
4. Cuando el usuario le indicó que sí tiene tools, el LLM pidió `request_more_tools`, recibió `fetch_readable, fetch_html`, y en vez de usarlas, respondió "voy a realizar una búsqueda exhaustiva" — sin ejecutar nada

**Resultado**: 5 thumbs-down consecutivos, el usuario tuvo que corregir al bot 4 veces.

### Problema B: Langfuse no muestra el input real al LLM

Al investigar el incidente en Langfuse, no es posible ver:
- Los mensajes completos que se enviaron a Ollama (solo `message_count`)
- El input/output del guardrails pipeline
- El prompt completo del classifier (solo los primeros 200 chars del user message)
- El contexto construido (memories, notes, daily logs, capabilities) que el LLM recibió

Esto hace imposible hacer debugging post-hoc de respuestas malas.

## Root Causes Identificados

| # | Root Cause | Evidencia del log | Ubicación |
|---|-----------|-------------------|-----------|
| RC-1 | Fecha al final del system prompt con bajo peso semántico | LLM dice "Agosto 2025" cuando `Current Date: 2026-03-18` está en el prompt | `profiles/prompt_builder.py:30` |
| RC-2 | System prompt no declara que el LLM tiene herramientas de búsqueda web | LLM afirma "no tengo capacidad para navegar por internet" después de usar `web_search` | `config.py:66-81`, `prompt_registry.py:16-23` |
| RC-3 | Prompt registry desincronizado con config.py | `config.py` tiene reglas de grounding que `prompt_registry.py` no tiene | `eval/prompt_registry.py:16-24` |
| RC-4 | `request_more_tools` no fuerza al LLM a usar las tools cargadas | LLM responde con texto prometiendo que "va a buscar" en vez de ejecutar las tools | `skills/executor.py:563-570` |
| RC-5 | Spans de Langfuse solo capturan metadata, no el input real al LLM | `gen_span.set_input({"message_count": N})` en vez del messages array | `skills/executor.py:387-390` |
| RC-6 | Guardrails pipeline sin input/output en Langfuse | Solo metadata (pass/fail) pero no qué texto se evaluó | `webhook/router.py:1575-1592` |
| RC-7 | Classifier span trunca input a 200 chars | Prompt completo con examples y recent_context no se captura | `skills/router.py:320` |

## Alcance (In Scope & Out of Scope)

### In Scope

**Stream A — LLM Tool Awareness (Prompt Engineering)**
- Fecha prominente al inicio del system prompt con formato enfático
- Declaración explícita de capacidades de búsqueda web en system prompt
- Sincronizar `prompt_registry.py` con las reglas de grounding de `config.py`
- Nudge post-`request_more_tools` para forzar uso de tools cargadas
- Instrucción anti-alucinación: "NEVER say you cannot search the web"

**Stream B — Langfuse Full Observability**
- Capturar messages completos como input en spans `llm:iteration_N`
- Capturar input/output del guardrails pipeline en spans
- Capturar prompt completo del classifier en span
- Capturar context breakdown (sections, token estimate) como metadata
- Capturar compaction input/output completo

### Out of Scope
- Cambios al modelo LLM (seguimos con qwen3.5:9b)
- Cambios a la lógica del tool router (classify_intent, select_tools)
- Nuevo UI o dashboard de Langfuse
- Cambios a la tool execution pipeline (security, audit)
- Fine-tuning o LoRA para mejorar tool awareness
- Migración a Langfuse SDK v3 (Plan 43 separado)

## Casos de Uso Críticos

### 1. Usuario pide buscar info en la web → LLM usa tools y presenta resultados

**Antes:** LLM usa `web_search`, recibe resultados, pero dice "no puedo acceder a internet".
**Después:** LLM usa `web_search`, recibe resultados, y los presenta al usuario directamente.

### 2. LLM pide `request_more_tools` → usa las tools inmediatamente

**Antes:** LLM pide tools, recibe "Loaded 4 new tools", responde "voy a buscar..." (nunca busca).
**Después:** LLM pide tools, recibe "Loaded 4 new tools. NOW call them.", ejecuta las tools.

### 3. Developer investiga respuesta mala en Langfuse → ve el input completo

**Antes:** Solo ve `{"message_count": 9, "tool_count": 1}` — imposible diagnosticar.
**Después:** Ve el system prompt completo, memories inyectadas, historia, tools disponibles.

### 4. Guardrail falla → Developer ve qué texto se evaluó

**Antes:** Ve `{"passed": false, "failed_checks": ["language_match"]}` — no sabe sobre qué texto.
**Después:** Ve `input: {user_text: "...", reply: "..."}` + `output: {checks: [...], passed: false}`.

## Restricciones Arquitectónicas

- **Modelo**: qwen3.5:9b — instrucciones deben ser concisas y directas (no tolera prompts largos)
- **Token budget**: System prompt ya usa ~1500-2000 tokens. Agregar máximo ~200 tokens de instrucciones
- **Langfuse payload**: No enviar mensajes completos raw (pueden ser enormes). Usar preview truncado + metadata estructurada
- **`think=False`**: No afecta a este plan (solo se usa en prompts binarios/JSON)
- **Backward compat**: No romper tests existentes ni cambiar la interfaz de TraceContext/SpanData
- **Performance**: Serialización de inputs para Langfuse no debe agregar >5ms al critical path
