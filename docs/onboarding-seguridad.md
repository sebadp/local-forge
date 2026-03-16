# Guia de Onboarding: Seguridad y Extensibilidad de LocalForge

> **Audiencia**: Desarrolladores nuevos al proyecto
> **Ultima actualizacion**: 2026-03-08
> **Prerequisitos**: Python async basico, FastAPI, conceptos de LLM

---

## 1. Que es LocalForge

LocalForge es un asistente personal via WhatsApp/Telegram construido sobre LLMs locales (Ollama).
El bot puede: chatear, recordar cosas, gestionar proyectos, tomar notas, ejecutar comandos,
navegar la web, y mucho mas — todo extensible via un sistema de skills y servidores MCP.

El proyecto tiene una **superficie de ataque significativa** porque un LLM decide que herramientas
ejecutar en tu sistema. Por eso la seguridad no es un add-on: es una capa integral que atraviesa
todo el stack.

---

## 2. Capas de Seguridad — Mapa General

```
[Mensaje entrante]
     |
     v
  1. HMAC Signature Validation        <- rechaza payloads falsos
     |
     v
  2. Rate Limiter                     <- previene abuso por volumen
     |
     v
  3. Message Dedup                    <- evita procesamiento duplicado
     |
     v
  4. Intent Classification            <- decide si se necesitan tools
     |
     v
  5. Policy Engine                    <- ALLOW / BLOCK / FLAG por tool
     |
     v
  6. HITL (Human-in-the-Loop)         <- aprobacion humana para FLAG
     |
     v
  7. Tool Execution Sandbox           <- shell sin shell=True, stdin cerrado
     |
     v
  8. Guardrails Pipeline              <- valida respuesta antes de enviar
     |
     v
  9. Audit Trail                      <- registro inmutable de toda accion
     |
     v
[Respuesta al usuario]
```

Cada capa es independiente. Si una falla, las otras siguen protegiendo.
Esto se llama **Defensa en Profundidad**.

---

## 3. Capa por Capa — Por Que y Como

### 3.1 HMAC Signature Validation

**Archivo**: `app/webhook/security.py`

**Por que**: WhatsApp envia un webhook a tu servidor. Sin verificacion, cualquier persona
que conozca tu URL podria enviar payloads falsos y hacer que el bot ejecute cosas.

**Como funciona**: Meta firma cada request con tu `app_secret` usando HMAC-SHA256.
Nosotros recalculamos la firma y comparamos con `hmac.compare_digest()` (timing-safe).

```python
# app/webhook/security.py
def validate_signature(payload: bytes, signature: str, app_secret: str) -> bool:
    expected = hmac.new(
        app_secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)
```

**Detalle importante**: `compare_digest` evita timing attacks — una comparacion normal
con `==` filtra informacion sobre cuantos bytes coinciden por el tiempo de respuesta.

---

### 3.2 Rate Limiter

**Archivo**: `app/webhook/rate_limiter.py`

**Por que**: Un usuario (o atacante) podria enviar cientos de mensajes por segundo.
Sin limite, cada mensaje dispara llamadas al LLM, embeddings y queries a la DB.

**Como funciona**: Sliding window por numero de telefono. Usa `time.monotonic()`
(no `time.time()`, que puede retroceder si se ajusta el reloj del sistema).

```python
# Uso en router.py
if not rate_limiter.check(phone_number):
    logger.warning("Rate limited: %s", phone_number)
    return  # silenciosamente descarta el mensaje
```

**Config**: `rate_limit_max_requests` y `rate_limit_window_seconds` en `app/config.py`.

---

### 3.3 Message Dedup (Deduplicacion Atomica)

**Archivo**: `app/database/repository.py`

**Por que**: WhatsApp puede reenviar el mismo webhook multiples veces (garantia at-least-once).
Sin dedup, el bot responderia varias veces al mismo mensaje.

**Como funciona**: `INSERT OR IGNORE` sobre una tabla con `wa_message_id` como PRIMARY KEY.
Si la fila ya existe, `rowcount == 0` — sabemos que ya la procesamos.

```python
# app/database/repository.py
async def try_claim_message(self, message_id: str) -> bool:
    cursor = await self.conn.execute(
        "INSERT OR IGNORE INTO processed_messages (wa_message_id) VALUES (?)",
        (message_id,),
    )
    return cursor.rowcount == 0  # True = ya procesado, skip
```

