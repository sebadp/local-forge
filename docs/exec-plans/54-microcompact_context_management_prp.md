# PRP: MicroCompact & Deferred Tool Loading — Context Window Optimization (Plan 54)

## Archivos a Modificar

### Feature A: MicroCompact
- `app/formatting/microcompact.py`: **Nuevo** — `microcompact_messages()` function
- `app/skills/executor.py`: Integrar microcompact antes de cada LLM call en el tool loop
- `app/webhook/router.py`: Integrar microcompact en `_build_context()` para el flow normal
- `tests/test_microcompact.py`: **Nuevo**

### Feature B: Lazy Tool Loading
- `app/skills/tools/meta_tools.py`: **Nuevo** — `discover_tools` handler
- `app/skills/router.py`: Modificar `select_tools()` para retornar top-K + discover_tools
- `app/skills/registry.py`: Agregar método `search_tools(query: str) -> list[ToolDefinition]`
- `skills/meta/SKILL.md`: **Nuevo** — Skill definition para discover_tools
- `tests/test_meta_tools.py`: **Nuevo**

## Fases de Implementación

### Phase 1: MicroCompact

- [x] Crear `app/formatting/microcompact.py`:
  ```python
  COMPACTABLE_TOOLS = {
      "web_search", "web_research", "search_source_code", 
      "read_source_file", "read_lines", "get_recent_messages",
      "search_notes", "run_command", "get_file_outline",
      "get_file_contents", "search_repositories",
  }
  
  REPLACEMENT = "[Tool result from {tool_name} cleared — returned {n_chars} chars]"
  
  def microcompact_messages(
      messages: list[ChatMessage],
      max_age_rounds: int = 2,
      current_round: int = 0,
  ) -> list[ChatMessage]:
      """Replace old verbose tool results with compact stubs."""
  ```
- [x] La lógica:
  - Iterar messages en reverse, contando "rounds" (un round = un assistant message con tool_calls + los tool results)
  - Para rounds ≥ `max_age_rounds` anteriores al current_round:
    - Si el tool_name está en `COMPACTABLE_TOOLS` y el resultado tiene >200 chars:
      - Reemplazar content con `REPLACEMENT.format(...)`
  - Retornar nueva lista (no mutar la original)
- [x] Integrar en `execute_tool_loop()`: llamar `microcompact_messages(messages, current_round=iteration)` antes de cada `ollama_client.chat()`
- [x] Integrar en `_run_normal_flow()`: llamar antes del LLM principal si `len(messages) > 20`
- [x] Tests: verificar que compacta solo los tools correctos, respeta max_age, no toca el round actual

### Phase 2: Lazy Tool Loading

- [x] Agregar a `SkillRegistry`:
  ```python
  def search_tools(self, query: str, limit: int = 5) -> list[dict]:
      """Search registered tools by name/description fuzzy match."""
  ```
  - Simple: tokenizar query, matchear contra tool name + description, score por overlap
- [x] Crear `app/skills/tools/meta_tools.py`:
  ```python
  async def discover_tools(query: str) -> str:
      """Find available tools by keyword. Returns tool names and descriptions."""
  ```
  - Llama `registry.search_tools(query)` 
  - Retorna formatted list: `- {name}: {description}` 
- [x] Crear `skills/meta/SKILL.md` con frontmatter:
  ```yaml
  name: meta
  description: Tool discovery and system introspection
  version: "1.0"
  tools:
    - name: discover_tools
      handler: meta_tools.discover_tools
  ```
- [x] Modificar `select_tools()` en `app/skills/router.py`:
  - Actual: retorna todas las tools de los skills matcheados
  - Nuevo: retorna top `MAX_TOOLS_IN_CONTEXT` (default 6) + siempre incluir `discover_tools`
  - Si el skill clasificado tiene ≤6 tools, retornar todas (sin cambio funcional)
  - Si tiene >6, retornar las 6 más relevantes + `discover_tools`
- [x] Tests: verify discover_tools returns relevant results, select_tools caps at MAX

### Phase 3: Documentación & QA

- [x] `make test` pasa
- [x] `make lint` pasa
- [x] Crear `docs/features/54-microcompact_lazy_tools.md`
- [x] Actualizar `AGENTS.md` con el nuevo skill `meta` y el módulo `microcompact`
- [x] Actualizar `CLAUDE.md` con el patrón de microcompact en la sección de Performance
