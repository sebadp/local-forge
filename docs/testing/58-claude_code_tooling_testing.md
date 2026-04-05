# Testing Manual: Claude Code Tooling & Code Mode (Plan 58)

> **Feature documentada**: [`docs/features/58-claude_code_tooling.md`](../features/58-claude_code_tooling.md)
> **Requisitos previos**: Container corriendo, Ollama disponible.

---

## Casos de prueba: glob_files

| Mensaje / Acción | Resultado esperado |
|---|---|
| `buscá todos los archivos .py en app/` (en agent/code mode) | LLM usa `glob_files(pattern="**/*.py", directory="app/")`. Lista de archivos retornada |
| Glob con patrón específico: `archivos de test` | `glob_files(pattern="test_*.py")` retorna archivos de test |
| Glob en workspace activo | Busca dentro del workspace root, no del proyecto principal |

### Verificar en logs

```bash
docker compose logs -f localforge 2>&1 | grep -i "glob_files"
```

---

## Casos de prueba: grep_code

| Mensaje / Acción | Resultado esperado |
|---|---|
| `buscá dónde se define validate_email` | `grep_code(pattern="def validate_email", include="*.py")`. Resultado con archivo y líneas |
| Búsqueda con contexto | `context_lines=2` muestra líneas alrededor del match |
| Resultado largo (>8000 chars) | Output truncado a 8000 chars, 50 matches max |

### Verificar

```bash
docker compose logs -f localforge 2>&1 | grep -i "grep_code"
```

---

## Casos de prueba: /code command

| Mensaje / Acción | Resultado esperado |
|---|---|
| `/code fix the login bug` | Sesión de agent con categorías pre-clasificadas (code, selfcode, shell, workspace), 20 iteraciones max |
| `/code` sin argumento | Sesión de code mode sin objetivo específico |

---

## Casos de prueba: git_undo & git_stash

| Mensaje / Acción | Resultado esperado |
|---|---|
| `deshacé los cambios en main.py` | `git_undo(scope="file", file_path="main.py")` — `git checkout -- main.py` |
| `revertí el último commit` | `git_undo(scope="commit")` — `git revert HEAD --no-edit` |
| `guardá los cambios actuales` | `git_stash(action="save", message="...")` |
| `restaurá el stash` | `git_stash(action="pop")` |

---

## Casos de prueba: Budget-Based Compaction

| Escenario | Resultado esperado |
|---|---|
| Sesión larga con muchas tool calls (>80% de CONTEXT_WINDOW_TOKENS) | `_budget_compact` se activa: microcompact + clear old tool results |
| Sesión corta con pocas tool calls | No se activa compaction |

### Verificar

```bash
docker compose logs -f localforge 2>&1 | grep -i "budget_compact\|context.*exceed"
```

---

## Edge cases y validaciones

| Escenario | Resultado esperado |
|---|---|
| `glob_files` con path traversal (`../../etc/passwd`) | Rechazado: `is_relative_to(root)` bloquea |
| `grep_code` con path traversal | Rechazado igualmente |
| `git_undo` con path que empieza con `-` | Rechazado: flag injection bloqueado |
| `read_source_file` archivo >12KB | Truncado a 12KB (antes era 5KB) |
| `CONTEXT_WINDOW_TOKENS` muy bajo (ej: 4096) | Compaction se activa muy temprano — funcional pero agresivo |

---

## Tests automatizados

```bash
.venv/bin/python -m pytest tests/test_glob_tools.py tests/test_grep_tools.py tests/test_git_undo.py tests/test_budget_compaction.py -v
# 24 tests: glob, grep, git undo/stash, budget compaction
```

---

## Troubleshooting

| Problema | Causa probable | Solución |
|---|---|---|
| `glob_files` no encuentra archivos | Directorio incorrecto o pattern erróneo | Verificar root del workspace vs proyecto |
| `grep_code` lento | `rg` no instalado, fallback a `grep -rn` | Instalar ripgrep en container |
| Budget compaction nunca se activa | `CONTEXT_WINDOW_TOKENS` muy alto | Default 32768 — Ollama models suelen tener 32K |
| `/code` no activa code mode | Command no registrado | Verificar `app/commands/builtins.py` |

---

## Variables relevantes para testing

| Variable (`.env`) | Valor de test | Efecto |
|---|---|---|
| `CONTEXT_WINDOW_TOKENS` | `32768` | Ventana de contexto para budget compaction |
