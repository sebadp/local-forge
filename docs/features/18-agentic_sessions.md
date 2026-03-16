# Sesiones Agénticas (Agent Mode)

> **Estado:** ✅ Implementado — LocalForge puede operar en modo agente autónomo para completar tareas complejas en segundo plano.

El modo agente transforma a LocalForge de un chatbot reactivo a un **"Software Worker" autónomo**. Cuando el
usuario le pide una tarea compleja (crear un PR, refactorizar un módulo, investigar un bug),
el agente acepta el pedido, responde de inmediato, trabaja en segundo plano, y notifica al usuario cuando termina.

---

## Flujo de una sesión

```
Usuario WA: "Crea una rama y arregla el bug del color del header"
    ↓
LocalForge responde: "🤖 Entendido, inicio sesión de trabajo. Te aviso cuando termine."
    ↓
[Background: Agent Loop se lanza como asyncio.Task]
    ↓
    create_task_plan("- [ ] Leer index.css\n- [ ] Aplicar fix\n- [ ] Commit\n- [ ] Push")
    read_source_file("app/static/index.css")
    apply_patch("app/static/index.css", "color: red", "color: blue")
    git_create_branch("fix/header-color")
    git_commit("fix: change header color to blue")
    git_push("fix/header-color")
    ↓
LocalForge WA: "✅ Sesión completada. Branch 'fix/header-color' lista para PR."
```

---

## Arquitectura

```
app/agent/
├── __init__.py          # Package marker
├── models.py            # AgentSession, AgentStatus
├── loop.py              # run_agent_session(), create_session(), cancel_session()
├── task_memory.py       # Tools: create_task_plan, get_task_plan, update_task_status
└── hitl.py              # Human-in-the-Loop: request_user_approval, resolve_hitl

app/skills/tools/
└── git_tools.py         # git_status, git_diff, git_create_branch, git_commit, git_push
    selfcode_tools.py    # +write_source_file, +apply_patch
```

### Módulos

#### `app/agent/models.py`
Contiene `AgentSession` (dataclass) y `AgentStatus` (enum).

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `session_id` | `str` | UUID hex único |
| `phone_number` | `str` | Usuario que inició la sesión |
| `objective` | `str` | Pedido original del usuario |
| `status` | `AgentStatus` | `running` / `waiting_user` / `completed` / `failed` / `cancelled` |
| `task_plan` | `str \| None` | Checklist markdown actualizable |
| `max_iterations` | `int` | Límite de iteraciones (default: 15) |

#### `app/agent/loop.py`

- **`run_agent_session(session, ollama_client, skill_registry, wa_client, mcp_manager)`**  
  Función principal que lanza el loop agéntico completo. Registra las herramientas de sesión (task memory + HITL), ejecuta `execute_tool_loop()` con `max_tools=session.max_iterations`, y envía el resultado por WhatsApp al terminar.

- **`create_session(phone_number, objective, max_iterations)`**  
  Factory function que crea un `AgentSession` con UUID único.

- **`get_active_session(phone_number)`** / **`cancel_session(phone_number)`**  
  Consulta y control de sesiones activas. `cancel_session` cancela el `asyncio.Task` real (no solo el status flag) y acepta tanto el estado `running` como `waiting_user`.

#### `app/agent/task_memory.py`

Registra tres tools en el skill registry de la sesión:

| Tool | Uso |
|------|-----|
| `create_task_plan(plan)` | Crea la lista de pasos en formato markdown checklist |
| `get_task_plan()` | Lee el plan actual para re-orientarse |
| `update_task_status(task_index, done)` | Marca un paso como hecho `[x]` o pendiente `[ ]` |

#### `app/agent/hitl.py`

Permite al agente pausar y esperar aprobación humana antes de acciones críticas.

- **`request_user_approval(phone_number, question, wa_client, timeout=120)`**  
  Envía la pregunta al usuario y bloquea la corutina hasta recibir respuesta (o timeout de 120s).

- **`resolve_hitl(phone_number, user_message)`**  
  Llamado en `router.py` cuando llega un mensaje. Si hay un HITL activo, consume el mensaje y devuelve `True` (sin procesamiento normal).

#### `app/skills/tools/git_tools.py`

| Tool | Descripción |
|------|------------|
| `git_status()` | Estado del working tree (formato corto) |
| `git_diff()` | Resumen de cambios staged/unstaged (reporta errores si los hay) |
| `git_create_branch(branch_name)` | Crea y hace checkout de un branch nuevo (protegido contra flag injection con `--`) |
| `git_commit(message)` | `git add -A` + commit; verifica el código de retorno del stage antes de commitear |
| `git_push(branch_name?)` | Push del branch actual o del especificado; valida que el nombre no empiece con `-` |

#### Write tools en `selfcode_tools.py`

| Tool | Descripción |
|------|------------|
| `write_source_file(path, content)` | Escribe un archivo completo (para archivos **nuevos**) |
| `apply_patch(path, search, replace)` | Reemplaza la primera ocurrencia de `search` por `replace` (para **editar** archivos existentes) |