**Por que no un set en memoria**: Porque se pierde al reiniciar. SQLite persiste.

**Por que no Redis**: No es necesario para un bot single-instance. Menos dependencias = menos
cosas que pueden fallar.

---

### 3.4 Policy Engine (Reglas de Seguridad para Tools)

**Archivo**: `app/security/policy_engine.py`
**Config**: `data/security_policies.yaml`

**Por que**: El LLM decide que tools llamar. Sin restricciones, podria ejecutar
`run_command("rm -rf /")` si un prompt injection lo convence. El Policy Engine
es una capa **determinista** (sin LLM) que evalua ANTES de ejecutar.

**Como funciona**: Lee un archivo YAML con reglas. Cada regla dice:
- `target_tool`: nombre de la tool
- `argument_match`: regex sobre los argumentos
- `action`: `allow`, `block`, o `flag` (requiere aprobacion humana)

```yaml
# data/security_policies.yaml
version: "1.0"
default_action: "allow"
rules:
  - id: "block_destructive_rm"
    target_tool: "run_command"
    argument_match:
      command: '(?i).*rm\s+-r.*\s+/(?!tmp|data).*'
    action: "block"
    reason: "Destructive rm -rf outside safe directories"

  - id: "flag_git_push"
    target_tool: "git_push"
    argument_match: {}
    action: "flag"
    reason: "All git pushes require human approval"
```

**Fail-secure**: Si el archivo YAML no existe, el default es `BLOCK` — ninguna tool
se ejecuta. Esto es intencional: es mejor bloquear todo que permitir todo por accidente.

**Como se integra en el executor**:

```python
# app/skills/executor.py — dentro de _run_tool_call()
policy = await get_policy_engine()
decision = policy.evaluate(tool_name, arguments)

if decision.is_blocked:
    audit.record(tool_name, arguments, "block", decision.reason, "blocked_by_policy")
    return ChatMessage(role="tool", content=f"Security Policy Blocked: {decision.reason}")

if decision.requires_flag:
    approved = await hitl_callback(tool_name, arguments, decision.reason)
    if not approved:
        return ChatMessage(role="tool", content="Denied by user.")
```

**Leccion aprendida**: El exec plan 34 cambio el default de ALLOW a BLOCK pero no creo
el archivo `data/security_policies.yaml` en el deploy. Resultado: TODAS las tools se
bloquearon y el bot dejó de poder listar proyectos, notas, etc. La solucion fue crear
el archivo con `default_action: "allow"` y reglas especificas para tools peligrosas.

> **Moraleja**: Un cambio de seguridad que bloquea features legitimas no es seguridad,
> es un bug. Siempre probar el flujo end-to-end despues de hardening.

**Patron de deteccion**: El `PolicyEngine` tiene una propiedad `is_misconfigured` que
es `True` cuando el archivo YAML falta o no se pudo cargar. El executor usa esta propiedad
para diferenciar el mensaje que le da al LLM:

- **Misconfigured** → El LLM recibe un mensaje claro que dice al usuario: "el archivo
  `data/security_policies.yaml` no existe, copia el `.example` y reinicia el servidor."
- **Blocked by rule** → El LLM le dice al usuario que la tool fue bloqueada por politica
  y que puede agregar una regla `allow` en el YAML si la necesita.

En ambos casos, el usuario recibe informacion **accionable** sobre como resolver el problema.

```python
# app/skills/executor.py — dentro de _run_tool_call()
if decision.is_blocked:
    if policy.is_misconfigured:
        # Archivo falta o invalido — decirle al usuario como arreglarlo
        error_msg = (
            f"CONFIGURATION ERROR: Tool '{tool_name}' is blocked because the security policy "
            "file is missing or invalid. Tell the user: the file "
            "'data/security_policies.yaml' does not exist or has errors. "
            "They should copy 'data/security_policies.yaml.example' to "
            "'data/security_policies.yaml' and restart the server."
        )
    else:
        # Bloqueo por regla — informar y sugerir como habilitarlo
        error_msg = (
            f"POLICY BLOCK: Tool '{tool_name}' was blocked by security policy. "
            f"Reason: {decision.reason}. "
            "If the user needs this tool, they should add an 'allow' rule for it "
            "in 'data/security_policies.yaml'."
        )
```

