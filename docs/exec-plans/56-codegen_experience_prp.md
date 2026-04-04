# PRP: CodeGen Experience — Generación de Proyectos y Aplicaciones desde WhatsApp (Plan 56)

## Archivos a Modificar

### A. Project Workspace Engine
- `app/workspace/engine.py`: **Nuevo** — Workspace lifecycle (create, switch, list, get_root)
- `app/workspace/templates.py`: **Nuevo** — Template registry + scaffold
- `app/workspace/delivery.py`: **Nuevo** — GitHub push, zip, preview
- `app/skills/tools/selfcode_tools.py`: **Refactor** — parametrizar `_PROJECT_ROOT` por workspace activo
- `app/skills/tools/shell_tools.py`: **Refactor** — `_PROJECT_ROOT` dinámico via workspace
- `app/skills/tools/workspace_tools.py`: **Refactor** — integrar con nuevo engine

### B. Templates
- `data/templates/html-static/`: **Nuevo** — index.html, styles.css, README.md
- `data/templates/python-fastapi/`: **Nuevo** — main.py, models.py, requirements.txt, tests/
- `data/templates/react-vite/`: **Nuevo** — package.json, src/App.tsx, vite.config.ts
- `data/templates/nextjs/`: **Nuevo** — package.json, app/page.tsx, next.config.js

### C. Agent Integration
- `app/agent/loop.py`: Integrar workspace context en el system prompt del agente
- `app/agent/planner.py`: Agregar awareness de workspace activo y template disponibles
- `app/agent/workers.py`: Ejecutar workers en paralelo cuando `depends_on` lo permite
- `app/config.py`: Settings nuevos (`PROJECTS_ROOT`, `ENABLE_GITHUB_PUSH`, etc.)
- `skills/codegen/SKILL.md`: **Nuevo** — Skill definition para code generation

### D. Delivery
- `app/whatsapp/client.py`: Agregar `send_document()` para envío de ZIP files

## Fases de Implementación

### Phase 1: Workspace Engine — Desacoplar `_PROJECT_ROOT`

**Objetivo**: Que las file/shell tools puedan operar en cualquier directorio autorizado, no solo en el repo de LocalForge.

- [x] Crear `app/workspace/engine.py`:
  ```python
  class WorkspaceEngine:
      def __init__(self, projects_root: Path, localforge_root: Path):
          self._projects_root = projects_root
          self._localforge_root = localforge_root
          self._active: dict[str, Path] = {}  # phone -> active workspace path
      
      def create_workspace(self, name: str, phone: str) -> Path:
          """Create new project directory. Git init. Return path."""
      
      def set_active(self, phone: str, workspace_path: Path) -> None:
          """Set active workspace for a phone number."""
      
      def get_active_root(self, phone: str) -> Path:
          """Return active workspace root, or localforge root if none."""
      
      def list_workspaces(self) -> list[dict]:
          """List all project directories in PROJECTS_ROOT."""
      
      def is_valid_path(self, path: Path, phone: str) -> bool:
          """Check if path is within the active workspace."""
  ```
- [x] Agregar a `app/config.py`:
  ```python
  projects_root: str = ""  # Empty = disabled. Set to e.g. "~/projects"
  enable_github_push: bool = False
  ```
- [x] Refactor `selfcode_tools.py`:
  - Reemplazar `_PROJECT_ROOT` global con `workspace_engine.get_active_root(phone)`
  - Pasar `phone` a las closures via el contexto de la sesión/request
  - `_is_safe_path()` valida contra workspace root activo
  - **Backwards compatible**: si `projects_root` está vacío, todo sigue igual (solo LocalForge root)
- [x] Refactor `shell_tools.py`:
  - `run_command()` usa `cwd=workspace_engine.get_active_root(phone)` en vez de `_PROJECT_ROOT`
- [x] Refactor `workspace_tools.py`:
  - Delegar a `WorkspaceEngine` en vez de manejar state propio
  - Agregar `create_workspace` tool
- [x] Registrar `WorkspaceEngine` en `app.state` via `dependencies.py`
- [x] Tests: crear workspace, switch, validar path safety, backwards compat

### Phase 2: Templates & Scaffolding

- [x] Crear `app/workspace/templates.py`:
  ```python
  TEMPLATE_REGISTRY = {
      "html-static": {
          "description": "Static HTML/CSS/JS website",
          "files": ["index.html", "styles.css", "script.js"],
      },
      "python-fastapi": {
          "description": "Python FastAPI REST API with SQLite",
          "files": ["main.py", "models.py", "requirements.txt", "tests/test_api.py"],
      },
      "react-vite": {
          "description": "React + TypeScript with Vite",
          "files": ["package.json", "src/App.tsx", "src/main.tsx", "vite.config.ts", "index.html"],
      },
      "nextjs": {
          "description": "Next.js App Router project",
          "files": ["package.json", "app/page.tsx", "app/layout.tsx", "next.config.js"],
      },
  }
  
  def scaffold(workspace_path: Path, template_name: str, project_name: str) -> list[str]:
      """Copy template files to workspace, replacing placeholders. Return list of created files."""
  
  def list_templates() -> list[dict]:
      """Return available templates with descriptions."""
  ```
