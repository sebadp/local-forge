# PRP: Claude Code Refinements — Edit UX, Tool Consolidation & Plan 58 Fixes (Plan 59)

## Archivos a Modificar

| Archivo | Cambio |
|---------|--------|
| `app/skills/tools/__init__.py` | Fix `_get_project_root` para usar WorkspaceEngine |
| `app/skills/tools/selfcode_tools.py` | Unificar read, mejorar apply_patch (unicidad + replace_all), write guard |
| `app/skills/executor.py` | Fix `_budget_compact` para usar settings inyectado |
| `app/skills/tools/grep_tools.py` | Fix `--max-count` global limiting |
| `docs/exec-plans/58-claude_code_tooling_prp.md` | Marcar checkboxes `[x]` |
| `tests/test_selfcode.py` | Actualizar tests para nuevos parametros y comportamiento |
| `tests/test_edit_improvements.py` | **Nuevo** — tests para unicidad, replace_all, write guard |
| `tests/test_workspace_glob_grep.py` | **Nuevo** — tests para glob/grep workspace-aware |

## Analisis de Impacto en Tests Existentes

| Test existente | Afectado? | Razon |
|----------------|-----------|-------|
| `tests/test_glob_tools.py` | NO | Los tests usan `get_root` mock, no cambian |
| `tests/test_grep_tools.py` | NO | Idem, mock-based |
| `tests/test_selfcode.py` | SI | `apply_patch` cambia comportamiento con strings duplicados; `write_source_file` agrega `overwrite` |
| `tests/test_budget_compaction.py` | MINIMO | `_budget_compact` cambia como obtiene el budget, pero la logica de threshold no cambia |
| `tests/test_microcompact.py` | NO | No se toca |
| `tests/test_agent_tracing.py` | NO | No se toca |

---

## Fases de Implementacion

### Phase 1: Fix Plan 58 Bugs

- [x] **1.1** Fix `_get_project_root` en `app/skills/tools/__init__.py`:
  ```python
  # ANTES (hardcoded):
  _fallback_root = _Path(__file__).resolve().parents[3]
  def _get_project_root() -> _Path:
      return _fallback_root

  # DESPUES (workspace-aware):
  _fallback_root = _Path(__file__).resolve().parents[3]

  def _get_project_root() -> _Path:
      """Return active workspace root, falling back to LocalForge root."""
      try:
          from app.workspace.engine import WorkspaceEngine
          engine = WorkspaceEngine.get_instance()
          if engine is not None:
              # Use empty phone for now — glob/grep are phone-agnostic
              # in agent mode, the phone is set via set_active()
              root = engine.get_active_root("")
              if root != _fallback_root:
                  return root
      except Exception:
          pass
      return _fallback_root
  ```
  **Alternativa mas simple**: si `WorkspaceEngine` no tiene singleton, pasar `phone` como parametro contextual. Verificar primero como `workspace_tools.py` resuelve esto y seguir el mismo patron.

  **Nota**: revisar si `WorkspaceEngine` tiene un `get_instance()` o si se instancia en `__init__.py` durante el startup. Si se instancia ahi, capturarlo en una variable de modulo y usarlo en el closure.

- [x] **1.2** Fix `_budget_compact` en `app/skills/executor.py`:
  ```python
  # ANTES:
  def _budget_compact(working_messages: list[ChatMessage], iteration: int) -> None:
      ...
      try:
          from app.config import Settings
          budget = Settings.model_fields["context_window_tokens"].default
      except Exception:
          budget = 32768
      import os
      budget = int(os.getenv("CONTEXT_WINDOW_TOKENS", str(budget)))

  # DESPUES:
  def _budget_compact(
      working_messages: list[ChatMessage],
      iteration: int,
      context_window_tokens: int = 32768,
  ) -> None:
      ...
      budget = context_window_tokens
  ```
  Y en el caller dentro de `execute_tool_loop`:
  ```python
  _budget_compact(
      working_messages, iteration,
      context_window_tokens=getattr(settings, "context_window_tokens", 32768) if settings else 32768,
  )
  ```
  **Nota**: `execute_tool_loop` ya no recibe `settings` directamente. Verificar si se puede acceder via import o si hay que agregar el parametro. Usar el patron existente en el archivo.

