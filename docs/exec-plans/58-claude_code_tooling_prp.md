# PRP: Claude Code Tooling & Code Mode (Plan 58)

## Archivos a Modificar

| Archivo | Cambio |
|---------|--------|
| `app/skills/tools/glob_tools.py` | **Nuevo** — `glob_files` tool (pathlib-based pattern search) |
| `app/skills/tools/grep_tools.py` | **Nuevo** — `grep_code` tool (ripgrep-based search with fallback) |
| `app/skills/tools/git_tools.py` | Agregar `git_undo` y `git_stash` tools |
| `app/skills/tools/selfcode_tools.py` | Aumentar truncamiento de `read_source_file` de 5KB a 12KB |
| `app/skills/tools/__init__.py` | Registrar glob y grep tools |
| `app/skills/router.py` | Agregar categoría `"code"` a `TOOL_CATEGORIES`, `WORKER_TOOL_SETS`, y classifier examples |
| `app/skills/executor.py` | Agregar budget-based compaction trigger antes de cada LLM call |
| `app/config.py` | Agregar `context_window_tokens: int = 32768` |
| `app/commands/builtins.py` | Agregar `/code` slash command |
| `tests/test_glob_tools.py` | **Nuevo** |
| `tests/test_grep_tools.py` | **Nuevo** |
| `tests/test_git_undo.py` | **Nuevo** |
| `tests/test_budget_compaction.py` | **Nuevo** |

## Análisis de Impacto en Tests Existentes

| Test existente | Afectado? | Razón |
|----------------|-----------|-------|
| `tests/test_shell_tools.py` | NO | No se toca `_validate_command`; nuevos git tools son funciones directas |
| `tests/test_security.py` | NO | PolicyEngine y AuditTrail no cambian |
| `tests/test_selfcode.py` | MÍNIMO | Solo cambia el límite de truncamiento (5000→12000), tests que assertean truncation text necesitan ajuste |
| `tests/test_microcompact.py` | NO | MicroCompact no cambia, solo se agrega un trigger adicional que lo llama |
| `tests/test_workspace_engine.py` | NO | WorkspaceEngine no cambia |
| `tests/test_meta_tools.py` | NO | `discover_tools` no cambia |
| `tests/test_subagent.py` | NO | Subagent no cambia |

---

## Fases de Implementación

### Phase 1: Glob Tool

- [x] **1.1** Crear `app/skills/tools/glob_tools.py`:
  ```python
  from __future__ import annotations

  import asyncio
  import logging
  from pathlib import Path

  from app.skills.registry import SkillRegistry

  logger = logging.getLogger(__name__)

  _DEFAULT_EXCLUDES = frozenset({
      ".git", "node_modules", "__pycache__", ".venv",
      ".pytest_cache", ".mypy_cache", ".ruff_cache",
      ".tox", "dist", "build", ".eggs",
  })

  _MAX_RESULTS = 200


  def register(registry: SkillRegistry, get_root: callable) -> None:
      """Register glob_files tool. get_root() returns the active workspace Path."""

      async def glob_files(
          pattern: str, directory: str = "", exclude: str = ""
      ) -> str:
          """Find files matching a glob pattern in the project."""
          def _glob() -> str:
              root = get_root()
              base = (root / directory).resolve() if directory else root

              if not base.is_relative_to(root):
                  return f"Access denied: '{directory}' is outside project root."
              if not base.exists():
                  return f"Directory not found: {directory or '.'}"

              user_excludes = {e.strip() for e in exclude.split(",") if e.strip()}
              all_excludes = _DEFAULT_EXCLUDES | user_excludes

              matches = []
              for p in base.rglob(pattern):
                  if any(ex in p.parts for ex in all_excludes):
                      continue
                  if p.is_file():
                      matches.append(str(p.relative_to(root)))
                  if len(matches) >= _MAX_RESULTS:
                      break

              if not matches:
                  return f"No files matching '{pattern}' in {directory or '.'}"

              header = f"Found {len(matches)} file(s) matching '{pattern}':"
              if len(matches) >= _MAX_RESULTS:
                  header += f" (limited to {_MAX_RESULTS})"
              return header + "\n" + "\n".join(matches)

          return await asyncio.to_thread(_glob)

      registry.register_tool(
          name="glob_files",
          description=(
              "Find files matching a glob pattern (e.g. '*.py', '**/test_*.ts', "
              "'**/*.md'). Returns file paths relative to project root."
          ),
          parameters={
              "type": "object",
              "properties": {
                  "pattern": {
                      "type": "string",
                      "description": "Glob pattern (e.g. '*.py', '**/test_*.ts', 'src/**/*.tsx')",
                  },
                  "directory": {
                      "type": "string",
                      "description": "Subdirectory to search in (relative to project root). Empty = entire project.",
                  },
                  "exclude": {
                      "type": "string",
                      "description": "Comma-separated directory names to exclude (added to defaults: .git, node_modules, __pycache__, .venv)",
                  },
              },
              "required": ["pattern"],
          },
          handler=glob_files,
          skill_name="code",
      )
  ```