> ⚠️ Ambas herramientas requieren `AGENT_WRITE_ENABLED=true`, validan que el path esté dentro del proyecto, y bloquean extensiones binarias (`.pyc`, `.db`, `.jpg`, etc.).

---

## Comandos de usuario

| Comando | Descripción |
|---------|-------------|
| `/agent` | Ver estado de la sesión activa (status + task plan) |
| `/cancel` | Cancelar la sesión agéntica activa |

---

## Configuración (`.env`)

```bash
# Habilita write tools (write_source_file, apply_patch). OFF por defecto.
AGENT_WRITE_ENABLED=false

# Máximo de iteraciones de tools por sesión (default: 15)
AGENT_MAX_ITERATIONS=15

# Timeout de la sesión en segundos (default: 300 = 5 min)
AGENT_SESSION_TIMEOUT=300
```

---

## Integración con el pipeline existente

El Agent Mode se apoya en la infraestructura existente:

| Infraestructura | Rol en Agent Mode |
|----------------|------------------|
| `execute_tool_loop()` | **Reutilizado directamente** con `max_tools` elevado. Hereda compaction, think-tag stripping, tracing. |
| `compact_tool_output()` | Compacta payloads grandes entre iteraciones para evitar context overflow |
| `OllamaClient.chat_with_tools()` | Motor de razonamiento del agente |
| `TraceRecorder` | Traza automáticamente cada tool call y span |
| Skill registry | El agente usa todas las herramientas ya registradas (selfcode, notes, projects, git, etc.) |

El HITL se integra en `router.py` en la función `process_message()`, **antes** de que el mensaje entre al pipeline normal:

```python
# router.py — process_message()
if msg.text:
    from app.agent.hitl import resolve_hitl
    if resolve_hitl(msg.from_number, msg.text):
        return  # El mensaje fue consumido por la sesión agéntica activa
```

---

## Seguridad

- **`AGENT_WRITE_ENABLED=false`** por defecto. Sin este flag, `write_source_file` y `apply_patch` retornan un error. Esto previene prompt injection que intente escribir archivos en producción.
- **Una sesión por usuario** simultáneamente. Si hay una activa, se rechaza la segunda hasta que el usuario use `/cancel`.
- **`/cancel` detiene la sesión real**: cancela el `asyncio.Task` subyacente, no solo el status flag. Funciona tanto en estado `running` como `waiting_user`.
- **`_is_safe_path()`**: valida que el path esté dentro del `PROJECT_ROOT` y bloquea archivos sensibles (`.env`, `*.key`, `*.pem`, `*password*`, etc.).
- **Extensiones binarias bloqueadas** en ambas write tools: `.pyc`, `.db`, `.sqlite`, `.jpg`, `.png`, `.zip`, etc.
- **Git arg injection prevention**: `git_create_branch` y `git_push` usan `--` como separador para evitar que branch names que empiecen con `-` sean interpretados como flags de git.
- **`git add -A` return code chequeado**: si el staging falla, `git_commit` retorna error en lugar de hacer commit silenciosamente.
- **Registry por sesión**: cada sesión agéntica obtiene una copia aislada del skill registry, evitando que sesiones concurrentes se sobreescriban los handlers.
- **Git timeout de 30s** por comando para evitar cuelgues en operaciones de red.

---

## Ejemplo: Sesión completa con HITL

```
Usuario: "Agrega logging a la función _run_tool_call en executor.py, con un PR"

→ Agente: "🤖 Entendido, inicio sesión de trabajo..."

→ create_task_plan("
  - [ ] Leer executor.py
  - [ ] Aplicar el logging
  - [ ] Crear branch
  - [ ] Pedir aprobación antes de commit
  - [ ] Commit y push
")
→ read_source_file("app/skills/executor.py")
→ apply_patch("app/skills/executor.py", "result = await ...", "logger.info(...)\nresult = await ...")
→ update_task_status(1, done=True)
→ update_task_status(2, done=True)
→ git_create_branch("feat/executor-logging")
→ request_user_approval("Te parece bien este cambio antes de hacer el commit?")

[Agente pausa — usuarios recibe el mensaje]

Usuario: "Sí, adelante"

[Agente reanuda]

→ git_commit("feat: add logging to _run_tool_call")
→ git_push("feat/executor-logging")
→ update_task_status(3..5, done=True)
→ Agente: "✅ Sesión completada. Branch 'feat/executor-logging' lista para PR."
```

---

## Referencias

- Conceptual: [`docs/features/18-agentic_sessions.md`](18-agentic_sessions.md)
- Exec plan: [`docs/exec-plans/18-agentic_sessions_plan.md`](../exec-plans/18-agentic_sessions_plan.md)
- Contexto relacionado: [`docs/features/04-context_compaction.md`](04-context_compaction.md)