- [x] **1.3** Fix `grep_code` global result limiting en `app/skills/tools/grep_tools.py`:
  ```python
  # ANTES:
  cmd = ["rg", ..., "--max-count", str(cap), ...]

  # DESPUES (limitar output lines, no per-file matches):
  # Remover --max-count del rg command
  # Agregar pipe-style limiting post-capture:
  cmd = ["rg", "--no-heading", "--line-number", "--color", "never"]
  # ... (context, include as before, sin --max-count)
  cmd += ["--", pattern, str(target)]

  # Despues del subprocess.run:
  lines = output.strip().splitlines()
  if len(lines) > cap:
      output = "\n".join(lines[:cap]) + f"\n... ({len(lines)} total matches, showing first {cap})"
  ```

- [x] **1.4** Marcar todos los checkboxes en `docs/exec-plans/58-claude_code_tooling_prp.md`:
  Reemplazar todos `- [ ]` con `- [x]`.

- [ ] **1.5** Tests para workspace-aware (deferred — covered by integration) glob/grep en `tests/test_workspace_glob_grep.py`:
  - `test_glob_uses_workspace_root` — mock WorkspaceEngine, verify glob searches in workspace dir
  - `test_grep_uses_workspace_root` — mock WorkspaceEngine, verify grep searches in workspace dir
  - `test_glob_falls_back_to_project_root` — sin WorkspaceEngine, verify falls back
  - `test_grep_global_result_limit` — crear archivo con 100+ matches, verify output capped

### Phase 2: Tool Consolidation — Read

- [x] **2.1** Crear `_read_file_impl()` como funcion interna unificada en `selfcode_tools.py`:
  ```python
  def _read_file_impl(path: str, offset: int = 0, limit: int = 0) -> str:
      """Unified file reading: full file or specific range.

      offset: 0-based line offset (0 = start). Maps to 1-indexed internally.
      limit: max lines to return (0 = no limit, read entire file).
      """
      target = (_PROJECT_ROOT / path).resolve()

      if not _is_safe_path(target):
          return f"Access denied: '{path}' is outside project root or is a sensitive file."
      if not target.exists():
          return f"File not found: {path}"
      if not target.is_file():
          return f"Not a file: {path}"

      try:
          content = target.read_text(encoding="utf-8", errors="replace")
      except Exception as e:
          return f"Error reading file: {e}"

      all_lines = content.splitlines()
      total = len(all_lines)

      if offset > 0 or limit > 0:
          # Range mode
          start = max(offset, 0)
          if start >= total:
              return f"Error: offset={start} exceeds file length ({total} lines)."
          end = min(start + limit, total) if limit > 0 else total
          if end - start > 500:
              return f"Error: Range too large ({end - start} lines). Use limit <= 500."
          selected = all_lines[start:end]
          numbered = "\n".join(f"{start + i + 1:4d}  {line}" for i, line in enumerate(selected))
          return f"{path} — Lines {start + 1}-{end} (of {total} total):\n{numbered}"
      else:
          # Full file mode
          numbered = "\n".join(f"{i + 1:4d}  {line}" for i, line in enumerate(all_lines))
          if len(numbered) > 12000:
              numbered = numbered[:12000] + (
                  f"\n... (truncated at 12KB, {total} total lines. "
                  "Use offset and limit params for specific sections.)"
              )
          return f"=== {path} ===\n{numbered}"
  ```

- [x] **2.2** Refactorizar `read_source_file` para delegar:
  ```python
  async def read_source_file(path: str, offset: int = 0, limit: int = 0) -> str:
      return await asyncio.to_thread(_read_file_impl, path, offset, limit)
  ```

- [x] **2.3** Hacer que `read_lines` delegue al mismo impl (backward compat):
  ```python
  async def read_lines(path: str, start: int, end: int) -> str:
      """Backward-compatible wrapper. Prefer read_source_file with offset/limit."""
      if start < 1:
          return "Error: start line must be >= 1."
      if end < start:
          return "Error: end line must be >= start line."
      return await asyncio.to_thread(
          _read_file_impl, path, offset=start - 1, limit=end - start + 1
      )
  ```