- [x] **1.2** Tests en `tests/test_glob_tools.py`:
  - `test_glob_py_files` — crear temp dir con `.py` y `.js` files, verify solo `.py` returned
  - `test_glob_excludes_default` — verify `.git/` and `__pycache__/` dirs excluded
  - `test_glob_custom_exclude` — verify user-provided excludes work
  - `test_glob_max_results` — verify cap at `_MAX_RESULTS`
  - `test_glob_directory_scoped` — verify subdirectory restricts search
  - `test_glob_path_traversal` — verify `../` is blocked

### Phase 2: Grep Tool (ripgrep)

- [x] **2.1** Crear `app/skills/tools/grep_tools.py`:
  ```python
  from __future__ import annotations

  import asyncio
  import logging
  import shutil
  import subprocess
  from pathlib import Path

  from app.skills.registry import SkillRegistry

  logger = logging.getLogger(__name__)

  _MAX_RESULTS = 50
  _MAX_OUTPUT_CHARS = 8000
  _RG_AVAILABLE: bool | None = None


  def _check_rg() -> bool:
      global _RG_AVAILABLE
      if _RG_AVAILABLE is None:
          _RG_AVAILABLE = shutil.which("rg") is not None
      return _RG_AVAILABLE


  def register(registry: SkillRegistry, get_root: callable) -> None:
      """Register grep_code tool. get_root() returns the active workspace Path."""

      async def grep_code(
          pattern: str,
          path: str = "",
          include: str = "",
          context_lines: int = 0,
          max_results: int = 50,
      ) -> str:
          """Search for a regex pattern in project files using ripgrep (or grep fallback)."""
          def _grep() -> str:
              root = get_root()
              target = (root / path).resolve() if path else root

              if not target.is_relative_to(root):
                  return f"Access denied: '{path}' is outside project root."

              cap = min(max_results, _MAX_RESULTS)
              ctx = max(0, min(context_lines, 10))

              if _check_rg():
                  cmd = [
                      "rg", "--no-heading", "--line-number",
                      "--max-count", str(cap),
                      "--color", "never",
                  ]
                  if ctx > 0:
                      cmd += ["-C", str(ctx)]
                  if include:
                      for glob_pat in include.split(","):
                          g = glob_pat.strip()
                          if g:
                              cmd += ["--glob", g]
                  cmd += ["--", pattern, str(target)]
              else:
                  cmd = ["grep", "-rn", "--color=never"]
                  if ctx > 0:
                      cmd += [f"-C{ctx}"]
                  if include:
                      for glob_pat in include.split(","):
                          g = glob_pat.strip()
                          if g:
                              cmd += [f"--include={g}"]
                  cmd += ["--", pattern, str(target)]

              try:
                  result = subprocess.run(
                      cmd, capture_output=True, text=True, timeout=15,
                      cwd=str(root),
                  )
              except subprocess.TimeoutExpired:
                  return "Search timed out (15s limit)."
              except FileNotFoundError:
                  return "Error: neither rg nor grep found on this system."

              if result.returncode not in (0, 1):
                  return f"Search error: {result.stderr[:500]}"

              output = result.stdout.strip()
              if not output:
                  return f"No matches for pattern '{pattern}' in {path or '.'}"

              # Make paths relative to project root for readability
              root_str = str(root) + "/"
              output = output.replace(root_str, "")

              if len(output) > _MAX_OUTPUT_CHARS:
                  output = output[:_MAX_OUTPUT_CHARS] + f"\n... (truncated, limit {_MAX_OUTPUT_CHARS} chars)"

              return output

          return await asyncio.to_thread(_grep)

      registry.register_tool(
          name="grep_code",
          description=(
              "Search for a regex pattern in project files using ripgrep. "
              "Returns file:line:content matches. Supports context lines and "
              "file type filtering via include globs."
          ),
          parameters={
              "type": "object",
              "properties": {
                  "pattern": {
                      "type": "string",
                      "description": "Regex pattern to search for (e.g. 'def validate_email', 'import httpx')",
                  },
                  "path": {
                      "type": "string",
                      "description": "File or directory to search in (relative to project root). Empty = entire project.",
                  },
                  "include": {
                      "type": "string",
                      "description": "Comma-separated glob patterns to filter files (e.g. '*.py', '*.ts,*.tsx')",
                  },
                  "context_lines": {
                      "type": "integer",
                      "description": "Number of context lines before and after each match (0-10, default 0)",
                  },
                  "max_results": {
                      "type": "integer",
                      "description": "Maximum number of matches to return (default 50)",
                  },
              },
              "required": ["pattern"],
          },
          handler=grep_code,
          skill_name="code",
      )
  ```

