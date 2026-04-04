# Feature: CodeGen Experience — Project Generation from WhatsApp

> **Version**: v1.0
> **Fecha de implementacion**: 2026-04-02
> **Fase**: Fase 6
> **Estado**: ✅ Implementada

---

## Que hace?

Permite al usuario generar proyectos completos (landing pages, APIs, apps React/Next.js) desde WhatsApp. El agente crea un workspace, scaffoldea desde templates, genera codigo personalizado, y entrega el resultado (GitHub push, ZIP, o preview).

---

## Arquitectura

```
Usuario: "/agent creame una landing page para mi cafeteria"
        │
        ▼
  Planner creates plan → tasks (parallel when no deps)
        │
        ├── Task 1: scaffold_project("mi-cafe", "html-static")
        ├── Task 2: write/patch files with custom content  ← parallel if independent
        ├── Task 3: run_command tests
        └── Task 4: deliver_project("github" | "zip")
        │
        ▼
  User receives: GitHub URL, ZIP file, or preview link
```

---

## Componentes

### A. Workspace Engine (`app/workspace/engine.py`)
- `WorkspaceEngine` class: create, switch, list, validate paths
- Per-phone active workspace tracking
- Path safety validation (traversal protection)
- Propagates root to `selfcode_tools` and `shell_tools`

### B. Templates (`app/workspace/templates.py`)
- 4 templates: `html-static`, `python-fastapi`, `react-vite`, `nextjs`
- In-memory file definitions with `{PROJECT_NAME}` / `{DESCRIPTION}` placeholders
- `scaffold()` creates workspace + writes template files + git init

### C. Delivery (`app/workspace/delivery.py`)
- `push_to_github()`: git add/commit + `gh repo create` + push
- `create_zip()`: shutil archive excluding node_modules/.git/etc
- `serve_preview()`: temporary HTTP server for static sites

### D. Parallel Workers (`app/agent/loop.py` + `models.py`)
- `AgentPlan.ready_tasks()`: returns all pending tasks with deps met
- Planner-orchestrator runs ready tasks via `asyncio.gather` when >1
- Status updates to user after each batch

---

## Archivos clave

| Archivo | Rol |
|---|---|
| `app/workspace/engine.py` | WorkspaceEngine: lifecycle + path validation |
| `app/workspace/templates.py` | Template registry + scaffold |
| `app/workspace/delivery.py` | GitHub push, ZIP, preview server |
| `app/skills/tools/workspace_tools.py` | Tool handlers (refactored to use engine) |
| `app/agent/loop.py` | Parallel worker execution, workspace-aware prompt |
| `app/agent/models.py` | `ready_tasks()` for parallel execution |
| `tests/test_workspace_engine.py` | 18 tests |

---

## Tools registrados

| Tool | Descripcion |
|---|---|
| `create_workspace` | Crear workspace vacio con git init |
| `scaffold_project` | Crear proyecto desde template |
| `list_project_templates` | Listar templates disponibles |
| `deliver_project` | Entregar proyecto (github/zip/preview) |
| `list_workspaces` | Listar workspaces existentes |
| `switch_workspace` | Cambiar workspace activo |
| `get_workspace_info` | Info del workspace activo |

---

## Templates disponibles

| Template | Stack | Archivos |
|---|---|---|
| `html-static` | HTML5/CSS/JS | index.html, styles.css, script.js, README.md |
| `python-fastapi` | FastAPI + SQLite | main.py, models.py, requirements.txt, tests/ |
| `react-vite` | React + TypeScript + Vite | package.json, src/App.tsx, vite.config.ts, tsconfig.json |
| `nextjs` | Next.js 14 App Router | package.json, app/page.tsx, app/layout.tsx, next.config.js |

---

## Decisiones de diseno

| Decision | Alternativa | Motivo |
|---|---|---|
| Templates in-memory (no archivos en disco) | Directorio `data/templates/` | Mas simple, sin I/O, facil de mantener |
| `.replace()` para placeholders | `string.Template` o `.format()` | CSS/JSON tienen `{}` que conflictuan con `.format()` |
| Parallel workers via `asyncio.gather` | Sequential siempre | Aprovecha I/O paralelo (file writes, tool calls) |
| Preview server en thread daemon | Subprocess | Mas simple, se limpia solo al terminar el proceso |

---

## Variables de configuracion

| Variable (`config.py` / `.env`) | Default | Efecto |
|---|---|---|
| `PROJECTS_ROOT` | `""` (disabled) | Directorio raiz para workspaces |
| `GITHUB_TOKEN` | `""` | Requerido para `deliver_project("github")` |
