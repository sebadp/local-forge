# PRP: Project Notes & Tool RAG — Plan 41

## Archivos a Modificar

### Fase A — Project Notes Completos
- `app/models.py`: agregar `title: str | None = None` a `ProjectNote`
- `app/database/db.py`: agregar columna `title` a `project_notes`, schema `vec_tools`
- `app/database/repository.py`: `get_project_note()`, `get_unembedded_project_notes()`, update `add_project_note()` con title
- `app/skills/tools/project_tools.py`: tools `get_project_note`, `list_project_notes`, update `add_project_note` con title, fix truncado en `search_project_notes`
- `app/embeddings/indexer.py`: `backfill_project_note_embeddings()`
- `app/main.py`: agregar backfill de project notes al startup
- `app/skills/router.py`: reordenar `TOOL_CATEGORIES["projects"]` para priorizar note tools
- `tests/test_project_notes.py`: tests para nuevos tools

### Fase B — Tool RAG
- `app/database/db.py`: schema `vec_tools` (virtual table)
- `app/database/repository.py`: `save_tool_embedding()`, `search_similar_tools()`
- `app/embeddings/indexer.py`: `embed_tool_descriptions()`, `backfill_tool_embeddings()`
- `app/skills/router.py`: `build_request_more_tools_schema()` con param `query`, `select_tools_semantic()`
- `app/skills/executor.py`: handler de `request_more_tools` con semantic search
- `app/main.py`: backfill tool embeddings al startup
- `tests/test_tool_rag.py`: tests para semantic tool discovery

### Fase C — Project Notes en Contexto
- `app/context/conversation_context.py`: fetch project notes en Phase B, nuevo field `project_notes`
- `app/database/repository.py`: `search_project_notes_by_embedding()` (cross-project para contexto)
- `app/webhook/router.py`: `_format_project_notes()`, inyectar en `_build_context()`
- `app/context/context_builder.py`: (sin cambios — usa `add_section` existente)
- `tests/test_context_project_notes.py`: tests para inyección en contexto

---

## Fases de Implementación (con Checkboxes)

### Phase 1: Project Notes — Modelo y Repository (Fase A.1)

- [x] **`app/models.py`**: Agregar `title: str | None = None` a `ProjectNote`
  ```python
  class ProjectNote(BaseModel):
      id: int
      project_id: int
      title: str | None = None  # NEW
      content: str
      created_at: str = ""
  ```

- [x] **`app/database/db.py`**: Agregar columna `title` a `project_notes`
  - Migration: detectar si columna `title` existe via `PRAGMA table_info(project_notes)`
  - Si no existe: `ALTER TABLE project_notes ADD COLUMN title TEXT`
  - No rompe datos existentes (NULL default)

- [x] **`app/database/repository.py`**: Nuevos métodos
  ```python
  async def get_project_note(self, note_id: int) -> ProjectNote | None:
      """Retrieve a single project note by ID — full content, no truncation."""

  async def get_unembedded_project_notes(self) -> list[tuple[int, str]]:
      """Return (note_id, content) for project notes without embeddings."""
      # LEFT JOIN vec_project_notes WHERE vec.note_id IS NULL

  # Update existing:
  async def add_project_note(self, project_id: int, content: str, title: str | None = None) -> int:
      # Add title param, INSERT con title
  ```
  - Update `list_project_notes` para incluir `title` en SELECT
  - Update row mapping en todos los métodos para incluir `title`

### Phase 2: Project Notes — Tools (Fase A.2)

- [x] **`app/skills/tools/project_tools.py`**: Nuevo tool `get_project_note`
  ```python
  async def get_project_note(note_id: int) -> str:
      """Get the full content of a project note by ID."""
      note = await repository.get_project_note(note_id)
      if note is None:
          return f"Note {note_id} not found."
      header = f"[{note.id}]"
      if note.title:
          header += f" {note.title}"
      header += f" (project ID: {note.project_id}, created: {note.created_at})"
      return f"{header}\n\n{note.content}"
  ```

- [x] **`app/skills/tools/project_tools.py`**: Nuevo tool `list_project_notes`
  ```python
  async def list_project_notes(project_name: str) -> str:
      """List all notes in a project with previews."""
      # resolve project → list_project_notes(project_id)
      # Format: [ID] Title — content[:300]
      # If no title: [ID] content[:300]
  ```

- [x] **`app/skills/tools/project_tools.py`**: Update `add_project_note` — agregar param `title`
  ```python
  async def add_project_note(project_name: str, content: str, title: str = "") -> str:
      # title opcional, si vacío → None en DB
  ```

