# Testing Manual: Claude Code Refinements (Plan 59)

> **Feature documentada**: [`docs/features/59-claude_code_refinements.md`](../features/59-claude_code_refinements.md)
> **Requisitos previos**: Container corriendo, Ollama disponible, workspace configurado para tests de workspace-aware tools.

---

## Casos de prueba: Workspace-Aware glob/grep

| Mensaje / Acción | Resultado esperado |
|---|---|
| Crear workspace, activarlo, luego `buscá archivos .py` | `glob_files` busca dentro del workspace activo, no del proyecto principal |
| Sin workspace activo, `buscá archivos .py` | `glob_files` usa la raíz del proyecto como fallback |
| `grep_code` en workspace activo | Busca dentro del workspace root |

---

## Casos de prueba: Read Consolidation

| Mensaje / Acción | Resultado esperado |
|---|---|
| LLM usa `read_source_file("main.py")` sin params | Lee archivo completo (truncado a 12KB) |
| LLM usa `read_source_file("main.py", offset=10, limit=20)` | Lee líneas 10-30 del archivo |
| LLM usa `read_lines("main.py", start=10, end=30)` | Funciona como alias, delega a `_read_file_impl()` |

---

## Casos de prueba: apply_patch Unicidad

| Escenario | Resultado esperado |
|---|---|
| `apply_patch` con search string que aparece 1 vez | Reemplazo normal (comportamiento anterior) |
| `apply_patch` con search string que aparece 3 veces, sin `replace_all` | **Error** con line numbers de cada ocurrencia: "Found 3 matches at lines 5, 22, 41" |
| `apply_patch` con search string repetido + `replace_all=true` | Reemplaza todas las ocurrencias |
| `apply_patch` con search string que no aparece | Error: "No match found" |

### Verificar

```bash
docker compose logs -f localforge 2>&1 | grep -i "apply_patch\|ambiguous\|matches"
```

---

## Casos de prueba: Write Guard

| Escenario | Resultado esperado |
|---|---|
| `write_source_file("new_file.py", content)` — archivo no existe | Se crea normalmente |
| `write_source_file("existing.py", content)` — sin `overwrite` | **Warning**: "File exists (N lines). Use overwrite=true or apply_patch" |
| `write_source_file("existing.py", content, overwrite=true)` | Se sobrescribe normalmente |

### Verificar

```bash
docker compose logs -f localforge 2>&1 | grep -i "write_source_file\|overwrite\|write guard"
```

---

## Edge cases y validaciones

| Escenario | Resultado esperado |
|---|---|
| `apply_patch` con `replace_all` y 0 matches | Error: "No match found" |
| `read_source_file` con offset mayor que el archivo | Retorna contenido vacío o error informativo |
| Workspace eliminado entre glob calls | Fallback a project root |
| `_budget_compact` con `CONTEXT_WINDOW_TOKENS` env var | Lee valor de env, no de Settings default |

---

## Tests automatizados

```bash
.venv/bin/python -m pytest tests/test_edit_improvements.py -v
# 12 tests: unicidad, replace_all, write guard, read consolidation
```

---

## Troubleshooting

| Problema | Causa probable | Solución |
|---|---|---|
| LLM repite `apply_patch` con string ambiguo | No leyó error con line numbers | El error incluye sugerencias — verificar que se pasa correctamente |
| `write_source_file` siempre falla | Archivo existe y LLM no pasa `overwrite=true` | Warning incluye instrucciones — LLM debería reintentar |
| glob/grep buscan en proyecto en vez de workspace | Workspace no activo | Verificar con `get_workspace_info` |