---

### 3.5 Human-in-the-Loop (HITL)

**Archivo**: `app/agent/hitl.py`

**Por que**: Algunas acciones (push a git, escribir archivos, instalar paquetes) son
demasiado riesgosas para bloquear siempre, pero demasiado peligrosas para permitir sin
supervision. La solucion: pausar y preguntar al usuario via WhatsApp.

**Como funciona**:

1. El Policy Engine retorna `FLAG` para la tool
2. El executor llama `hitl_callback(tool_name, arguments, reason)`
3. Se envia un mensaje al usuario: "El agente quiere ejecutar X. Responde si/no"
4. Se espera con `asyncio.Event()` (timeout 120s)
5. Cuando el usuario responde, `resolve_hitl()` completa el evento
6. Si aprueba → ejecuta. Si rechaza o timeout → bloquea.

```python
# app/agent/hitl.py
async def request_user_approval(phone: str, tool_name: str, args: dict, reason: str) -> bool:
    event = asyncio.Event()
    _pending_approvals[phone] = event
    # Enviar pregunta al usuario...
    try:
        await asyncio.wait_for(event.wait(), timeout=120)
        return _approval_replies.pop(phone, False)
    except asyncio.TimeoutError:
        return False  # Safe default: deny if no response
```

---

### 3.6 Shell Command Security (4 capas)

**Archivo**: `app/skills/tools/shell_tools.py`

**Por que**: El agente puede ejecutar comandos del sistema. Es la tool mas peligrosa.

**Las 4 capas**:

| Capa | Que hace | Ejemplo |
|------|----------|---------|
| **Gate** | Feature flag `AGENT_WRITE_ENABLED` | Si esta en `false`, la tool ni se registra |
| **Denylist** | Comandos prohibidos (hardcoded) | `rm, sudo, chmod, shutdown` → DENY |
| **Argument Validation** | Flags peligrosos en comandos permitidos | `python -c "..."` → DENY, `python -m pytest` → ALLOW |
| **Execution Sandbox** | `subprocess_exec` sin shell | `shell=False`, `stdin=DEVNULL`, `cwd=PROJECT_ROOT` |

```python
# Denylist: nunca se ejecutan, punto
_DENY_LIST = frozenset({"rm", "sudo", "chmod", "chown", "mkfs", "dd", "shutdown", ...})

# Patrones peligrosos: detectados en cualquier posicion del comando
_DANGEROUS_PATTERNS = frozenset({"rm -rf", "> /dev/", ":()", "/etc/passwd", "/.ssh/", ...})

# Validacion de argumentos para comandos allowlisteados
if base_cmd in _CODE_EXEC_CMDS:       # python, node, ruby...
    for tok in tokens[1:]:
        if tok in _CODE_EXEC_FLAGS:    # -c, -e, --eval, --exec
            return CommandDecision.DENY

# Ejecucion segura: sin shell, sin stdin
process = await asyncio.create_subprocess_exec(
    *tokens,
    stdout=PIPE, stderr=PIPE,
    stdin=DEVNULL,   # no puede leer input
    cwd=_PROJECT_ROOT
)
```

**Por que `create_subprocess_exec`**: A diferencia de `create_subprocess_shell`, `exec` no
pasa el comando por `/bin/sh` — cada token es un argumento literal y no hay interpretacion
de metacaracteres como `; rm -rf /`. El parametro `shell` no existe en `create_subprocess_exec`
(es exclusivo de `create_subprocess_shell`).

---

### 3.7 File Access Security

**Archivo**: `app/skills/tools/selfcode_tools.py`

**Por que**: El agente puede leer y escribir archivos del proyecto. Sin restricciones,
podria leer `.env` (con tokens), modificar `security_policies.yaml`, o escapar del
directorio del proyecto.

**Controles**:

```python
# Path traversal prevention
def _is_safe_path(path: Path) -> bool:
    resolved = path.resolve()
    if not resolved.is_relative_to(_PROJECT_ROOT):
        return False  # escape del proyecto

    # Bloquear archivos sensibles por nombre
    if any(pattern in resolved.name.lower() for pattern in _BLOCKED_NAME_PATTERNS):
        return False  # .env, secret, token, password, .key, .pem

    return True

# Archivos de config protegidos contra escritura
_BLOCKED_CONFIG_FILES = {"mcp_servers.json", "security_policies.yaml", "audit_trail.jsonl"}

# Campos sensibles ocultos en runtime config
_SENSITIVE = {
    "whatsapp_access_token", "whatsapp_app_secret", "whatsapp_verify_token",
    "ngrok_authtoken", "github_token", "langfuse_secret_key", "langfuse_public_key",
    "audit_hmac_key", "telegram_bot_token", "telegram_webhook_secret",
}
```

---

### 3.8 Calculator Safe Eval

**Archivo**: `app/skills/tools/calculator_tools.py`

**Por que**: El bot tiene una calculadora. La solucion ingenua es `eval(expression)`,
pero eso permite ejecutar cualquier codigo Python.

**Solucion**: Parsear la expresion como AST y solo permitir nodos seguros:

```python
# Solo estos nodos son validos:
ALLOWED_NODES = {ast.Constant, ast.Name, ast.UnaryOp, ast.BinOp, ast.Call}
ALLOWED_OPS = {ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow, ...}
ALLOWED_FUNCS = {"sqrt", "sin", "cos", "tan", "log", "abs", "round", "ceil", "floor"}

# Evaluacion recursiva con whitelist
def _safe_eval(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.BinOp):
        if type(node.op) not in ALLOWED_OPS:
            raise ValueError(f"Operator not allowed: {type(node.op)}")
        return OPERATOR_MAP[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    # ... etc
```

`eval("__import__('os').system('rm -rf /')")` → rechazado porque `ast.Attribute` no esta
en la whitelist.

---

### 3.9 Guardrails Pipeline

**Archivo**: `app/guardrails/pipeline.py`, `app/guardrails/checks.py`

**Por que**: Incluso despues de generar una respuesta, hay cosas que no deberian
llegar al usuario: PII filtrado, JSON crudo de tools, respuestas en el idioma
equivocado, respuestas vacias.

**Checks deterministas (sin LLM, siempre activos)**:

| Check | Que detecta |
|-------|-------------|
| `check_not_empty` | Respuesta vacia |
| `check_language_match` | Idioma incorrecto (solo si >30 chars) |
| `check_no_pii` | Tokens, emails, telefonos filtrados |
| `check_excessive_length` | >8000 chars (generacion descontrolada) |
| `check_no_raw_tool_json` | JSON crudo de tool calls en la respuesta |

**Checks LLM (opt-in via `GUARDRAILS_LLM_CHECKS=true`)**:

| Check | Que detecta |
|-------|-------------|
| `check_tool_coherence` | Tool respondio algo irrelevante a la pregunta |
| `check_hallucination` | Datos inventados (numeros, nombres, fechas) |

**Diseño fail-open**: Si un check falla por error interno, la respuesta pasa igual.
Es mejor entregar una respuesta potencialmente imperfecta que bloquear al usuario.

**Remediacion**: Si un check falla, se hace UNA llamada extra al LLM para corregir
(single-shot, sin recursion — evita loops infinitos).

---

### 3.10 Audit Trail

**Archivo**: `app/security/audit.py`

**Por que**: Necesitamos un registro inmutable de toda accion que el agente tome.
Util para debugging, compliance, y deteccion de anomalias.

**Como funciona**: Append-only JSONL con cadena de hashes SHA-256 (o HMAC-SHA256
si `AUDIT_HMAC_KEY` esta configurado).

```
{"timestamp": "...", "tool_name": "run_command", "arguments": {"command": "pytest"},
 "decision": "allow", "entry_hash": "a1b2c3...", "previous_hash": "d4e5f6..."}
{"timestamp": "...", "tool_name": "write_source_file", "arguments": {"path": "app/x.py"},
 "decision": "flag", "entry_hash": "g7h8i9...", "previous_hash": "a1b2c3..."}
```

Cada registro incluye el hash del registro anterior → si alguien modifica una linea
intermedia, todos los hashes siguientes se rompen → deteccion de tampering.

**Con HMAC**: Ademas, si se configura `AUDIT_HMAC_KEY`, el hash incluye una clave secreta.
Sin la clave, no se puede forjar un hash valido.