- [x] **`app/skills/tools/project_tools.py`**: Fix truncado en `search_project_notes`
  - Cambiar `n.content[:120]` → `n.content[:500]`
  - Incluir `n.title` si existe: `[{n.id}] {n.title}: {n.content[:500]}`

- [x] **`app/skills/tools/project_tools.py`**: Registrar los 2 nuevos tools
  ```python
  registry.register_tool(
      name="get_project_note",
      description="Get the full content of a project note by its ID",
      parameters={"type": "object", "properties": {"note_id": {"type": "integer", ...}}, "required": ["note_id"]},
      handler=get_project_note,
      category="projects",
  )
  registry.register_tool(
      name="list_project_notes",
      description="List all notes in a project with title and content preview",
      parameters={"type": "object", "properties": {"project_name": {"type": "string", ...}}, "required": ["project_name"]},
      handler=list_project_notes,
      category="projects",
  )
  ```

- [x] **`app/skills/router.py`**: Actualizar `TOOL_CATEGORIES["projects"]` — reordenar para que note tools estén al inicio del grupo funcional
  ```python
  "projects": [
      "create_project",
      "list_projects",
      "get_project",
      "add_project_note",
      "get_project_note",
      "list_project_notes",
      "search_project_notes",
      "add_task",
      "update_task",
      "delete_task",
      "project_progress",
      "update_project_status",
  ],
  ```
  > Nota: esto NO resuelve el problema de fondo (budget sigue siendo 8), pero prioriza note tools por sobre task management tools menos usados (`delete_task`, `project_progress`, `update_project_status`). La solución real es Fase B (Tool RAG).

### Phase 3: Backfill de Project Notes (Fase A.3)

- [x] **`app/embeddings/indexer.py`**: Nueva función `backfill_project_note_embeddings()`
  ```python
  async def backfill_project_note_embeddings(
      repository: Repository,
      ollama_client: OllamaClient,
      model: str,
  ) -> int:
      """Backfill embeddings for all unembedded project notes. Returns count."""
      unembedded = await repository.get_unembedded_project_notes()
      if not unembedded:
          return 0
      count = 0
      for i in range(0, len(unembedded), BATCH_SIZE):
          batch = unembedded[i : i + BATCH_SIZE]
          texts = [content[:_MAX_EMBED_CHARS] for _, content in batch if content]
          # ... same pattern as backfill_note_embeddings
      return count
  ```

- [x] **`app/main.py`**: Agregar `backfill_project_note_embeddings` al startup task
  ```python
  async def _safe_backfill() -> None:
      from app.embeddings.indexer import (
          backfill_embeddings,
          backfill_note_embeddings,
          backfill_project_note_embeddings,  # NEW
      )
      await backfill_embeddings(...)
      await backfill_note_embeddings(...)
      await backfill_project_note_embeddings(...)  # NEW
  ```

- [ ] **Tests Fase A**: `tests/test_project_notes.py`
  - Test `get_project_note` retorna contenido completo sin truncar
  - Test `list_project_notes` retorna todas las notas con previews
  - Test `add_project_note` con y sin title
  - Test `search_project_notes` muestra 500 chars (no 120)
  - Test backfill detecta project notes sin embedding

### Phase 4: Tool RAG — Schema y Repository (Fase B.1)

- [x] **`app/database/db.py`**: Agregar schema `vec_tools`
  ```python
  VEC_SCHEMA_TOOLS = (
      "CREATE VIRTUAL TABLE IF NOT EXISTS vec_tools "
      "USING vec0(tool_name TEXT PRIMARY KEY, embedding float[{dims}])"
  )
  ```
  - Crear en `init_db()` junto con las otras vec tables
  - PK es `tool_name` (string), no integer — cada tool tiene nombre único

- [x] **`app/database/repository.py`**: Nuevos métodos para tool embeddings
  ```python
  async def save_tool_embedding(
      self, tool_name: str, embedding: list[float], auto_commit: bool = True
  ) -> None:
      blob = self._serialize_vector(embedding)
      await self._conn.execute(
          "INSERT OR REPLACE INTO vec_tools (tool_name, embedding) VALUES (?, ?)",
          (tool_name, blob),
      )
      if auto_commit:
          await self._conn.commit()

  async def search_similar_tools(
      self, embedding: list[float], top_k: int = 5
  ) -> list[str]:
      """Return tool names most similar to the query embedding."""
      blob = self._serialize_vector(embedding)
      cursor = await self._conn.execute(
          "SELECT tool_name FROM vec_tools "
          "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
          (blob, top_k),
      )
      rows = await cursor.fetchall()
      return [r[0] for r in rows]
  ```

