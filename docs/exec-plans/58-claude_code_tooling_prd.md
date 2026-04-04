# PRD: Claude Code Tooling & Code Mode (Plan 58)

## Objetivo y Contexto

### Problema

Después de implementar los Planes 53-57 (Auto-Dream, MicroCompact, Session Memory, CodeGen, Subagent Fork), LocalForge tiene la **arquitectura** de un coding agent completo: workspaces, templates, delivery, subagentes paralelos, plan mode interactivo. Sin embargo, faltan **primitivas de bajo nivel** y un **punto de entrada unificado** que conviertan esa arquitectura en una experiencia de coding fluida:

1. **No hay glob tool**: `list_source_files` hace un tree walk completo pero no acepta patrones como `**/*.py` o `**/test_*.ts`. El LLM no puede descubrir archivos por patrón sin leer todo el árbol.

2. **grep es básico**: `search_source_code` usa `grep` via subprocess sin líneas de contexto, sin globs de inclusión/exclusión, sin output estructurado. Para buscar una definición con contexto circundante hay que hacer grep + read_lines en dos pasos.

3. **No hay `/code` command**: El usuario debe usar `/agent` (general-purpose) para tareas de código. No hay un entry point que pre-configure el toolset de coding, un system prompt optimizado para código, ni un limit de iteraciones más alto.

4. **No hay categoría `code` unificada**: Las requests de coding se dispersan entre `selfcode` (introspección), `shell` (ejecución), y `workspace` (gestión de proyectos). El intent classifier no tiene ejemplos de coding y no puede agruparlas.

5. **Auto-compaction sin budget awareness**: MicroCompact (Plan 54) limpia resultados viejos por edad pero no monitorea el uso total del contexto. Con qwen3.5:9b (32K tokens), una sesión de coding con múltiples reads + greps puede exceder el contexto sin que el sistema reaccione.

6. **No hay git undo**: El agente puede commitear y pushear pero no puede deshacer cambios — no hay `git checkout -- <file>` ni `git revert HEAD` como tools.

7. **`read_source_file` trunca a 5KB**: Para un coding agent, 5KB es insuficiente para la mayoría de archivos. Claude Code maneja archivos de hasta 200KB.

### Inspiración: Claude Code

- **GlobTool**: `pathlib`-based file discovery por patrón. Uno de los tools más usados.
- **GrepTool**: ripgrep via subprocess con `--json` output, context lines, include/exclude globs.
- **Context budget**: Monitoreo activo del token budget; trigger de compaction cuando se acerca al límite.
- **`/code` entry point**: Claude Code inicia siempre en "code mode" con un sistema de tools y prompts optimizados para código.

## Alcance

### In Scope

#### A. Glob Tool
- Nuevo `glob_files(pattern, directory, exclude)` tool basado en `pathlib.Path.rglob()`
- Exclusiones default: `.git`, `node_modules`, `__pycache__`, `.venv`, `.pytest_cache`
- Límite de resultados (default 200) para evitar outputs masivos
- Opera relativo al workspace activo (reutiliza `WorkspaceEngine.get_active_root()`)

#### B. Grep Tool (ripgrep)
- Nuevo `grep_code(pattern, path, include, context_lines, max_results)` tool
- Usa `rg` (ripgrep) como subprocess preferido, con fallback a `grep -rn` si `rg` no está disponible
- Output estructurado: `file:line:content` con líneas de contexto opcionales
- Respeta exclusiones estándar (`.git`, `node_modules`, etc.)

#### C. `/code` Slash Command
- Entry point dedicado que crea una sesión agéntica con:
  - System prompt optimizado para coding (understand → plan → execute → test → deliver)
  - `max_iterations=20` (vs 15 del `/agent` estándar)
  - `pre_classified_categories=["code", "selfcode", "shell", "workspace"]`
  - Workspace context inyectado automáticamente

#### D. Categoría `code` en Intent Classifier
- Nueva categoría en `TOOL_CATEGORIES` que unifica: `glob_files`, `grep_code`, todas las selfcode tools, shell tools, git tools, `git_undo`, `git_stash`
- Agregar `"code"` a `WORKER_TOOL_SETS["coder"]`
- Ejemplos de clasificación para requests de coding

