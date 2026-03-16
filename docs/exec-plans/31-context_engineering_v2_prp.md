# PRP: Context Engineering v2 — Optimización del Context Window

## Objetivo

Reducir y estructurar el contexto enviado a qwen3.5:9b para maximizar calidad de respuesta.
Ver PRD (`31-context_engineering_v2_prd.md`) para contexto, decisiones y restricciones.

## Archivos a Modificar

| Archivo | Cambio |
|---|---|
| `app/context/token_estimator.py` | **Nuevo.** Estimador de tokens (chars/4), budget tracking, log helper |
| `app/context/context_builder.py` | **Nuevo.** Construye 1-2 system messages consolidados con secciones XML |
| `app/context/conversation_context.py` | Extender `build()` con daily_logs, relevant_notes, query_embedding. Agregar `token_estimate` field |
| `app/webhook/router.py` | Reemplazar `_build_context()` + fetches manuales con `ConversationContext.build()` + `ContextBuilder`. Filtrar capabilities por categoría |
| `app/conversation/manager.py` | Agregar `get_windowed_history()` — ventana deslizante con summary |
| `app/database/repository.py` | Agregar `search_similar_memories_with_distance()` — retorna (content, distance) para threshold filtering |
| `app/skills/executor.py` | Inyectar/leer scratchpad en agent tool loop |
| `app/agent/loop.py` | Propagar scratchpad entre rounds, inyectar como system message compacto |
| `tests/test_token_estimator.py` | **Nuevo.** Tests para estimador |
| `tests/test_context_builder.py` | **Nuevo.** Tests para builder XML |
| `tests/test_context_windowing.py` | **Nuevo.** Tests para ventana deslizante |
| `tests/test_conversation_context.py` | Extender tests existentes o nuevo archivo |

## Phase 1: Token Budget Tracking

> Meta: saber cuántos tokens estamos enviando hoy, antes de optimizar.

- [x] Crear `app/context/token_estimator.py`:
  ```python
  def estimate_tokens(text: str) -> int:
      """Proxy estimator: chars / 4. Aceptable ±20% para qwen3 BPE."""
      return max(1, len(text) // 4)

  def estimate_messages_tokens(messages: list[ChatMessage]) -> int:
      """Estima tokens totales de una lista de mensajes."""
      return sum(estimate_tokens(m.content) for m in messages)

  def log_context_budget(
      messages: list[ChatMessage],
      context_limit: int = 32_000,
      logger: Logger | None = None,
  ) -> int:
      """Log estimated token usage. Returns estimate."""
  ```
  - Si estimate > `context_limit * 0.8`: log WARNING "context nearing limit"
  - Si estimate > `context_limit`: log ERROR "context likely exceeds window"
  - Siempre log INFO con `estimated_tokens`, `message_count`, `system_message_count`
- [x] Integrar en `app/webhook/router.py`:
  - Llamar `log_context_budget(context)` después de `_build_context()` (antes de la llamada LLM)
  - token estimate guardado en `ctx.token_estimate`
- [x] Integrar en `app/agent/loop.py`:
  - Llamar antes de cada `execute_tool_loop()` en la sesión reactiva
- [x] Tests `tests/test_token_estimator.py`:
  - `test_estimate_tokens_basic` — "hola" → 1 token
  - `test_estimate_messages` — lista de ChatMessages → suma correcta
  - `test_log_warns_near_limit` — verify WARNING log cuando >80%
  - `test_log_errors_over_limit` — verify ERROR log cuando >100%
- [ ] **Medir baseline**: (requiere app corriendo con Ollama — completar manualmente)
  - "hola" → ¿cuántos tokens?
  - "qué tiempo hace en BA" → ¿cuántos tokens?
  - Conversación con 20 msgs de historial → ¿cuántos tokens?

## Phase 2: System Prompt Consolidation con XML Tags

> Meta: pasar de 6-7 system messages separados a 1-2 bien estructurados.