### Phase 5: Tool RAG — Embedding y Discovery (Fase B.2)

- [x] **`app/embeddings/indexer.py`**: Nueva función `embed_tool_descriptions()`
  ```python
  async def embed_tool_descriptions(
      tools_map: dict[str, dict],
      repository: Repository,
      ollama_client: OllamaClient,
      model: str,
  ) -> int:
      """Embed all tool descriptions for semantic tool discovery. Returns count."""
      texts_by_name: list[tuple[str, str]] = []
      for name, schema in tools_map.items():
          func = schema.get("function", {})
          desc = func.get("description", "")
          # Include param names for better semantic matching
          params = func.get("parameters", {}).get("properties", {})
          param_names = ", ".join(params.keys()) if params else ""
          text = f"{name}: {desc}"
          if param_names:
              text += f" (params: {param_names})"
          texts_by_name.append((name, text))

      if not texts_by_name:
          return 0

      # Batch embed
      all_texts = [t for _, t in texts_by_name]
      embeddings = await ollama_client.embed(
          [t[:_MAX_EMBED_CHARS] for t in all_texts], model=model
      )

      for (name, _), emb in zip(texts_by_name, embeddings):
          await repository.save_tool_embedding(name, emb, auto_commit=False)
      await repository.commit()
      return len(texts_by_name)
  ```

- [x] **`app/skills/router.py`**: Agregar `query` param a `build_request_more_tools_schema()`
  ```python
  def build_request_more_tools_schema(available_categories: list[str]) -> dict:
      # Add "query" alongside "categories"
      "properties": {
          "categories": { ... },  # existing — keep for backward compat
          "query": {
              "type": "string",
              "description": "Natural language description of what tools you need (e.g. 'save a note to a project'). Use this when you don't know the category name."
          },
          "reason": { ... },  # existing
      },
      "required": []  # Neither categories nor query required — one must be provided
  ```

- [x] **`app/skills/executor.py`**: Update handler de `request_more_tools` para semantic search
  ```python
  # In the meta-tool handler section:
  query = call.arguments.get("query", "")
  requested_cats = call.arguments.get("categories", [])

  new_tools: list[dict] = []

  # Path 1: Category-based (existing)
  if requested_cats:
      cat_tools = select_tools(requested_cats, all_tools_map, max_tools=max_tools)
      new_tools.extend(cat_tools)

  # Path 2: Semantic search (NEW)
  if query and repository:
      try:
          query_emb = await ollama_client.embed([query], model=embed_model)
          tool_names = await repository.search_similar_tools(query_emb[0], top_k=5)
          for name in tool_names:
              if name in all_tools_map and name not in existing_names:
                  new_tools.append(all_tools_map[name])
      except Exception:
          logger.warning("Semantic tool search failed", exc_info=True)
  ```
  - `execute_tool_loop` debe recibir `repository`, `ollama_client`, `embed_model` para el semantic path
  - Estos ya están disponibles en el call site de `_run_normal_flow()` — solo hay que pasarlos

- [x] **`app/main.py`**: Embed tool descriptions al startup (después de skill registry init)
  ```python
  # After skill registry + MCP initialization, before yield
  if vec_available and settings.semantic_search_enabled:
      from app.embeddings.indexer import embed_tool_descriptions
      from app.skills.executor import _get_cached_tools_map
      tools_map = _get_cached_tools_map(skill_registry, mcp_manager)
      await embed_tool_descriptions(tools_map, repository, ollama_client, settings.embedding_model)
  ```

- [x] **`app/skills/executor.py`**: Pasar dependencias al handler
  - `execute_tool_loop` ya tiene `skill_registry` y `mcp_manager`
  - Agregar params opcionales: `repository: Repository | None = None`, `ollama_client: OllamaClient | None = None`, `embed_model: str | None = None`
  - En call sites (`router.py`, `agent/loop.py`): pasar las dependencias existentes

- [ ] **Tests Fase B**: `tests/test_tool_rag.py`
  - Test `embed_tool_descriptions` crea embeddings para todas las tools
  - Test `search_similar_tools` retorna tools relevantes para un query
  - Test `request_more_tools` con `query` param encuentra tools semánticamente
  - Test `request_more_tools` con `categories` sigue funcionando (backward compat)
  - Test `request_more_tools` con ambos params combina resultados

### Phase 6: Project Notes en Contexto (Fase C)

