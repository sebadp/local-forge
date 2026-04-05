# Testing Manual: CodeGen Experience — Project Generation

> **Feature documentada**: [`docs/features/56-codegen_experience.md`](../features/56-codegen_experience.md)
> **Requisitos previos**: Container corriendo, Ollama disponible, `PROJECTS_ROOT` configurado en `.env`.

---

## Verificar que la feature está activa

```bash
# Verificar que PROJECTS_ROOT está configurado
docker compose logs -f localforge | grep -i "projects_root\|workspace"
```

Confirmar: `PROJECTS_ROOT` apunta a un directorio válido (ej: `data/projects`).

---

## Casos de prueba: Workspace Engine

| Mensaje / Acción | Resultado esperado |
|---|---|
| `/agent creame un workspace llamado mi-proyecto` | Workspace creado en `PROJECTS_ROOT/mi-proyecto/`, git init ejecutado |
| Enviar `listá mis workspaces` | Lista de workspaces existentes con estado (activo/inactivo) |
| Enviar `cambiá al workspace mi-proyecto` | Workspace activo cambia. Tools de código operan dentro del nuevo root |
| Crear workspace con nombre inválido (ej: `../hack` o `mi proyecto con espacios`) | Error de validación: nombre unsafe rechazado |

---

## Casos de prueba: Templates & Scaffold

| Mensaje / Acción | Resultado esperado |
|---|---|
| `/agent creame una API en FastAPI para tareas` | Scaffold usa template `python-fastapi`. Archivos: main.py, models.py, requirements.txt, tests/, README.md |
| `/agent creame una landing page` | Scaffold usa template `html-static`. Archivos: index.html, styles.css, script.js, README.md |
| `qué templates hay disponibles?` | Lista: html-static, python-fastapi, react-vite, nextjs |

### Verificar archivos creados

```bash
# Listar workspaces
ls -la data/projects/

# Verificar archivos de un scaffold
ls -la data/projects/<nombre>/
```

---

## Casos de prueba: Delivery

| Mensaje / Acción | Resultado esperado |
|---|---|
| `/agent creame una landing y pusheala a GitHub` | Proyecto creado, commit, `gh repo create`, push. URL de GitHub retornada |
| `/agent creame un proyecto y dame el ZIP` | Proyecto creado, ZIP generado excluyendo node_modules/.git, path del ZIP retornado |
| `dame un preview de mi proyecto` | Servidor HTTP temporal en thread daemon, URL localhost retornada |

### Prerequisitos para GitHub delivery

```bash
# Verificar que gh CLI está disponible y autenticado
docker compose exec localforge gh auth status
# Verificar GITHUB_TOKEN
docker compose exec localforge env | grep GITHUB_TOKEN
```

---

## Edge cases y validaciones

| Escenario | Resultado esperado |
|---|---|
| `PROJECTS_ROOT` no configurado (vacío) | Workspace tools no disponibles, error informativo |
| Path traversal en nombre de workspace | Rechazado por `_is_safe_name()` |
| Scaffold con template inexistente | Error claro: template no encontrado |
| GitHub push sin `GITHUB_TOKEN` | Error: token no configurado |
| Workspace activo eliminado manualmente | `get_active_root()` detecta que no existe, limpia referencia |

---

## Verificar en logs

```bash
# Workspace operations
docker compose logs -f localforge 2>&1 | grep -i "workspace\|scaffold\|deliver"

# Template usage
docker compose logs -f localforge 2>&1 | grep -i "template\|scaffold_project"

# Delivery
docker compose logs -f localforge 2>&1 | grep -i "github\|zip\|preview"
```

---

## Tests automatizados

```bash
.venv/bin/python -m pytest tests/test_workspace_engine.py -v
# 18 tests: create, switch, validate, path safety, templates, delivery
```

---

## Troubleshooting

| Problema | Causa probable | Solución |
|---|---|---|
| Workspace tools no aparecen | `PROJECTS_ROOT` no configurado | Agregar a `.env`: `PROJECTS_ROOT=data/projects` |
| Scaffold falla | Directorio ya existe | Usar nombre diferente o eliminar el existente |
| GitHub push falla | `gh` no instalado o `GITHUB_TOKEN` faltante | Instalar gh CLI, configurar token |
| Preview no accesible | Puerto en uso o firewall | Verificar puerto, usar otro |

---

## Variables relevantes para testing

| Variable (`.env`) | Valor de test | Efecto |
|---|---|---|
| `PROJECTS_ROOT` | `data/projects` | Directorio raíz para workspaces (vacío = deshabilitado) |
| `GITHUB_TOKEN` | Token válido | Requerido para delivery a GitHub |