#### E. Budget-Based Auto-Compaction
- Antes de cada LLM call en `execute_tool_loop`, estimar tokens totales
- Si >80% de `context_window_tokens` (configurable, default 32768): aplicar `microcompact_messages()` agresivo + `_clear_old_tool_results(keep_last_n=1)`
- Reutilizar `estimate_tokens()` de `app/context/token_estimator.py`

#### F. Git Undo Tools
- `git_undo(scope, file_path)`: scope="file" → `git checkout -- <file>`, scope="commit" → `git revert HEAD --no-edit`
- `git_stash(action)`: action="save"/"pop"/"list"

#### G. `read_source_file` Size Increase
- Aumentar truncamiento de 5KB a 12KB
- Agregar warning: `[truncated at 12KB, use read_lines for specific sections]`

### Out of Scope

- **Tool concurrency classification** (`is_read_only` field): Plan 57 ya implementó parallel workers que dan el 80% del beneficio. Clasificar tools individuales dentro de `execute_tool_loop` tiene retorno marginal dado que Ollama serializa las LLM calls.
- **`web_tools.py` / `fetch_url`**: Plan 51 (`web_research`) y Plan 52 (`web_search` enhanced) ya cubren fetching web con LLM extraction. Un tercer fetch tool sería redundante.
- **Streaming execution**: Ollama retorna respuestas completas, no streaming tool_use blocks.
- **LSP integration**: Sobredimensionado para un asistente via WhatsApp.
- **Multi-agent swarm / coordinator**: `subagent.py` (Plan 57) ya cubre el caso de uso principal.

## Casos de Uso Críticos

1. **Coding session completa via `/code`**:
   ```
   Usuario: /code fix the login bug in my-api project
   ```
   → Switch workspace → glob_files para descubrir estructura → grep_code para encontrar el bug → read + patch → run_command (tests) → git commit → report

2. **Descubrimiento por patrón**:
   ```
   Usuario: busca todos los archivos .py que importan httpx
   ```
   → `grep_code("import httpx", ".", include="*.py")` retorna file:line:context en un solo step

3. **Búsqueda de definición con contexto**:
   ```
   Usuario: donde se define validate_email en el proyecto
   ```
   → `grep_code("def validate_email", ".", context_lines=5)` retorna definición + 5 líneas de contexto

4. **Context overflow prevention**:
   Agent session en iteración 8, el agente hizo 6 reads + 3 greps. Antes del LLM call #9, el budget check detecta 28000/32768 tokens (85%). Auto-compact dispara, limpia resultados viejos, libera ~8000 tokens.

5. **Rollback de error**:
   El agente modificó `app/main.py` incorrectamente. El siguiente step usa `git_undo(scope="file", file_path="app/main.py")` para restaurar el archivo antes de reintentar.

6. **Lectura de archivos medianos**:
   Un archivo de 300 líneas (~10KB) ahora se lee completo en vez de truncarse a la línea 100.

## Restricciones Arquitectónicas

- **Sin dependencias nuevas**: ripgrep se invoca via subprocess (ya disponible en la mayoría de entornos); fallback a `grep` si no está instalado. `pathlib` es stdlib.
- **`_validate_command` sigue siendo pura**: No se modifica. Los nuevos tools de git (undo, stash) son funciones async directas, no pasan por shell_tools.
- **Backward compatibility**: Si `rg` no está disponible, `grep_code` degrada a `grep -rn`. Si `context_window_tokens` no está seteado, se usa 32768. La categoría `code` se agrega sin modificar las categorías existentes.
- **Workspace-aware**: `glob_files` y `grep_code` operan relativo al workspace activo (via `WorkspaceEngine.get_active_root()`), consistente con `selfcode_tools`.
- **Security**: Los nuevos tools heredan el sandbox existente. `git_undo` con `scope="commit"` requiere HITL si está habilitado. `glob_files` y `grep_code` validan paths contra `_is_safe_path()`.
