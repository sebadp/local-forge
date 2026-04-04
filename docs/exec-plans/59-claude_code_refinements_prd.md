# PRD: Claude Code Refinements — Edit UX, Tool Consolidation & Plan 58 Fixes (Plan 59)

## Objetivo y Contexto

### Problema

Plan 58 implemento glob, grep, budget compaction, git undo y `/code` — las primitivas de bajo nivel para coding. Sin embargo, quedan gaps en dos categorias:

**A. Bugs y deuda tecnica del Plan 58:**

1. **`glob_files` y `grep_code` no usan el workspace activo.** El `_get_project_root()` en `__init__.py` retorna siempre la raiz de wasap-assistant (`Path(__file__).parents[3]`), ignorando `WorkspaceEngine.get_active_root()`. Si un usuario crea un proyecto con `/code` y luego busca archivos, busca en LocalForge en vez de en su proyecto.

2. **PRP checkboxes sin marcar.** Todos los `[ ]` del PRP 58 quedaron sin convertir a `[x]`, rompiendo la convencion del proyecto.

3. **`_budget_compact` lee el default del model field en vez del settings instanciado.** Usa `Settings.model_fields["context_window_tokens"].default` + `os.getenv()` como fallback, en vez del `settings` inyectado. Si alguien configura el setting via constructor (no env var), no lo detecta.

4. **`--max-count` en ripgrep es per-file, no global.** Con `--max-count 50` y 100 archivos con matches, podrias obtener hasta 5000 resultados. El `_MAX_OUTPUT_CHARS` actua como safety net, pero el output ya se genero innecesariamente.

**B. Mejoras de UX inspiradas en Claude Code que faltan:**

5. **`read_source_file` y `read_lines` son tools separados.** El LLM tiene que decidir cual usar. Claude Code tiene un solo `Read` con `offset` y `limit` opcionales. Menos tools = menos confusion para qwen3.5.

6. **`apply_patch` no valida unicidad del search string.** Si `search` aparece multiples veces, solo reemplaza la primera. El LLM no tiene forma de saber que su patch fue ambiguo. Claude Code falla con error explicito si `old_string` no es unico, forzando al LLM a usar mas contexto para desambiguar.

7. **`apply_patch` no tiene `replace_all`.** Para renombrar una variable en todo un archivo, el LLM tiene que llamar `apply_patch` N veces. Claude Code tiene `replace_all: bool` como parametro.

8. **`run_command` no tiene `run_in_background` como flag nativo.** Ya tiene `background: bool` — OK. Pero no hay mecanismo para leer el output de un background process despues (solo `manage_process` con action=status que muestra si esta running). Claude Code notifica automaticamente cuando un background command termina.

9. **No hay `Write` tool separado del `write_source_file`.** Actualmente `write_source_file` crea O sobreescribe. No distingue entre "crear nuevo" y "reemplazar existente". Un `Write` que solo crea (falla si existe) previene sobreescrituras accidentales.

### Inspiracion: Patrones de Claude Code

| Patron Claude Code | Estado en LocalForge | Accion |
|---|---|---|
| `Read(file, offset, limit)` unificado | Separado en `read_source_file` + `read_lines` | Unificar |
| `Edit(file, old_string, new_string, replace_all)` con validacion de unicidad | `apply_patch` sin validacion de unicidad ni `replace_all` | Mejorar |
| `Write(file, content)` que falla si no leyo antes | `write_source_file` crea o sobreescribe sin distincion | Agregar guard |
| `Bash(command, timeout, run_in_background)` | `run_command(command, timeout, background)` — funcional | Solo fix minor |
| `Glob/Grep` workspace-aware | Hardcodeado a project root | Fix |
| Budget compaction con settings inyectado | Lee model_fields default | Fix |

## Alcance

### In Scope

#### A. Fixes del Plan 58
- **A1.** `glob_files`/`grep_code` workspace-aware: inyectar `WorkspaceEngine` o phone number para resolver root correcto
- **A2.** `_budget_compact` usa settings inyectado (con fallback a env var)
- **A3.** `grep_code` limitar resultados globalmente (no solo per-file)
- **A4.** Marcar checkboxes del PRP 58

#### B. Tool Consolidation
- **B1.** Unificar `read_source_file` + `read_lines` en un solo `read_file(path, offset?, limit?)` — mantener los antiguos como aliases deprecados (para no romper prompts/memories que los referencien)
- **B2.** Agregar `replace_all: bool` a `apply_patch`
- **B3.** Agregar validacion de unicidad en `apply_patch`: si `search` aparece >1 vez y `replace_all=false`, retornar error con las posiciones donde aparece, para que el LLM amplíe el contexto

#### C. Write Guard
- **C1.** `write_source_file` ahora requiere confirmacion explicita si el archivo ya existe: retorna warning + requiere `overwrite=true` en la siguiente llamada. Previene sobreescrituras accidentales por el LLM.

### Out of Scope

- **Background process notification system**: Requeriria un event loop de polling o callback mechanism que excede el scope. El `manage_process(action=status)` existente es suficiente.
- **Jupyter/Notebook editing**: No relevante para WhatsApp.
- **Task tracking nativo para el agente**: `task_memory.py` + el plan mode de Plan 57 cubren el caso. Un sistema mas sofisticado (tipo `TaskCreate/TaskUpdate`) seria overengineering dado que las sesiones son cortas.
- **`Write` como tool separado de `write_source_file`**: Agregar un tool nuevo incrementa la confusion del LLM. Mejor mejorar el existente con un guard.
- **Eliminar `read_source_file`/`read_lines` completamente**: Romperían prompts existentes. Mantener como aliases.

## Casos de Uso Criticos

1. **Workspace-aware glob**: Usuario hace `/code fix the API` en workspace "my-api". `glob_files("**/*.py")` busca en `data/projects/my-api/`, no en wasap-assistant.

2. **Edit con unicidad**: LLM llama `apply_patch(path="app/main.py", search="return None", replace="return result")`. El archivo tiene 3 `return None`. El tool retorna: "Error: search string found 3 times (lines 45, 102, 198). Provide more context to make it unique." El LLM reintenta con mas contexto: `search="    if not valid:\n        return None"`.

3. **Rename variable**: LLM llama `apply_patch(path="utils.py", search="old_name", replace="new_name", replace_all=true)`. Las 12 ocurrencias se reemplazan en un solo call.

4. **Write guard**: LLM llama `write_source_file(path="app/config.py", content="...")`. El archivo ya existe. Retorna: "Warning: 'app/config.py' already exists (245 lines). Call with overwrite=true to confirm, or use apply_patch for targeted edits." Previene que el LLM sobreescriba un archivo de 500 lineas con uno de 10.

5. **Budget compaction con settings custom**: Usuario configura `CONTEXT_WINDOW_TOKENS=65536` (modelo mas grande). La compaction se dispara al 80% de 65536, no de 32768.

## Restricciones Arquitectonicas

- No se crean tools nuevos (solo se mejoran los existentes). Esto es critico: cada tool adicional reduce la precision del LLM para elegir el correcto.
- `read_source_file` y `read_lines` se mantienen registrados como aliases para backward compatibility. Internamente delegan al nuevo `_read_file_impl()`.
- `apply_patch` mantiene su firma actual — `replace_all` es un parametro nuevo opcional (default `false`).
- `write_source_file` mantiene su firma — `overwrite` es un parametro nuevo opcional (default `false`).
- No se modifica `_validate_command()` en shell_tools.
- No se agregan dependencias externas.