- [x] **2.2** Tests en `tests/test_grep_tools.py`:
  - `test_grep_finds_pattern` — search for a known string, verify file:line output
  - `test_grep_with_context` — verify context_lines includes surrounding lines
  - `test_grep_include_filter` — verify include restricts to matching files
  - `test_grep_no_results` — verify clean message for non-matching pattern
  - `test_grep_path_traversal` — verify `../` is blocked
  - `test_grep_output_truncation` — verify large output is capped at `_MAX_OUTPUT_CHARS`

### Phase 3: `read_source_file` Size Increase

- [x] **3.1** En `app/skills/tools/selfcode_tools.py`, cambiar el truncamiento en `read_source_file`:
  ```python
  # Antes:
  if len(numbered) > 5000:
      numbered = numbered[:5000] + f"\n... (truncated, {len(lines)} total lines)"

  # Después:
  if len(numbered) > 12000:
      numbered = numbered[:12000] + (
          f"\n... (truncated at 12KB, {len(lines)} total lines. "
          "Use read_lines with offset+limit for specific sections.)"
      )
  ```

- [x] **3.2** Si existe algún test que assertee el texto de truncamiento "truncated, {N} total lines", actualizar el threshold en el test. Verificar en `tests/test_selfcode.py`.

### Phase 4: Git Undo Tools

- [x] **4.1** Agregar `git_undo` y `git_stash` en `app/skills/tools/git_tools.py`, dentro de `register()`:
  ```python
  async def git_undo(scope: str = "file", file_path: str = "") -> str:
      """Undo changes: restore a file or revert the last commit."""
      if scope == "file":
          if not file_path.strip():
              return "Error: file_path is required when scope='file'."
          clean = file_path.strip()
          if clean.startswith("-"):
              return f"Error: invalid file path '{clean}'."
          code, out, err = await asyncio.to_thread(
              _run_git, "checkout", "--", clean
          )
          if code != 0:
              return f"Error restoring '{clean}': {err}"
          return f"✅ Restored '{clean}' to last committed version."
      elif scope == "commit":
          code, out, err = await asyncio.to_thread(
              _run_git, "revert", "HEAD", "--no-edit"
          )
          if code != 0:
              return f"Error reverting last commit: {err}"
          return f"✅ Reverted last commit.\n{out}"
      else:
          return f"Error: unknown scope '{scope}'. Use 'file' or 'commit'."

  async def git_stash(action: str = "save", message: str = "") -> str:
      """Manage the git stash: save, pop, or list stashed changes."""
      if action == "save":
          args = ["stash", "push"]
          if message.strip():
              args += ["-m", message.strip()]
          code, out, err = await asyncio.to_thread(_run_git, *args)
          if code != 0:
              return f"Error stashing: {err}"
          return out or "✅ Changes stashed."
      elif action == "pop":
          code, out, err = await asyncio.to_thread(_run_git, "stash", "pop")
          if code != 0:
              return f"Error popping stash: {err}"
          return out or "✅ Stash applied and removed."
      elif action == "list":
          code, out, err = await asyncio.to_thread(_run_git, "stash", "list")
          if code != 0:
              return f"Error listing stash: {err}"
          return out or "(stash is empty)"
      else:
          return f"Error: unknown action '{action}'. Use 'save', 'pop', or 'list'."
  ```

- [x] **4.2** Registrar ambos tools al final de `register()`:
  ```python
  registry.register_tool(
      name="git_undo",
      description=(
          "Undo changes: restore a single file to its last committed state "
          "(scope='file') or revert the last commit (scope='commit')."
      ),
      parameters={
          "type": "object",
          "properties": {
              "scope": {
                  "type": "string",
                  "enum": ["file", "commit"],
                  "description": "'file' to restore a file, 'commit' to revert last commit",
              },
              "file_path": {
                  "type": "string",
                  "description": "Path to the file to restore (required when scope='file')",
              },
          },
          "required": ["scope"],
      },
      handler=git_undo,
      skill_name="git",
  )

  registry.register_tool(
      name="git_stash",
      description="Manage the git stash: save current changes, pop the latest stash, or list all stashes.",
      parameters={
          "type": "object",
          "properties": {
              "action": {
                  "type": "string",
                  "enum": ["save", "pop", "list"],
                  "description": "'save' to stash changes, 'pop' to restore, 'list' to show stashes",
              },
              "message": {
                  "type": "string",
                  "description": "Optional message for the stash (only used with action='save')",
              },
          },
          "required": ["action"],
      },
      handler=git_stash,
      skill_name="git",
  )
  ```

