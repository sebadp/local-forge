# Feature: Claude Code Refinements (Plan 59)

> **Version**: v1.0
> **Fecha de implementación**: 2026-04-02
> **Estado**: ✅ Implementada

---

## Qué hace?

Fixes de bugs del Plan 58 + mejoras de UX en las herramientas de código inspiradas en Claude Code.

---

## Cambios

### A. Plan 58 Fixes

1. **`glob_files`/`grep_code` workspace-aware**: Ahora usan `WorkspaceEngine.get_active_root()` en vez de hardcodear la raíz de wasap-assistant. Fallback al proyecto si no hay workspace activo.

2. **`_budget_compact` usa `CONTEXT_WINDOW_TOKENS` env var**: Ya no lee `Settings.model_fields` default. Lee directamente `os.getenv("CONTEXT_WINDOW_TOKENS", "32768")`.

3. **`grep_code` global result limiting**: Removido `--max-count` (era per-file). Ahora limita output lines globalmente después de la búsqueda.

4. **PRP 58 checkboxes marcados**.

### B. Read Consolidation

`read_source_file` ahora acepta `offset` (0-based) y `limit` opcionales:
- Sin params: lee el archivo completo (truncado a 12KB)
- Con params: lee un rango específico de líneas

`read_lines` se mantiene como alias backward-compatible que delega a `_read_file_impl()`.

### C. Edit Improvements

`apply_patch` ahora valida unicidad del search string:
- **1 match**: reemplazo normal (comportamiento anterior)
- **>1 match sin `replace_all`**: error con line numbers de cada ocurrencia
- **>1 match con `replace_all=true`**: reemplaza todas las ocurrencias

### D. Write Guard

`write_source_file` ahora requiere `overwrite=true` si el archivo ya existe. Sin el flag, retorna un warning con el número de líneas del archivo existente y sugerencias.

---

## Archivos clave

| Archivo | Cambio |
|---------|--------|
| `app/skills/tools/__init__.py` | `_get_project_root` usa WorkspaceEngine |
| `app/skills/tools/selfcode_tools.py` | `_read_file_impl`, apply_patch unicidad+replace_all, write guard |
| `app/skills/executor.py` | `_budget_compact` con param `context_window_tokens` |
| `app/skills/tools/grep_tools.py` | Global result limiting |
| `tests/test_edit_improvements.py` | 12 tests |

---

## Decisiones de diseño

| Decisión | Alternativa | Motivo |
|----------|-------------|--------|
| `read_lines` se mantiene como alias | Eliminar `read_lines` | Backward compat — prompts y memorias pueden referenciarlo |
| Unicidad falla con error, no silenciosamente | Reemplazar siempre la primera | El LLM necesita feedback para corregir — patches ambiguos causan bugs silenciosos |
| Write guard retorna warning, no bloquea | Bloquear completamente | El LLM puede reintentar con `overwrite=true` — no rompe el flujo |
| Workspace root via import de `workspace_tools._engine` | Pasar engine como param | Evita cambiar la firma de `register()` en glob/grep — minimal invasive |