- [x] **`app/context/conversation_context.py`**: Agregar field `project_notes: list[ProjectNote]`
  ```python
  @dataclass
  class ConversationContext:
      ...
      project_notes: list[ProjectNote] = field(default_factory=list)  # NEW
  ```

- [x] **`app/context/conversation_context.py`**: Fetch project notes en `build()`
  - Nueva subfunción `_get_project_notes(embedding)` — busca notas de proyectos activos
  ```python
  async def _get_project_notes(embedding: list[float] | None) -> list[ProjectNote]:
      if not settings or not vec_available or embedding is None:
          return []
      try:
          # Get active projects for this user
          projects = await repository.get_projects_with_progress(phone_number, status="active", limit=5)
          if not projects:
              return []
          all_notes: list[ProjectNote] = []
          for p in projects:
              notes = await repository.search_similar_project_notes(
                  p["id"], embedding, top_k=3
              )
              all_notes.extend(notes)
          return all_notes[:5]  # Cap total to avoid context bloat
      except Exception:
          logger.warning("Failed to fetch project notes for context", exc_info=True)
          return []
  ```
  - Agregar a `asyncio.gather` en Phase B junto con `_get_relevant_notes`

- [x] **`app/database/repository.py`**: Método `get_project_note_by_id` (si no existe ya como `get_project_note`)

- [x] **`app/webhook/router.py`**: Formatear e inyectar project notes
  ```python
  def _format_project_notes(project_notes: list[ProjectNote]) -> str | None:
      if not project_notes:
          return None
      lines = ["Project documents and notes:"]
      for n in project_notes:
          header = f"[{n.id}]"
          if n.title:
              header += f" {n.title}"
          lines.append(f"- {header}: {n.content[:500]}")
      return "\n".join(lines)
  ```

- [x] **`app/webhook/router.py`**: Agregar sección en `_build_context()`
  ```python
  def _build_context(..., project_notes: list[ProjectNote] | None = None):
      builder = ContextBuilder(system_prompt)
      builder.add_section("user_memories", _format_memories(memories))
      builder.add_section("active_projects", projects_summary)
      builder.add_section("project_notes", _format_project_notes(project_notes or []))  # NEW — between projects and notes
      builder.add_section("relevant_notes", _format_notes(relevant_notes))
      ...
  ```

- [ ] **Tests Fase C**: `tests/test_context_project_notes.py`
  - Test project notes se inyectan en contexto cuando hay proyectos activos
  - Test project notes vacías no generan sección
  - Test cap de 5 notas máximo
  - Test `_format_project_notes` incluye title cuando existe

### Phase 7: Documentación y Calidad

- [ ] `make lint` pasa
- [ ] `make typecheck` pasa
- [ ] `make test` pasa
- [ ] Actualizar `CLAUDE.md` con patrones nuevos (Tool RAG, project notes en contexto)
- [ ] Actualizar `AGENTS.md` si hay nuevos tools/módulos
- [ ] Crear `docs/features/41-project_notes_tool_rag.md`
- [ ] Crear `docs/testing/41-project_notes_tool_rag_testing.md`
- [ ] Actualizar `docs/exec-plans/README.md` con Plan 41

---

## Notas de Diseño

### Tool RAG — Por qué no reemplazar classify_intent

El router de dos etapas (classify → select) sigue siendo valioso:
- **classify_intent** es rápido (sin tools, `think=False`) y cubre el 90% de los casos
- **Tool RAG** es el escape hatch para el 10% donde el budget corta tools necesarias
- **Hybrid**: categoría primero (determinístico, predecible) → semantic fill para slots restantes

### Project Notes — Relación con Notes globales

Se mantienen separados intencionalmente:
- **Notes globales**: knowledge base del usuario, siempre en contexto, sin scope
- **Project notes**: documentos de trabajo, scoped a un proyecto, con title para navegación
- La sección `<project_notes>` en contexto es separada de `<relevant_notes>`

### Truncado — Política

- **En tools que devuelven contenido al LLM**: NO truncar (el LLM decide qué hacer)
- **En context injection**: 500 chars max por nota (protect token budget)
- **En search results**: 500 chars preview (suficiente para identificar el documento)
- **En embeddings**: 6000 chars (límite de nomic-embed-text, no cambia)

### Dependencias nuevas en executor

`execute_tool_loop` gana 3 params opcionales (`repository`, `ollama_client`, `embed_model`). Estos ya están disponibles en ambos call sites:
- `_run_normal_flow()` en `router.py` — tiene acceso a todos via `dependencies.py`
- `execute_worker()` en `agent/loop.py` — recibe `repository` y `ollama_client` del session