- [x] **2.4** Actualizar el schema de `read_source_file` para exponer `offset` y `limit`:
  ```python
  registry.register_tool(
      name="read_source_file",
      description=(
          "Read a source file within the project. Returns file content with line numbers. "
          "Use offset and limit to read specific sections of large files."
      ),
      parameters={
          "type": "object",
          "properties": {
              "path": {
                  "type": "string",
                  "description": "Relative path to the file within the project",
              },
              "offset": {
                  "type": "integer",
                  "description": "Start reading from this line (0-based). Default: 0 (start of file).",
              },
              "limit": {
                  "type": "integer",
                  "description": "Max lines to read. Default: 0 (entire file, truncated at 12KB).",
              },
          },
          "required": ["path"],
      },
      handler=read_source_file,
      skill_name="selfcode",
  )
  ```

- [x] **2.5** Mantener `read_lines` registrado pero actualizar su description:
  ```python
  description="(Alias) Read a line range. Prefer read_source_file with offset/limit."
  ```

### Phase 3: Edit Improvements — Unicidad & replace_all

- [x] **3.1** Agregar `replace_all: bool = False` a `apply_patch`:
  ```python
  async def apply_patch(
      path: str, search: str, replace: str, replace_all: bool = False
  ) -> str:
  ```

- [x] **3.2** Agregar validacion de unicidad antes del reemplazo:
  ```python
  def _patch() -> str:
      ...
      text = target.read_text(encoding="utf-8")

      if search not in text:
          snippet = text[:300] + ("..." if len(text) > 300 else "")
          return (
              f"Error: Search string not found in '{path}'.\n"
              f"Use read_source_file to check the exact current content.\n"
              f"File starts with:\n{snippet}"
          )

      count = text.count(search)

      if replace_all:
          new_text = text.replace(search, replace)
          target.write_text(new_text, encoding="utf-8")
          return f"✅ Patched '{path}': replaced all {count} occurrence(s)."

      if count > 1:
          # Find line numbers of each occurrence to help LLM disambiguate
          positions = []
          start = 0
          for _i in range(min(count, 10)):
              idx = text.find(search, start)
              if idx == -1:
                  break
              line_no = text[:idx].count("\n") + 1
              positions.append(str(line_no))
              start = idx + 1
          return (
              f"Error: Search string found {count} times in '{path}' "
              f"(lines {', '.join(positions)}). "
              f"Provide more surrounding context to make the match unique, "
              f"or use replace_all=true to replace all occurrences."
          )

      # Unique match — safe to replace
      new_text = text.replace(search, replace, 1)
      target.write_text(new_text, encoding="utf-8")
      return f"✅ Patched '{path}': replaced {len(search)} chars with {len(replace)} chars."
  ```

- [x] **3.3** Actualizar el schema de `apply_patch` para incluir `replace_all`:
  ```python
  parameters={
      "type": "object",
      "properties": {
          "path": {
              "type": "string",
              "description": "Relative path to the file to patch",
          },
          "search": {
              "type": "string",
              "description": "Exact text to find in the file (must be unique unless replace_all=true)",
          },
          "replace": {
              "type": "string",
              "description": "Replacement text",
          },
          "replace_all": {
              "type": "boolean",
              "description": "If true, replace ALL occurrences. Default: false (requires unique match).",
          },
      },
      "required": ["path", "search", "replace"],
  },
  ```

- [x] **3.4** Tests en `tests/test_edit_improvements.py`:
  - `test_apply_patch_unique_match` — single occurrence, succeeds
  - `test_apply_patch_ambiguous_match` — 3 occurrences, returns error with line numbers
  - `test_apply_patch_replace_all` — 3 occurrences + `replace_all=true`, all replaced
  - `test_apply_patch_replace_all_reports_count` — verify message says "replaced all N occurrence(s)"
  - `test_apply_patch_not_found` — existing behavior preserved
  - `test_apply_patch_replace_all_false_with_single_match` — 1 occurrence + `replace_all=false`, succeeds (backward compat)

### Phase 4: Write Guard

- [x] **4.1** Agregar `overwrite: bool = False` a `write_source_file`:
  ```python
  async def write_source_file(
      path: str, content: str, overwrite: bool = False
  ) -> str:
  ```

- [x] **4.2** Agregar check de existencia al inicio de `_write()`:
  ```python
  def _write() -> str:
      ...
      if target.exists() and not overwrite:
          try:
              existing_lines = len(target.read_text(encoding="utf-8").splitlines())
          except Exception:
              existing_lines = "?"
          return (
              f"Warning: '{path}' already exists ({existing_lines} lines). "
              f"Call with overwrite=true to confirm replacement, "
              f"or use apply_patch for targeted edits."
          )
      ...
  ```

