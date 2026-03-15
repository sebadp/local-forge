# Context Engineering v2 — Optimización del Context Window

## Resumen

LocalForge injected de 6-7 system messages separados a qwen3.5:9b sin medir cuántos tokens consumía
ni filtrar por relevancia. Context Engineering v2 implementa 7 optimizaciones que reducen el
contexto al mínimo de alta señal por request.

## Cambios implementados

### 1. Token Budget Tracking (`app/context/token_estimator.py`)

- `estimate_tokens(text)` — proxy chars/4, ±20% para BPE
- `estimate_messages_tokens(messages)` — suma total
- `log_context_budget(messages)` — log INFO/WARNING/ERROR según uso vs 32K limit
- Integrado en `router.py` (después de build) y `agent/loop.py` (antes de cada round)
- El estimado se guarda en `ctx.token_estimate`

### 2. System Prompt Consolidation (`app/context/context_builder.py`)

- `ContextBuilder` consolida N secciones en un solo system message con XML tags
- Reemplaza el patrón de `context.append(ChatMessage(role="system", ...))`
- `_build_context()` en `router.py` usa `ContextBuilder` con secciones:
  `<user_memories>`, `<active_projects>`, `<relevant_notes>`, `<recent_activity>`,
  `<capabilities>`, `<conversation_summary>`
- Secciones vacías se omiten automáticamente

### 3. History Windowing (`app/conversation/manager.py`)

- `get_windowed_history(phone, verbatim_count=8)` retorna `(recent, summary)`
- Si `len(history) <= verbatim_count`: retorna todos, sin summary
- Si `len(history) > verbatim_count`: retorna `history[-N:]` + summary existente de DB
- Zero-latency: usa summary ya computado por `maybe_summarize`, sin nuevo LLM call
- Setting: `history_verbatim_count: int = 8` en `config.py`

### 4. Capabilities Filtering (`app/webhook/router.py`)

- Capabilities ahora se construyen DESPUÉS de `classify_intent` (Phase C)
- `pre_classified == ["none"]` → `skills_summary = None` (skip completamente)
- Categorías conocidas → `_build_capabilities_for_categories()` filtra por relevancia
- Commands siempre incluidos (son cortos, el usuario puede preguntar por ellos)
- MCP tools y skills filtrados a las categorías activas

### 5. Memory Relevance Threshold (`app/database/repository.py`)

- `search_similar_memories_with_distance()` retorna `(content, L2_distance)` pairs
- `_get_memories()` filtra por `memory_similarity_threshold` (default 1.0 = accept all)
- Fallback: si ninguna pasa el threshold, se retornan top-3 (siempre hay contexto)
- Log: `"context.memories: N/M passed threshold (T.TT)"`
- Setting: `memory_similarity_threshold: float = 1.0` en `config.py`

### 6. ConversationContext.build() Adoption (`app/context/conversation_context.py`)

- `build()` extendido con params: `ollama_client`, `settings`, `daily_log`, `vec_available`
- Campos nuevos: `daily_logs`, `relevant_notes`, `projects_summary`, `query_embedding`, `token_estimate`
- Fetches internos en paralelo: embedding → memories, windowed_history, sticky, logs, notes, projects
- `_run_normal_flow()` reemplaza Phases A+B con `ConversationContext.build()` + `save_message` paralelos
- `ctx.sticky_categories` y `ctx.user_facts` alimentan Phase C directamente

### 7. Agent Scratchpad (`app/agent/loop.py`, `app/agent/models.py`)

- `AgentSession.scratchpad: str = ""` — campo persistente entre rounds
- `_inject_scratchpad(messages, scratchpad)` — inserta como system message después del main prompt
- `_extract_scratchpad(reply)` — extrae `<scratchpad>...</scratchpad>` del reply, retorna `(scratchpad, clean_reply)`
- El scratchpad se actualiza automáticamente después de cada round si el LLM usa los tags
- `_AGENT_SYSTEM_PROMPT` explica el mecanismo al agente
- Persiste hallazgos, decisiones, file paths sin re-cargar todo el historial

## Decisiones de diseño

### Un solo system message vs múltiples

Ollama con qwen3.5:9b fragmenta la atención entre múltiples `role=system` blocks. Consolidar en
uno con XML tags permite al modelo "navegar" secciones estructuradas. Con modelos 8B esto es
crítico porque el attention budget es limitado.

### chars/4 como estimador

qwen3.5:9b usa BPE propio. Integrar el tokenizer real agrega dependencia pesada. chars/4 da ±20%
de precisión — suficiente para logging y alertas. Si se necesita precisión, el swap es trivial.

### Ventana deslizante vs summarization on-the-fly

`maybe_summarize` ya genera summaries en background post-40-mensajes. Summarization síncrona
agrega ~3s de latencia. La ventana usa datos ya en DB — cero latencia adicional.

### memory_similarity_threshold = 1.0 (accept all por defecto)

sqlite-vec usa L2 distance, no cosine similarity. El threshold óptimo depende de la distribución
de embeddings reales. 1.0 es conservador (acepta todo) hasta que se calibre con datos reales.

### Scratchpad como string libre

Hacerlo structured (JSON) requiere que qwen3.5:9b genere output válido y parsearlo — falla ~20%
del tiempo. Un string libre es más robusto y suficiente para el caso de uso.

## Archivos modificados

| Archivo | Cambio |
|---|---|
| `app/context/token_estimator.py` | Nuevo |
| `app/context/context_builder.py` | Nuevo |
| `app/context/conversation_context.py` | Extendido build() |
| `app/webhook/router.py` | ConversationContext.build(), ContextBuilder, capabilities filter |
| `app/conversation/manager.py` | get_windowed_history() |
| `app/database/repository.py` | search_similar_memories_with_distance() |
| `app/agent/loop.py` | Scratchpad inject/extract, token tracking |
| `app/agent/models.py` | AgentSession.scratchpad field |
| `app/config.py` | history_verbatim_count, memory_similarity_threshold |
| `tests/test_token_estimator.py` | Nuevo (9 tests) |
| `tests/test_context_builder.py` | Nuevo (7 tests) |
| `tests/test_context_windowing.py` | Nuevo (6 tests) |

## Gotchas

- `search_similar_memories_with_distance()` requiere sqlite-vec. Si no está disponible, el
  `ConversationContext.build()` hace fallback a `get_active_memories()` via `except` block.
- El scratchpad solo funciona en el reactive loop (`_run_reactive_session`), no en el
  planner-orchestrator (`_run_planner_session`) — el planner tiene su propio mecanismo de
  persistencia de resultados por task.
- `memory_similarity_threshold` usa L2 distance (no cosine). Un threshold de 0.5 para cosine
  equivale aproximadamente a L2=1.0 para vectores normalizados de 768 dims — pero varía.
- La integración `add_metadata` en `TraceContext` no existe (TraceContext usa spans). El token
  estimate se loguea pero no se envía directamente a Langfuse (podría añadirse como span metadata).