- [x] **4.3** Tests en `tests/test_git_undo.py`:
  - `test_git_undo_file` — modify a file, undo, verify restored
  - `test_git_undo_file_missing_path` — verify error message
  - `test_git_undo_commit` — make a commit, revert, verify HEAD changed
  - `test_git_stash_save_pop` — stash changes, verify clean, pop, verify restored
  - `test_git_stash_list_empty` — verify clean output when no stashes
  - `test_git_undo_flag_injection` — verify `file_path="-rf"` is blocked

### Phase 5: Registration, Routing & `/code` Command

- [x] **5.1** En `app/skills/tools/__init__.py`, agregar imports y llamadas de registro:
  ```python
  from app.skills.tools.glob_tools import register as register_glob
  from app.skills.tools.grep_tools import register as register_grep

  # After register_selfcode, before register_git:
  if settings is not None:
      from app.workspace.engine import WorkspaceEngine
      # Create a lazy getter for the active workspace root
      _engine: WorkspaceEngine | None = getattr(
          getattr(settings, '_app_state', None), 'workspace_engine', None
      )
      def _get_root():
          if _engine:
              return _engine.get_active_root("")
          return Path(__file__).resolve().parents[3]

      register_glob(registry, get_root=_get_root)
      register_grep(registry, get_root=_get_root)
  ```
  Nota: la forma exacta de obtener `get_root` depende de cómo se pasa el WorkspaceEngine al startup. Revisar `app/dependencies.py` para el patrón correcto.

- [x] **5.2** En `app/skills/router.py`, agregar categoría `"code"` a `TOOL_CATEGORIES`:
  ```python
  "code": [
      "glob_files",
      "grep_code",
      "read_source_file",
      "read_lines",
      "get_file_outline",
      "search_source_code",
      "list_source_files",
      "write_source_file",
      "apply_patch",
      "preview_patch",
      "run_command",
      "manage_process",
      "git_status",
      "git_diff",
      "git_create_branch",
      "git_commit",
      "git_push",
      "git_create_pr",
      "git_undo",
      "git_stash",
  ],
  ```

- [x] **5.3** En `WORKER_TOOL_SETS`, agregar `"code"` al coder:
  ```python
  "coder": ["code", "selfcode", "shell", "workspace"],
  ```

- [x] **5.4** En `_CLASSIFIER_PROMPT_TEMPLATE`, agregar ejemplos de clasificación para `code`:
  ```python
  # code
  '"fix the login bug" → code\n'
  '"refactorizá el módulo de auth" → code\n'
  '"crea un endpoint /users con paginación" → code\n'
  '"busca donde se define validate_email" → code\n'
  '"encuentra todos los archivos .py que importan httpx" → code\n'
  '"hacé un commit con los cambios" → code\n'
  '"deshacé los cambios en main.py" → code\n'
  ```

- [x] **5.5** Agregar `/code` command en `app/commands/builtins.py`:
  ```python
  async def cmd_code(args: str, context: CommandContext) -> str:
      """Start a coding agent session with optimized tools and higher iteration limit."""
      import asyncio
      from app.agent.loop import create_session, get_active_session, run_agent_session

      session = get_active_session(context.phone_number)
      if session:
          return "Ya hay una sesión activa. Usa /cancel antes de iniciar una nueva."

      objective = args.strip()
      if not objective:
          return "Uso: /code <objetivo>\nEjemplo: /code fix the login validation bug"

      new_session = create_session(context.phone_number, objective)
      new_session.max_iterations = 20  # Higher limit for coding tasks

      task = asyncio.create_task(
          run_agent_session(
              session=new_session,
              ollama_client=context.ollama_client,
              skill_registry=context.skill_registry,
              wa_client=context.wa_client,
              mcp_manager=context.mcp_manager,
              recorder=context.trace_recorder,
              repository=context.repository,
              pre_classified_categories=["code", "selfcode", "shell", "workspace"],
          )
      )
      _bg_agent_tasks.add(task)
      task.add_done_callback(_bg_agent_tasks.discard)

      return (
          f"💻 *Sesión de código iniciada*\n"
          f"_Objetivo:_ {objective}\n\n"
          "Herramientas activadas: glob, grep, read/write, shell, git.\n"
          "Te informo mi progreso."
      )
  ```