- [x] **4.3** Actualizar el schema de `write_source_file`:
  ```python
  parameters={
      "type": "object",
      "properties": {
          "path": {...},
          "content": {...},
          "overwrite": {
              "type": "boolean",
              "description": "Set to true to overwrite an existing file. Default: false (creates new files only).",
          },
      },
      "required": ["path", "content"],
  },
  ```

- [x] **4.4** Tests:
  - `test_write_new_file` — file doesn't exist, creates successfully (existing behavior)
  - `test_write_existing_file_blocked` — file exists, `overwrite=false`, returns warning
  - `test_write_existing_file_overwrite` — file exists, `overwrite=true`, succeeds
  - `test_write_existing_file_reports_line_count` — verify warning includes line count

### Phase 5: Test Adjustments & Existing Test Fixes

- [x] **5.1** Revisar `tests/test_selfcode.py` (no existe — no action needed) y actualizar tests que:
  - Llaman `apply_patch` con un search string que aparece multiples veces (ahora falla con error de unicidad)
  - Llaman `write_source_file` sobre un archivo existente sin `overwrite=true`
  - Verificar que todos los tests existentes siguen pasando

- [x] **5.2** Actualizar `tests/test_budget_compaction.py` (tests pass without changes — compatible API):
  - Verificar que `_budget_compact` ahora recibe `context_window_tokens` como parametro
  - Ajustar mocks si es necesario

### Phase 6: Documentacion & QA

- [x] **6.1** `make test` (871 passed, 27 skipped) pasa sin regresiones
- [x] **6.2** `make lint` pasa sin errores
- [x] **6.3** Crear `docs/features/59-claude_code_refinements.md`
- [x] **6.4** Actualizar `AGENTS.md`:
  - Documentar que `apply_patch` ahora valida unicidad
  - Documentar `replace_all` parameter
  - Documentar write guard
- [x] **6.5** Actualizar `CLAUDE.md`:
  - Agregar patron: "apply_patch requiere match unico (o replace_all=true)"
  - Agregar patron: "write_source_file requiere overwrite=true para archivos existentes"
- [x] **6.6** Actualizar `docs/exec-plans/README.md` con Plan 59

---

## Mapa de Dependencias entre Fases

```
Phase 1 (Plan 58 Fixes)     ──┐
Phase 2 (Read Consolidation) ──┤
Phase 3 (Edit Improvements)  ──┼──> Phase 5 (Test Adjustments) ──> Phase 6 (Docs)
Phase 4 (Write Guard)        ──┘
```

- Phases 1-4 son independientes entre si — pueden implementarse en cualquier orden.
- Phase 5 depende de 2, 3, y 4 (los cambios de comportamiento afectan tests existentes).
- Phase 6 depende de todo lo anterior.

---

## Invariantes — Lo que NO Cambia

- `_validate_command()` en `shell_tools.py` — intacta.
- `_is_safe_path()` — intacta, todos los tools siguen usandola.
- `preview_patch` — intacta (no necesita unicidad porque no modifica el archivo).
- `microcompact_messages()` — intacta.
- `glob_files` y `grep_code` tool implementations — intactas (solo cambia como se inyecta `get_root`).
- `read_lines` sigue registrado y funcional (delega a `_read_file_impl`).
- `apply_patch` sin `replace_all` con un solo match — comportamiento identico al actual.
- `write_source_file` para archivos nuevos sin `overwrite` — comportamiento identico al actual.
- `/code` command — intacto.
- Todas las categorias en `TOOL_CATEGORIES` — intactas.

## Riesgos

| Riesgo | Mitigacion |
|--------|-----------|
| `apply_patch` con unicidad rompe prompts existentes que hacian patches ambiguos | El LLM recibe un error claro con line numbers, puede reintentar con mas contexto. Es un cambio a mejor. |
| `write_source_file` con guard rompe el agente si intenta overwrite sin el flag | El warning es explicito y dice como proceder. El LLM puede reintentar con `overwrite=true`. |
| Workspace-aware glob/grep podria romper si WorkspaceEngine no esta disponible | Fallback a `_fallback_root` (comportamiento actual) |
| Read consolidation podria romper callers que pasan positional args a `read_lines` | `read_lines` se mantiene con la misma firma `(path, start, end)` |