- [x] Crear template files en `data/templates/`:
  - `html-static/`: HTML5 responsive skeleton con placeholders `{{PROJECT_NAME}}`, `{{DESCRIPTION}}`
  - `python-fastapi/`: FastAPI app con health check, CORS, SQLite setup
  - `react-vite/`: Vite config + React App component skeleton
  - `nextjs/`: Next.js 14+ app router skeleton
- [x] Registrar tools:
  - `scaffold_project(name: str, template: str)` → crea workspace + copia template
  - `list_templates()` → retorna templates disponibles
- [x] Tests: scaffold cada template, verify files created

### Phase 3: Delivery Pipeline

- [x] Crear `app/workspace/delivery.py`:
  ```python
  async def push_to_github(workspace: Path, repo_name: str, github_token: str) -> str:
      """Git add + commit + create repo + push. Returns URL."""
  
  async def create_zip(workspace: Path) -> Path:
      """Zip the workspace directory. Returns path to .zip file."""
  
  async def serve_preview(workspace: Path, port: int = 0) -> str:
      """Start a temporary HTTP server for static sites. Returns URL."""
  ```
- [x] `push_to_github()`:
  - Usar `gh` CLI o GitHub API via httpx
  - `git add .` → `git commit -m "Initial commit from LocalForge"` → `gh repo create` → `git push`
  - HITL approval antes de push
  - Retornar URL del repo
- [x] `create_zip()`:
  - `shutil.make_archive()` excluyendo `node_modules`, `.git`, `__pycache__`, `.venv`
  - Max size check (WhatsApp doc limit: 100MB)
- [x] `send_document()` en `app/whatsapp/client.py`:
  - Upload media → send document message con caption
- [x] Registrar tools:
  - `deliver_project(method: "github" | "zip" | "preview")` → ejecuta el delivery
- [x] Tests: mock GitHub API, verify zip creation, verify WA document send

### Phase 4: Agent Integration — Prompts & Parallel Workers

- [x] Crear `skills/codegen/SKILL.md`:
  ```yaml
  name: codegen
  description: Generate complete projects and applications from user descriptions
  version: "1.0"
  tools:
    - scaffold_project
    - list_templates
    - deliver_project
    - write_source_file
    - apply_patch
    - read_source_file
    - list_source_files
    - run_command
  ```
  Con instrucciones de cuándo usar templates vs generar desde cero, y workflow recomendado.
- [x] Modificar `_AGENT_SYSTEM_PROMPT` en `app/agent/loop.py`:
  - Agregar sección de workspace awareness:
    ```
    WORKSPACE: {workspace_name} at {workspace_path}
    Available templates: {templates}
    ```
- [x] Modificar `_PLANNER_SYSTEM_PROMPT` en `app/agent/planner.py`:
  - Agregar "scaffolder" worker type que crea la estructura inicial
  - Agregar awareness de templates disponibles para que el planner elija uno
- [x] **Workers paralelos** en `app/agent/loop.py`:
  - En `_run_planner_session()`, al iterar tasks pendientes:
    ```python
    # Group tasks by dependency readiness
    ready = [t for t in plan.pending_tasks() if plan.deps_met(t)]
    if len(ready) > 1:
        results = await asyncio.gather(*[
            execute_worker(task=t, ...) for t in ready
        ])
    ```
  - El planner ya genera `depends_on` — solo falta que el orchestrator los respete
- [x] Status updates al usuario durante ejecución:
  - En `_run_planner_session()`, antes de cada task: `wa_client.send_message(phone, f"⚡ {task.description}")`
- [x] Tests: verify planner generates scaffold task, verify parallel execution

### Phase 5: Documentación & QA

- [x] `make test` pasa
- [x] `make lint` pasa
- [x] E2E test manual: "creame una landing page" → archivos generados → zip entregado
- [x] Crear `docs/features/56-codegen_experience.md`
- [x] Crear `docs/testing/56-codegen_testing.md`
- [x] Actualizar `AGENTS.md`: nuevo skill `codegen`, nuevo módulo `workspace/`
- [x] Actualizar `CLAUDE.md`: patrón de workspace dinámico, delivery pipeline

## Dependencias entre Phases

```
Phase 1 (Workspace Engine) ← obligatorio primero, todo depende de esto
  ↓
Phase 2 (Templates)  ──── independiente de ───── Phase 3 (Delivery)
  ↓                                                  ↓
Phase 4 (Agent Integration) ← requiere Phase 1+2+3
  ↓
Phase 5 (Docs)
```

Phase 2 y 3 se pueden hacer en paralelo después de Phase 1.

## Notas de Implementación

### Sobre la limitación de tokens
Con qwen3.5:9b (32K contexto), generar un archivo grande (>200 líneas) de un solo shot es arriesgado. Estrategia:
- Templates proveen el 60-70% del boilerplate
- El LLM solo genera las partes personalizadas (contenido, lógica de negocio)
- Para archivos largos, generar en secciones con `apply_patch` incremental

### Sobre workers paralelos
Los workers comparten el filesystem pero no deberían editar el mismo archivo. El planner debe asegurar que tasks paralelas operen en archivos distintos. Si dos workers tocan el mismo archivo, el segundo verá el resultado del primero (filesystem es la fuente de verdad).

### Sobre delivery
El método de delivery depende de lo que el usuario tenga configurado:
- Si `GITHUB_TOKEN` está set → ofrecer push a GitHub
- Si no → default a ZIP por WhatsApp
- Preview server es bonus para HTML estático (requiere puerto accesible)