---

### 3.11 Docker Security

**Archivo**: `Dockerfile`

- Container corre como `appuser` (UID=1000), **no root**
- Imagen base `python:3.11-slim` (minima, sin compiladores)
- `pip install --no-cache-dir` (no almacena wheels descargados)

---

### 3.12 Database Integrity

**Archivo**: `app/database/db.py`

```sql
PRAGMA journal_mode=WAL;        -- Write-Ahead Logging: lecturas no bloquean escrituras
PRAGMA synchronous=NORMAL;      -- Balance seguridad/performance con WAL
PRAGMA foreign_keys=ON;         -- Integridad referencial enforced
```

CHECK constraints en las tablas para validar valores (roles, estados, tipos de mensaje).

---

## 4. Extensibilidad — Como Agregar Features

### 4.1 Agregar un Skill nuevo

Un "skill" es un conjunto de tools que el LLM puede usar. Se compone de:

**Paso 1**: Crear `skills/mi_skill/SKILL.md`

```markdown
---
name: mi_skill
description: Hace cosas utiles
version: 1
tools:
  - mi_tool_1
  - mi_tool_2
---
Instrucciones para el LLM sobre cuando usar estas tools.
Estas instrucciones se inyectan la primera vez que se usa el tool.
```

**Paso 2**: Crear `app/skills/tools/mi_skill_tools.py`

```python
from app.skills.registry import SkillRegistry

def register(registry: SkillRegistry) -> None:
    async def mi_tool_1(query: str) -> str:
        """Busca algo."""
        return f"Resultado para: {query}"

    registry.register_tool(
        name="mi_tool_1",
        description="Busca cosas",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Que buscar"}
            },
            "required": ["query"],
        },
        handler=mi_tool_1,
        skill_name="mi_skill",
    )
```

**Paso 3**: Registrar en `app/skills/tools/__init__.py`

```python
from app.skills.tools.mi_skill_tools import register as register_mi_skill

def register_builtin_tools(...):
    # ... otros registros ...
    register_mi_skill(registry)
```

**Paso 4**: Agregar categoria en `app/skills/router.py`

```python
TOOL_CATEGORIES = {
    # ... categorias existentes ...
    "mi_categoria": ["mi_tool_1", "mi_tool_2"],
}
```

Eso es todo. El intent classifier automaticamente incluye la nueva categoria,
el executor la encuentra en el cache, y el Policy Engine la evalua con `default_action`.

---

### 4.2 Agregar un Comando (slash command)

Los comandos son acciones directas del usuario (ej: `/remember`, `/clear`).

```python
# En app/commands/builtins.py
async def cmd_mi_comando(args: str, context: CommandContext) -> str:
    if not args.strip():
        return "Uso: /mi_comando <argumento>"
    result = await context.repository.alguna_query(args)
    return f"Resultado: {result}"

# En register_builtins():
registry.register(CommandSpec(
    name="mi_comando",
    description="Hace algo util",
    usage="/mi_comando <arg>",
    handler=cmd_mi_comando,
))
```

---

### 4.3 Agregar un Servidor MCP

MCP (Model Context Protocol) permite conectar tools externas sin escribir codigo Python.

**Opcion A**: Editar `data/mcp_servers.json`:

```json
{
  "servers": {
    "mi_server": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "mi-mcp-server"],
      "enabled": true
    }
  }
}
```

**Opcion B**: Hot-add desde el chat (si `expand` skill esta activo):

```
Usuario: Instala el servidor MCP de nombre X
Bot: [usa install_from_smithery] Servidor instalado.
```

En ambos casos, las tools del servidor aparecen automaticamente sin reiniciar.

---

### 4.4 Agregar una Plataforma de Mensajeria

Actualmente: WhatsApp y Telegram. Para agregar otra:

**Paso 1**: Implementar `PlatformClient` protocol:

```python
# app/platforms/base.py — el Protocol que debes implementar
@runtime_checkable
class PlatformClient(Protocol):
    async def send_message(self, to_id: str, text: str) -> str | None: ...
    async def download_media(self, media_id: str) -> bytes: ...
    async def mark_as_read(self, message_id: str) -> None: ...
    async def send_typing_indicator(self, to_id: str) -> None: ...
    async def remove_typing_indicator(self, to_id: str, indicator_id: str | None = None) -> None: ...
    def format_text(self, text: str) -> str: ...
    def platform_name(self) -> str: ...
```