- [x] **5.6** Registrar el comando en el `CommandRegistry` (donde se registran los demás comandos builtin):
  ```python
  registry.register(CommandSpec(
      name="code",
      description="Start a coding agent session with optimized tools",
      handler=cmd_code,
  ))
  ```

- [x] **5.7** Verificar que `run_agent_session` acepta `pre_classified_categories` como argumento. Si no lo tiene, agregar el parámetro y pasarlo al `execute_tool_loop` dentro del agent loop.

### Phase 6: Budget-Based Auto-Compaction

- [x] **6.1** Agregar setting en `app/config.py`:
  ```python
  # Context window budget (Plan 58)
  context_window_tokens: int = 32768  # Model context window size for budget-based compaction
  ```

- [x] **6.2** En `app/skills/executor.py`, agregar compaction trigger dentro del `for iteration` loop, **antes** del LLM call (antes de la línea `response = await ollama_client.chat_with_tools(...)`):
  ```python
  # Budget-based compaction: if context is approaching the model's limit,
  # aggressively compact old tool results to prevent context overflow.
  from app.context.token_estimator import estimate_tokens

  total_text = "".join(m.content or "" for m in working_messages)
  estimated = estimate_tokens(total_text)
  budget = getattr(settings, "context_window_tokens", 32768) if settings else 32768

  if estimated > int(budget * 0.8):
      logger.warning(
          "Budget compaction triggered: %d tokens estimated (%.0f%% of %d budget)",
          estimated, (estimated / budget) * 100, budget,
      )
      working_messages = microcompact_messages(
          working_messages, max_age_rounds=1, current_round=iteration,
      )
      _clear_old_tool_results(working_messages, keep_last_n=1)
  ```
  Nota: esto requiere que `settings` esté disponible en `execute_tool_loop`. Si no se pasa actualmente, agregar `settings=None` como parámetro opcional y pasarlo desde los callers.

- [x] **6.3** Verificar que el import de `estimate_tokens` y la referencia a `settings` no creen imports circulares. Si `settings` no está disponible directamente, usar `os.getenv("CONTEXT_WINDOW_TOKENS", "32768")` como fallback.

### Phase 7: Documentación & Verificación

- [x] **7.1** `make test` pasa sin regresiones
- [x] **7.2** `make lint` pasa sin errores
- [x] **7.3** Crear `docs/features/58-claude_code_tooling.md` con walkthrough de las features
- [x] **7.4** Actualizar `AGENTS.md`:
  - Agregar `glob_tools.py`, `grep_tools.py` al mapa de archivos
  - Agregar categoría `"code"` a la documentación de TOOL_CATEGORIES
  - Agregar `/code` al listado de comandos slash
- [x] **7.5** Actualizar `CLAUDE.md`:
  - Agregar patrón de budget-based compaction
  - Documentar `rg` como dependencia opcional
- [x] **7.6** Actualizar `docs/exec-plans/README.md`:
  - Marcar Plans 53-57 como ✅ Completado
  - Agregar Plan 58

---

## Mapa de Dependencias entre Fases

```
Phase 1 (Glob)           ──┐
Phase 2 (Grep)           ──┤
Phase 3 (read_source_file)─┤──> Phase 5 (Registration & /code) ──> Phase 7 (Docs)
Phase 4 (Git Undo)       ──┤                                    ↗
Phase 6 (Budget Compact)  ─┘────────────────────────────────────
```

- Phases 1-4 y 6 son independientes entre sí — pueden implementarse en cualquier orden o en paralelo.
- Phase 5 depende de 1-4 (los tools deben existir antes de registrarlos y rutearlos).
- Phase 6 es independiente de 5 (modifica executor.py, no router.py).
- Phase 7 depende de todo lo anterior.

---

## Invariantes — Lo que NO Cambia

- Firma de `_validate_command()` en `shell_tools.py` — intacta.
- Estructura de `ToolDefinition` y `SkillRegistry.register_tool()` — intacta.
- Comportamiento de `microcompact_messages()` — intacto (se llama adicionalmente, no se modifica).
- Comportamiento de `_clear_old_tool_results()` — intacto (se llama adicionalmente).
- `execute_tool_loop()` sin `settings` sigue funcionando (budget check se skipea con fallback).
- `discover_tools` — sigue funcionando, ahora descubre también los nuevos tools.
- Todas las categorías existentes en `TOOL_CATEGORIES` — intactas.
- `/agent` command — sigue funcionando exactamente igual.
