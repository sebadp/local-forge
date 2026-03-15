# Testing Guide — Agentic Sessions

> This document covers how to test the Agentic Sessions feature at all levels:
> automated unit/integration tests, local Docker simulation, and manual WhatsApp testing.

---

## 1. Automated Tests

All tests live in `tests/test_agent.py`. Run with:

```bash
# All agent tests
pytest tests/test_agent.py -v

# With output (useful for debugging async timing)
pytest tests/test_agent.py -v -s

# Fast smoke-check (no slow HITL timeout tests)
pytest tests/test_agent.py -v -k "not timeout"

# Full test suite (no regressions)
pytest tests/ -q
```

### Test Coverage

| Area | Tests |
|------|-------|
| `AgentSession` model | `test_agent_session_defaults`, `test_agent_status_values` |
| `loop.py` helpers | `test_create_session`, `test_get_active_session_*`, `test_cancel_*` |
| Task memory tools | `test_task_plan_full_lifecycle`, `test_update_task_status_*`, `test_task_memory_no_active_session` |
| HITL mechanism | `test_resolve_hitl_no_pending`, `test_hitl_resolve_consumes_message`, `test_hitl_timeout` |
| Write tools | `test_write_source_file_disabled`, `test_write_source_file_blocked_sensitive`, `test_apply_patch_disabled` |
| Git tools | `test_git_status_success`, `test_git_create_branch_*`, `test_git_commit_*` |
| `/cancel` command | `test_cmd_cancel_no_session`, `test_cmd_cancel_active_session` |
| `/agent` command | `test_cmd_agent_status_no_session`, `test_cmd_agent_status_with_session` |

---

## 2. Local Docker Testing

These steps test the **full agent loop** end-to-end inside the Docker environment.

### Step 1: Enable write tools

In your `.env`:
```bash
AGENT_WRITE_ENABLED=true
AGENT_MAX_ITERATIONS=15
```

### Step 2: Build and start

```bash
docker compose up --build
```

### Step 3: Send a task via `/simulate` or test client

If you have a local webhook simulator, send a message like:

```
"Crea el archivo app/agent/scratch.md con el contenido 'Test agéntico exitoso'"
```

### Step 4: Check from logs

```bash
docker compose logs -f localforge | grep -E "Agent session|run_agent"
```

You should see:
```
Agent session abc123 started for 5491112345678: Crea el archivo...
Agent session abc123 completed
```

---

## 3. Manual WhatsApp Testing Scenarios

### 3.1 Basic session — file creation

**Input:** `"Crea el archivo test.md con el texto 'hola mundo'"`

**Expected behavior:**
1. LocalForge responde inmediatamente: `"🤖 Entendido, inicio sesión de trabajo..."`
2. El agente llama `write_source_file("test.md", "hola mundo")`
3. LocalForge envía: `"✅ Sesión agéntica completada\n..."`
4. El archivo `test.md` existe en la raíz del proyecto

**Verificar:**
```bash
cat localforge-assistant/test.md  # debe decir "hola mundo"
```

---

### 3.2 `/agent` — Ver estado durante la ejecución

Si hay una sesión corriendo, enviar `/agent` debe retornar:

```
🤖 Sesión agéntica activa
Estado: running
Objetivo: Crea el archivo...

Plan actual:
- [x] Crear el archivo test.md
- [ ] Verificar contenido
```

---

### 3.3 `/cancel` — Cancelar sesión

1. Iniciar una sesión larga (e.g., investigar el codebase completo)
2. Enviar `/cancel`
3. LocalForge debe responder: `"🛑 Sesión agéntica cancelada."`
4. Confirmar: `/agent` responde `"No hay ninguna sesión agéntica activa."`

---

### 3.4 HITL — Aprobación antes de commit

**Input:** `"Agrega un comentario en la primera línea de app/config.py y haz un commit"`

**Expected behavior:**
1. El agente lee el archivo
2. El agente aplica el patch
3. El agente pregunta: `"⏸️ ¿Te parece bien el cambio antes de hacer el commit? [S/N]"`
4. El usuario responde `"sí"`
5. El agente hace el commit
6. LocalForge reporta el resultado

**Verificar:**
```bash
git -C localforge-assistant log --oneline -1  # debe mostrar el commit del agente
```

---

### 3.5 Write tools deshabilitados (seguridad)

Con `AGENT_WRITE_ENABLED=false`:

**Input:** `"Crea el archivo test.md"`

**Expected behavior:** El agente debe responder indicando que los write tools están deshabilitados, sin crear ningún archivo.

---

### 3.6 Git tools — Branch + commit completo

**Input:** `"Lista los archivos en app/agent/ y crea una rama llamada 'test/agent-branch'"`

**Expected behavior:**
1. Llama `list_source_files("app/agent")`
2. Llama `git_create_branch("test/agent-branch")`
3. Reporta el resultado

**Verificar:**
```bash
git -C localforge-assistant branch | grep test/agent-branch
```

---

## 4. Checklists de Verificación

### ✅ Antes del merge
- [ ] `pytest tests/test_agent.py -v` → todos los tests pasan
- [ ] `pytest tests/ -q` → sin regresiones
- [ ] `ruff check app/agent/ app/skills/tools/git_tools.py` → sin errores
- [ ] Test manual 3.1 (file creation) funciona
- [ ] Test manual 3.4 (HITL) funciona con `AGENT_WRITE_ENABLED=true`
- [ ] Verificar que con `AGENT_WRITE_ENABLED=false` los write tools retornan error

### ✅ En staging/producción
- [ ] `AGENT_WRITE_ENABLED=false` en producción (solo habilitar explícitamente)
- [ ] Verificar que `/cancel` detiene una sesión activa
- [ ] Verificar que una sesión que supera el timeout envía el mensaje de error al usuario

---

## 5. Tests de regresión importantes

Estos tests del resto del proyecto son los más susceptibles a romperse con cambios en el agent:

| Test | Por qué importa |
|------|-----------------|
| `test_webhook_commands.py` | `/cancel` y `/agent` se despachan por el mismo pipeline |
| `test_tool_executor.py` | `execute_tool_loop` es reutilizado por el Agent Loop |
| `test_skill_registry.py` | Los session-scoped tools se registran dinámicamente |
| `test_webhook_incoming.py` | El HITL interception corre en `process_message` |

Correrlos explícitamente:
```bash
pytest tests/test_webhook_commands.py tests/test_tool_executor.py tests/test_skill_registry.py tests/test_webhook_incoming.py -v
```