- [x] Crear `app/context/context_builder.py`:
  ```python
  class ContextBuilder:
      """Builds a structured system prompt with XML-delimited sections."""

      def __init__(self, system_prompt: str):
          self._sections: list[tuple[str, str]] = []  # (tag_name, content)
          self._base_prompt = system_prompt

      def add_section(self, tag: str, content: str) -> "ContextBuilder":
          """Add a named section. Skipped if content is empty/None."""
          if content:
              self._sections.append((tag, content))
          return self

      def build_system_message(self) -> str:
          """Consolidate into a single system prompt with XML sections."""
          parts = [self._base_prompt]
          for tag, content in self._sections:
              parts.append(f"\n<{tag}>\n{content}\n</{tag}>")
          return "\n".join(parts)
  ```
- [x] Refactorear `_build_context()` en `router.py` para usar `ContextBuilder`:
  - Los helpers `_format_memories()` y `_format_notes()` extraen el formateo de listas
  - **Nota**: user_facts se siguen inyectando como system message separado en el tool loop
    (executor.py), no en el prompt principal — porque solo se necesitan cuando hay tools
- [x] Tests `tests/test_context_builder.py`:
  - `test_empty_sections_skipped` — secciones con content vacío no aparecen
  - `test_xml_tags_present` — output contiene `<user_memories>`, `</user_memories>`, etc.
  - `test_base_prompt_preserved` — el system prompt base está al inicio
  - `test_history_appended_after_system` — history messages van después del system message

## Phase 3: History Windowing (ventana deslizante)

> Meta: reducir tokens de historial sin perder continuidad.

- [x] Agregar método en `app/conversation/manager.py`:
  ```python
  async def get_windowed_history(
      self,
      phone_number: str,
      verbatim_count: int = 8,
  ) -> tuple[list[ChatMessage], str | None]:
  ```
  - Si `len(history) <= verbatim_count`: retorna (history, None)
  - Si `len(history) > verbatim_count`: retorna (history[-verbatim_count:], summary)
  - El summary viene de `repository.get_latest_summary()` — ya existe, no genera uno nuevo
  - **No agrega latencia**: usa datos que ya están en DB
- [x] Actualizar `ConversationContext.build()` para usar `get_windowed_history()`
  - El summary de msgs viejos va como sección `<conversation_summary>` (solo si hay)
  - Los últimos N msgs van como `role=user/assistant` después del system message
- [x] Agregar setting `history_verbatim_count: int = 8` en `app/config.py`
- [x] Tests `tests/test_context_windowing.py`:
  - `test_short_history_no_windowing` — <8 msgs retorna todos
  - `test_long_history_windowed` — 20 msgs retorna últimos 8 + summary
  - `test_no_summary_available` — history larga pero sin summary → retorna últimos 8, None
  - `test_verbatim_count_configurable` — funciona con N=5, N=10

## Phase 4: Capabilities Filtering

> Meta: no inyectar la lista de tools cuando no se necesitan.

- [x] En `app/webhook/router.py`, condicionar `_build_capabilities_section()`:
  - Solo llamar si `has_tools and pre_classified != ["none"]`
  - Si `pre_classified` es `["none"]` o no hay tools: `skills_summary = None`
- [x] Crear variante filtrada `_build_capabilities_for_categories()`:
  - Filtrar skills y tools por las categorías del `pre_classified`
  - Commands se incluyen siempre (son cortos, el usuario puede preguntar sobre ellos)
  - MCP servers se filtran por las categorías relevantes
- [ ] Tests de capabilities filtering (gated por integración — verificar manualmente):
  - `pre_classified=["none"]` → `skills_summary=None` (verificado en código)
  - Filtro por categoría aplicado (verificado en código)

## Phase 5: Memory Relevance Threshold

> Meta: inyectar solo memorias relevantes, no todas las top-K.

- [x] Agregar `search_similar_memories_with_distance()` en `app/database/repository.py`:
  ```python
  async def search_similar_memories_with_distance(
      self, embedding: list[float], top_k: int = 10
  ) -> list[tuple[str, float]]:
      """Return (content, distance) pairs sorted by distance."""
  ```
  - sqlite-vec `distance` column ya está disponible en el ORDER BY