**Paso 2**: Crear un router que convierta webhooks de la plataforma a `IncomingMessage`:

```python
from app.platforms.models import IncomingMessage, Platform

msg = IncomingMessage(
    platform=Platform.MI_PLATAFORMA,
    user_id="prefix_12345",  # prefijo unico para evitar colisiones
    message_id="unique_msg_id",
    text="Hola!",
    type="text",
)
```

**Paso 3**: Llamar `process_message_generic(msg, mi_client, ...)` — la funcion compartida
que maneja toda la logica de negocio independiente de la plataforma.

---

### 4.5 Agregar Reglas de Seguridad

Editar `data/security_policies.yaml`:

```yaml
rules:
  # Bloquear una tool completamente
  - id: "block_weather_always"
    target_tool: "get_weather"
    argument_match: {}
    action: "block"
    reason: "Weather tool disabled"

  # Bloquear solo ciertos argumentos (regex)
  - id: "block_sensitive_search"
    target_tool: "web_search"
    argument_match:
      query: '(?i).*(password|secret|credentials).*'
    action: "block"
    reason: "Blocked sensitive search terms"

  # Requerir aprobacion humana
  - id: "flag_delete_project"
    target_tool: "update_project_status"
    argument_match:
      status: "archived"
    action: "flag"
    reason: "Archiving projects requires approval"
```

Las reglas se evaluan en orden — la primera que matchea gana.
Si ninguna matchea, aplica `default_action`.

**No requiere reinicio**: El `PolicyEngine` se inicializa lazy la primera vez que se
necesita. Para recargar reglas, reiniciar el servidor.

---

### 4.6 Versionado de Prompts

Los prompts del sistema son editables sin tocar codigo:

```
/prompts                    # Lista todos los prompts activos
/prompts system_prompt      # Ver contenido del prompt de sistema
/prompts classifier 2       # Ver version 2 del classifier
/approve-prompt classifier 3  # Activar version 3 (con eval advisory)
```

Para proponer un cambio programaticamente:

```python
from app.eval.evolution import propose_prompt_change
await propose_prompt_change("system_prompt", "Eres un asistente...", ollama_client, repository)
# Crea una nueva version en la DB — requiere /approve-prompt para activar
```

---

## 5. Flujo Completo de un Mensaje

```
1. Webhook recibe POST /webhook
2. validate_signature() → rechaza si HMAC invalido
3. rate_limiter.check() → rechaza si excede limite
4. parser.extract_message() → extrae texto/audio/imagen
5. repository.try_claim_message() → rechaza si duplicado
6. send_reaction("hourglass") → feedback visual al usuario
7.
8. ═══ Procesamiento paralelo (Phase A) ═══
9. embed(query)  ||  save_message  ||  load_daily_logs
10.
11. ═══ Procesamiento paralelo (Phase B) ═══
12. search_memories || search_notes || get_summary || get_history || get_projects
13.
14. ═══ Classification (Phase C) ═══
15. classify_intent → ["projects", "notes"] o ["none"]
16.
17. ═══ LLM Call (Phase D) ═══
18. Si categories == ["none"]:
19.     respuesta = ollama.chat(messages)  # sin tools
20. Si no:
21.     tools = select_tools(categories)
22.     respuesta = execute_tool_loop(messages, tools)
23.         Para cada tool call:
24.         → policy_engine.evaluate() → ALLOW/BLOCK/FLAG
25.         → Si FLAG: hitl_callback() → espera aprobacion
26.         → Si ALLOW: ejecuta tool
27.         → audit.record() → registra decision
28.
29. ═══ Guardrails ═══
30. run_guardrails(respuesta) → check PII, idioma, longitud, etc.
31. Si falla: remediacion single-shot
32.
33. ═══ Entrega ═══
34. format_text() → convierte markdown a formato de plataforma
35. split_message() → divide si >4096 chars
36. send_message() → envia al usuario
37. remove_reaction("hourglass") → quita feedback visual
```

---

## 6. Estructura de Directorios Clave

