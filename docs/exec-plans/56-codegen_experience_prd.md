# PRD: CodeGen Experience — Generación de Proyectos y Aplicaciones desde WhatsApp (Plan 56)

## Objetivo y Contexto

### Problema Central

LocalForge tiene todas las primitivas de un coding agent (`write_source_file`, `apply_patch`, `run_command`, planner-orchestrator), pero están **atadas a su propio directorio**. El usuario no puede decir "creame una landing page" o "generá una API en FastAPI" porque:

1. **`selfcode_tools.py` hardcodea `_PROJECT_ROOT`** al directorio de LocalForge. Todo read/write/patch es relativo al propio repo del asistente.
2. **No hay ciclo completo**: crear proyecto → generar código → testear → entregar resultado al usuario.
3. **No hay forma de entregar**: WhatsApp no es una terminal. El usuario necesita un link, un zip, o un deploy.
4. **Las tools de workspace existen pero no están conectadas** con selfcode ni con shell para operar en un directorio externo.

### Qué Queremos

El usuario envía por WhatsApp:
```
/agent creame una landing page para una cafetería con secciones de menú, ubicación y contacto
```

Y LocalForge:
1. **Crea un directorio** para el proyecto en `PROJECTS_ROOT`
2. **Planea** la estructura (archivos, dependencias)
3. **Genera** los archivos (HTML, CSS, JS, config)
4. **Ejecuta** comandos si es necesario (`npm install`, `python -m venv`, etc.)
5. **Entrega** al usuario: link a GitHub repo, o URL de preview, o zip por WhatsApp

### Inspiración: Claude Code

Claude Code tiene:
- **File tools desacopladas del proyecto**: `Write`, `Edit`, `Read` operan en `cwd`, no en un directorio fijo
- **Subagentes en paralelo**: Agent tool que forkea hijos con contexto heredado
- **Worktrees**: `EnterWorktreeTool` crea un git worktree aislado para trabajo experimental
- **Plan mode**: `EnterPlanModeTool` para pensar antes de actuar

Nuestro approach adapta estos conceptos al contexto WhatsApp + Ollama:
- No tenemos terminal interactiva → el output va por mensajes WA
- No tenemos 200K tokens → necesitamos prompts focalizados y templates
- No tenemos GitHub Copilot → pero podemos pushear a GitHub y dar el link

## Alcance

### In Scope

#### A. Project Workspace Engine (el core)
- Desacoplar las file tools de `_PROJECT_ROOT` para que operen en **cualquier** directorio autorizado
- `PROJECTS_ROOT` setting (ej: `~/projects/`) donde se crean proyectos del usuario
- Tool `create_project_workspace(name, template?)` → crea directorio + git init
- Tool `set_active_workspace(name)` → cambia el CWD para todas las file/shell tools
- Reusar toda la lógica existente de `selfcode_tools.py` (read, write, patch, outline, search) pero parametrizada por workspace

#### B. Scaffolding con Templates
- Templates para stacks comunes: `html-static`, `react-vite`, `python-fastapi`, `nextjs`
- Cada template es un directorio en `data/templates/` con archivos base
- Tool `scaffold_project(name, template)` → copia template + personaliza
- El LLM puede decidir usar un template o crear desde cero

#### C. Delivery Pipeline
- **GitHub push**: `git_commit()` + `git_push()` a un repo del usuario (requiere `GITHUB_TOKEN`)
- **Preview link**: Para HTML estático, servir con un mini HTTP server temporal y exponer via ngrok/tunnel
- **ZIP delivery**: Comprimir el proyecto y enviarlo como document por WhatsApp API
- **Status updates**: Mensajes de progreso durante la generación ("📁 Creando estructura...", "✍️ Generando index.html...", "✅ Proyecto listo")

#### D. Subagentes Paralelos (mejora del planner)
- Extender `workers.py` para ejecutar workers **en paralelo** cuando no hay dependencias
- El planner ya marca `depends_on: []` — solo falta que el orchestrator use `asyncio.gather` para tasks sin dependencias

### Out of Scope
- IDE web completo (no somos Replit/StackBlitz)
- Deploy a producción (Vercel, Railway, etc.) — futuro Plan
- Edición interactiva de archivos generados via WA (demasiado friction)
- Templates complejos (monorepos, microservicios) — empezamos simple
- Browser preview rendering (Puppeteer ya existe como MCP tool)

## Casos de Uso Críticos

### 1. Landing page estática
```
Usuario: /agent haceme una landing page para mi cafetería "El Aroma" con menú, ubicación y contacto
```
→ Scaffold `html-static` → generar HTML/CSS con contenido personalizado → push a GitHub → link al usuario

### 2. API REST en Python
```
Usuario: /agent creame una API REST para gestionar una lista de tareas, con SQLite, FastAPI y CRUD completo
```
→ Scaffold `python-fastapi` → generar modelos, routes, schemas → `pip install` → `pytest` → push

### 3. Frontend React
```
Usuario: /agent quiero un dashboard de analytics con charts, dark mode, y responsive
```
→ Scaffold `react-vite` → generar componentes → `npm install` → `npm run build` → push + preview

### 4. Modificación de proyecto existente
```
Usuario: /agent en el proyecto "mi-api" agregá un endpoint /users con paginación
```
→ Switch workspace → leer estructura → planear → write/patch → test → commit

## Restricciones Arquitectónicas

### Seguridad
- Los workspaces se crean **solo** dentro de `PROJECTS_ROOT` (path traversal protection)
- `_is_safe_path()` se extiende para validar contra el workspace activo, no solo contra LocalForge root
- Shell commands en workspaces heredan el sandbox existente (denylist, allowlist, HITL)
- Los templates son read-only — el agente no puede modificarlos directamente
- GitHub push requiere HITL approval

### Rendimiento
- Templates evitan que el LLM genere boilerplate (ahorra tokens)
- Los status updates son fire-and-forget (no esperan response)
- Para proyectos grandes, el planner debe crear ≤6 tasks (cap existente)

### Modelo
- Generación de código funciona mejor con prompts focalizados por archivo que con un mega-prompt
- Cada worker genera 1-3 archivos, no el proyecto completo
- El synthesizer al final verifica coherencia y corre tests