- [x] Actualizar `_get_memories()` en `router.py` y en `ConversationContext.build()`:
  - Llamar `search_similar_memories_with_distance()`
  - Filtrar por `distance < threshold` (setting: `memory_similarity_threshold: float = 1.0`)
  - Si ninguna pasa el threshold: fallback a top-3 (siempre tener algo de contexto)
  - Log: `"Memories: %d/%d passed threshold (%.2f)", injected, total, threshold`
- [x] Agregar setting `memory_similarity_threshold: float = 1.0` en `app/config.py`
  - Nota: sqlite-vec usa L2 distance, no cosine similarity. Threshold 1.0 es razonable
    para nomic-embed-text 768d. Afinar con datos reales post-implementación.

## Phase 6: ConversationContext.build() Adoption

> Meta: eliminar código duplicado entre `ConversationContext.build()` y `_run_normal_flow()`.

- [x] Extender `ConversationContext.build()` en `app/context/conversation_context.py`:
  - Agregar params: `ollama_client`, `settings`, `daily_log`, `vec_available`
  - Movida la lógica de Phase A + B de `_run_normal_flow()` adentro:
    - `_get_query_embedding()` → `self.query_embedding`
    - `_get_memories_with_threshold()` → `self.memories`
    - `_get_relevant_notes()` → `self.relevant_notes`
    - `daily_log.load_recent()` → `self.daily_logs`
    - `_get_projects_summary()` → `self.projects_summary`
    - `get_windowed_history()` → `self.history`, `self.summary`
  - Agregar field `token_estimate: int = 0` — estimado después de build
  - `save_message()` queda fuera del build (side effect, no lectura)
- [x] Actualizar `_run_normal_flow()` en `router.py`:
  - Reemplaza Phases A+B manuales con `ConversationContext.build()` + `save_message` paralelos
  - Phase C usa `ctx.sticky_categories`, `ctx.user_facts` directamente
  - Phase D usa `ContextBuilder` alimentado desde campos del ctx
- [x] Mantener `_run_normal_flow()` como wrapper con trace spans

## Phase 7: Agent Scratchpad

> Meta: dar al agente un espacio de notas persistente entre rounds.

- [x] En `app/agent/loop.py`, `_run_reactive_session()`:
  - Antes de cada round, inyectar scratchpad como system message (si non-empty)
  - Después de cada round, extraer scratchpad del reply si contiene marker
  - Markers: `<scratchpad>...</scratchpad>` en el reply del LLM
- [x] Agregar `scratchpad: str = ""` al `AgentSession` model (`app/agent/models.py`)
- [x] En `_AGENT_SYSTEM_PROMPT`, agregar instrucción sobre scratchpad
- [x] Helpers en `loop.py`:
  - `_inject_scratchpad()` — Insert scratchpad as system message after main prompt
  - `_extract_scratchpad()` — Extract scratchpad content from reply, returns (scratchpad, clean_reply)

## Phase 8: Verificación y Documentación

- [x] Correr tests: 507 passed, 0 failed
- [x] Lint: ruff — 0 errors
- [x] Typecheck: mypy app/ — Success: no issues found in 104 source files
- [ ] **Medir post-implementación** vs baseline (requiere app corriendo con Ollama)
- [x] Actualizar `CLAUDE.md` con patrones nuevos
- [x] Crear `docs/features/31-context_engineering_v2.md`
- [x] Crear `docs/testing/31-context_engineering_v2_testing.md`
- [x] Actualizar `docs/exec-plans/README.md` con entrada 31
- [x] Actualizar `docs/features/README.md` y `docs/testing/README.md`

## Resultados de Medición (completar durante ejecución)

| Escenario | Tokens ANTES | Tokens DESPUÉS | Reducción |
|---|---|---|---|
| "hola" (sin historial) | N/A (no medido) | ~300-500 (estimado) | Capabilities eliminadas |
| "hola" (20 msgs historial) | N/A | ~800-1200 (8 msgs verbatim) | 12 msgs ahorrados |
| "qué tiempo hace en BA" | N/A | Solo tools de weather | Capabilities filtradas |
| Sesión agéntica round 10 | N/A | Scratchpad ~200 tokens vs tool history | Más coherente |