```
app/
  security/           # Policy Engine, Audit Trail, modelos de decision
  guardrails/         # Checks pre-entrega (PII, idioma, longitud)
  skills/
    tools/            # Handlers de cada tool (Python)
    router.py         # Clasificador de intent + selector de tools
    executor.py       # Loop de ejecucion con policy check
    registry.py       # Registro de tools
  platforms/          # Abstraccion multi-plataforma (Protocol)
  webhook/            # Router HTTP, HMAC, rate limiter, parser
  agent/              # Modo agentico (planner, workers, HITL)
  eval/               # Dataset vivo, prompt versioning, evolution

data/
  security_policies.yaml      # Reglas ALLOW/BLOCK/FLAG por tool
  audit_trail.jsonl            # Log inmutable de acciones
  mcp_servers.json             # Config de servidores MCP

skills/                        # SKILL.md files (metadata + instrucciones LLM)
tests/                         # Tests (pytest, asyncio_mode=auto)
```

---

## 7. Errores Comunes y Lecciones Aprendidas

### "El bot dice que no puede acceder a tools por seguridad"

**Causa**: `data/security_policies.yaml` no existe → `PolicyEngine` default a BLOCK.
**Fix**: Crear el archivo con `default_action: "allow"` (ver seccion 3.4).

### "Los tests fallan con `ModuleNotFoundError`"

**Causa**: El venv esta roto (symlink apunta a path de macOS).
**Fix**: `ln -sf /usr/bin/python3.12 .venv/bin/python3 && ln -sf /usr/bin/python3.12 .venv/bin/python`

### "El LLM ignora todas las tools"

**Causa**: Demasiadas tools en el payload (>6). qwen3.5:9b no las procesa.
**Fix**: El router limita a `max_tools_per_call` (default 8). Verificar que `classify_intent`
retorna categorias correctas.

### "El LLM usa `think` con tools y se rompe"

**Causa**: qwen3.5:9b no soporta `think: True` + tools simultaneamente.
**Fix**: `chat_with_tools()` ya deshabilita `think` cuando hay tools presentes.

### "Los guardrails bloquean respuestas cortas en otro idioma"

**Causa**: `langdetect` es unreliable con textos cortos (<30 chars).
**Fix**: `check_language_match` ya tiene un threshold de 30 chars — textos mas cortos pasan.

---

## 8. Checklist para Nuevas Features

- [ ] Si afecta >=3 archivos: crear PRD + PRP en `docs/exec-plans/`
- [ ] Si agrega una tool: agregarla a `TOOL_CATEGORIES` en `router.py`
- [ ] Si la tool es peligrosa: agregar regla en `data/security_policies.yaml`
- [ ] Si expone datos sensibles: agregar a `_SENSITIVE` en `selfcode_tools.py`
- [ ] Si acepta input del usuario: validar (regex, AST, whitelist — nunca `eval`)
- [ ] Crear tests en `tests/` (asyncio_mode=auto, no necesita `@pytest.mark.asyncio`)
- [ ] Correr `make check` (lint + typecheck + tests) antes de push
- [ ] Crear `docs/features/<nombre>.md` y actualizar README
- [ ] Actualizar `CLAUDE.md` con patrones nuevos
- [ ] Actualizar `AGENTS.md` si agrega skill/modulo/comando

---

## 9. Variables de Entorno Criticas de Seguridad

| Variable | Default | Efecto |
|----------|---------|--------|
| `AGENT_WRITE_ENABLED` | `false` | Gate para shell/write tools — sin esto, no se registran |
| `AUDIT_HMAC_KEY` | `None` | Clave para HMAC en audit trail — sin ella, solo SHA-256 |
| `GUARDRAILS_ENABLED` | `true` | Activa pipeline de validacion pre-entrega |
| `GUARDRAILS_LLM_CHECKS` | `false` | Activa checks LLM (coherencia, alucinaciones) |
| `RATE_LIMIT_MAX_REQUESTS` | `10` | Mensajes por ventana por usuario |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Ventana del rate limiter |
| `WHATSAPP_APP_SECRET` | (requerido) | Clave para HMAC de webhooks |
| `ALLOWED_TELEGRAM_CHAT_IDS` | `""` | IDs permitidos (vacio = todos) |
