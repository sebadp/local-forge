# Guía de Testing Manual — LocalForge

> **Propósito**: Validación pre-release de todas las features implementadas.
> **Última actualización**: 2026-03-12
> **Rama probada**: `feat/architecture_plan`

---

## Pre-flight: arranque del sistema

```bash
docker compose up --build -d
docker compose logs -f localforge | head -80
```

Confirmar estas líneas antes de testear cualquier feature:

| Línea en logs | Qué confirma |
|---|---|
| `sqlite-vec loaded successfully (dims=768)` | Búsqueda semántica disponible |
| `Backfilled N memory embeddings` | Embeddings sincronizados al boot |
| `Memory watcher started for data/MEMORY.md` | Sync bidireccional activo |
| `Skills loaded: N skill(s)` | Skills de `skills/` cargados |
| `MCP initialized: N server(s), M tool(s)` | MCP conectado |
| `Scheduler started` | APScheduler activo |
| `Restored N cron jobs from database` | Cron jobs persistidos re-registrados |
| `Model warmup complete` | qwen3:8b y nomic-embed-text calientes |
| `Entity registry initialized, backfilling...` | Ontology graph activo |
| `Automation: seeded N rules, scheduler job registered` | Automation engine activo |
| `Telegram webhook registered` | Telegram bot conectado (si habilitado) |
| `Provenance audit logger initialized` | Data provenance activo |
| `Token estimator calibrated` | Token accuracy calibrado |

**Verificar tests automatizados:**
```bash
make check   # lint + typecheck + 785 tests
```

---

## 1. Chat básico (sin tools)

| Mensaje | Esperado |
|---|---|
| `Hola, cómo estás?` | Respuesta conversacional, sin tool calls |
| `Contame un chiste` | Respuesta directa (no llama ningún tool) |
| `Cuál es la capital de Francia?` | "París" — sin tool calls |

**Verificar en logs:**
```bash
grep "Tool router: categories=none\|plain chat" data/localforge.log | tail -5
```

---

## 2. Tipos de mensaje multimedia

| Tipo | Cómo probar | Esperado |
|---|---|---|
| **Audio** | Enviar nota de voz | Transcripción via faster-whisper → respuesta al contenido |
| **Imagen** | Enviar foto | Descripción visual via llava:7b → respuesta contextual |
| **Imagen + caption** | Foto con texto | Vision + caption como contexto conjunto |
| **Reply** | Responder a un mensaje del bot | Texto citado inyectado como contexto |
| **Reacción 👍** | Reaccionar con pulgar arriba a una respuesta | Trace score positivo guardado silenciosamente |
| **Reacción 👎** | Reaccionar con pulgar abajo | Trace score negativo guardado silenciosamente |

> Las imágenes van directo a llava:7b — **no pasan por el tool calling loop**.

---

## 3. Comandos slash

| Comando | Esperado |
|---|---|
| `/help` | Lista de todos los comandos disponibles |
| `/remember Mi cumpleaños es el 15 de marzo` | `✅ Memorized: ...` — guardado en SQLite + MEMORY.md |
| `/memories` | Lista de memorias activas con ID |
| `/forget 1` | Memoria desactivada en DB y eliminada de MEMORY.md |
| `/clear` | Limpia historial, guarda snapshot en `data/memory/snapshots/`, daily log actualizado |
| `/review-skill` | Lista de skills activos + servidores MCP |
| `/review-skill weather` | Detalle del skill: tools, estado, instrucciones |
| `/feedback Excelente respuesta` | Señal positiva guardada como trace score |
| `/rate 5` | Score 1.0 guardado en la traza actual |
| `/rate 1` | Score 0.0 guardado en la traza actual |

**Verificar `/remember` en DB:**
```bash
sqlite3 data/localforge.db "SELECT content, category FROM memories ORDER BY id DESC LIMIT 3;"
```

**Verificar snapshot después de `/clear`:**
```bash
ls data/memory/snapshots/
```

---

## 4. Herramientas builtin

### 4a. Calculadora

| Mensaje | Esperado |
|---|---|
| `Cuánto es 15 * 7 + 3?` | 108 |
| `Raíz cuadrada de 144` | 12 |
| `sin(pi/2)` | 1.0 |
| `2 ** 10` | 1024 |
| `Cuánto es import("os")?` | Rechazo sin ejecutar código |

### 4b. Fecha y hora

| Mensaje | Esperado |
|---|---|
| `Qué hora es?` | Hora actual con timezone |
| `Qué hora es en Tokio?` | Hora en Asia/Tokyo |
| `Si acá son las 14:30, qué hora es en Londres?` | Conversión correcta |

### 4c. Clima

| Mensaje | Esperado |
|---|---|
| `Clima en Buenos Aires` | Temp, humedad, viento, pronóstico via OpenMeteo |
| `Weather in New York` | Funciona en inglés |
| `Ciudad que no existe, XYZ` | Error descriptivo (no crash) |

### 4d. Búsqueda web

| Mensaje | Esperado |
|---|---|
| `Buscá noticias sobre IA` | Hasta 5 resultados con título, URL, snippet |
| `Search for Python 3.13 features` | Resultados en inglés |

### 4e. Notas (CRUD)

| Paso | Mensaje | Esperado |
|---|---|---|
| Crear | `Guardá una nota: Reunión lunes - Hablar con Juan` | `Note saved (ID: N)` |
| Listar | `Mostrá mis notas` | Lista con ID, título, preview |
| Buscar | `Buscá notas sobre reunión` | Nota encontrada (semántico + keyword) |
| Borrar | `Borrá la nota N` | `Note N deleted.` |

### 4f. Recordatorios one-shot

| Mensaje | Esperado |
|---|---|
| `Recordame revisar los logs en 2 minutos` | Confirmación con hora, ID del job |
| (esperar 2 min) | Llega WA: `⏰ Reminder: revisar los logs` |
| `Qué recordatorios tengo?` | Lista de jobs activos |

---

## 5. Cron jobs (recurrentes)

| Mensaje | Esperado |
|---|---|
| `/agent Recordame cada lunes a las 9am que revise los PRs pendientes` | Agente llama `create_cron("0 9 * * 1", "Revisar PRs...", "UTC")` + confirmación con ID |
| `Listame mis recordatorios recurrentes` | Tabla: ID, cron expr, mensaje |
| `Eliminá el recordatorio cron N` | `Cron job N deleted.` |

**Verificar persistencia después de restart:**
```bash
docker compose restart localforge
# Esperar boot
grep "Restored.*cron jobs" data/localforge.log
```

**Verificar en DB:**
```bash
sqlite3 data/localforge.db "SELECT id, cron_expr, message, active FROM user_cron_jobs;"
```

---

## 6. Sistema de memoria

### 6a. Sync bidireccional MEMORY.md ↔ SQLite

**DB → archivo (via /remember):**
1. `/remember Soy ingeniero de software`
2. Verificar que aparece en `data/MEMORY.md`

**Archivo → DB (edición manual):**
1. Editar `data/MEMORY.md`, agregar: `- [hobby] Toca la guitarra`
2. Esperar ~1s
3. Verificar: `sqlite3 data/localforge.db "SELECT content, category FROM memories WHERE content LIKE '%guitarra%';"`

**Verificar en logs:**
```bash
grep "Synced from file\|Skipping sync" data/localforge.log | tail -5
```

### 6b. Búsqueda semántica de memorias

1. Guardar memorias diversas via `/remember`:
   - `Trabajo como ingeniero de software`
   - `Tengo un perro llamado Max`
   - `Mi color favorito es el azul`
2. Preguntar `Tengo mascotas?` → Debe mencionar a Max
3. Preguntar `A qué me dedico?` → Debe mencionar ingeniería

### 6c. Pre-compaction flush (>40 mensajes)

1. Enviar 40+ mensajes con hechos memorables mezclados
2. Verificar que el summarizer se activa y extrae hechos a MEMORY.md automáticamente
3. `ls data/memory/*.md` → debe aparecer el daily log del día

### 6d. Session snapshots

1. Tener una conversación de varios mensajes
2. Enviar `/clear`
3. `ls data/memory/snapshots/` → debe aparecer un `.md` con slug descriptivo

### 6e. Verificar embeddings

```bash
sqlite3 data/localforge.db "SELECT COUNT(*) FROM vec_memories;"
# Debe ser > 0

# Memorias sin embedding (debe ser 0 después del backfill)
sqlite3 data/localforge.db "
  SELECT m.id FROM memories m
  LEFT JOIN vec_memories v ON v.memory_id = m.id
  WHERE m.active = 1 AND v.memory_id IS NULL;
"
```

---

## 7. Proyectos

| Paso | Mensaje | Esperado |
|---|---|---|
| Crear | `Creá un proyecto llamado "Backend API" con descripción: Refactoring del módulo de auth` | `create_project(...)` → confirmación con ID |
| Agregar task | `Agregá una tarea al proyecto Backend API: Migrar JWT a OAuth2` | `add_task(...)` → confirmación |
| Ver progreso | `Cómo va el proyecto Backend API?` | Resumen con tareas y estado |
| Completar task | `Marcá como hecha la tarea "Migrar JWT a OAuth2"` | `update_task(...)` status→done |
| Nota | `Agregá una nota al proyecto: La migración requiere cambiar 3 endpoints` | `add_project_note(...)` |
| Buscar | `Buscá notas del proyecto sobre endpoints` | Búsqueda semántica en project notes |
| Archivar | `Archivá el proyecto Backend API` | `update_project_status(...)` → resumen final automático |

**Verificar en DB:**
```bash
sqlite3 data/localforge.db "SELECT name, status FROM projects;"
sqlite3 data/localforge.db "SELECT description, status FROM project_tasks LIMIT 10;"
```

---

## 8. Web Browsing (MCP — Fetch)

### 8a. Puppeteer activo (modo primario)

| Mensaje | Esperado |
|---|---|
| `Qué dice https://example.com?` | Contenido real de la página |
| `Resumí https://news.ycombinator.com` | Lista de links/títulos de HN |

```bash
grep "Fetch mode: puppeteer\|Tool router.*fetch" data/localforge.log | tail -3
```

### 8b. Fallback a mcp-fetch

**Setup**: en `data/mcp_servers.json`, deshabilitar puppeteer y habilitar mcp-fetch. Reiniciar.

| Mensaje | Esperado |
|---|---|
| `Qué hay en https://example.com?` | Contenido via HTTP básico, con nota al usuario sobre fetch limitado |

```bash
grep "Fetch mode: mcp-fetch\|mcp-fetch fallback" data/localforge.log | tail -3
```

### 8c. URL detectada automáticamente

```
https://github.com/fastapi/fastapi
```
**Esperado**: el clasificador fuerza categoría "fetch" aunque diga "none". Logs: `URL detected`.

---

## 9. MCP — GitHub

**Requisito**: `GITHUB_PERSONAL_ACCESS_TOKEN` en `.env`

| Mensaje | Esperado |
|---|---|
| `Lista las issues abiertas del repo localforge-assistant` | Lista de issues con número, título |
| `Crea una issue: Test desde LocalForge` | Issue creada, retorna URL |
| `Buscá repositorios sobre FastAPI` | Lista de repos con estrellas |

---

## 10. MCP — Filesystem

**Requisito**: servidor `mcp-filesystem` configurado y habilitado en `mcp_servers.json`.

| Mensaje | Esperado |
|---|---|
| `Lista los archivos en /home/appuser/data` | Lista de archivos del directorio mapeado |
| `Leé el archivo mcp_servers.json` | Contenido del JSON |
| `Intentá leer /etc/passwd` | Error de permiso (fuera del path configurado) |

---

## 11. Selfcode (introspección del propio código)

| Mensaje / Acción | Esperado |
|---|---|
| `Cuál es tu versión actual?` | `get_version_info()` → info de git + versión |
| `Mostrá la estructura de app/skills/executor.py` | `get_file_outline(...)` → lista de funciones con números de línea |
| `Leé las líneas 229 a 260 de app/skills/executor.py` | `read_lines(...)` → código numerado |
| `Buscá en el código dónde se define select_tools` | `search_source_code("select_tools")` |
| `Cuál es la configuración runtime?` | `get_runtime_config()` — sin tokens de WA |
| `Cómo está la salud del sistema?` | `get_system_health()` — DB, embeddings, scheduler |
| `Mostrá los últimos logs de error` | `get_recent_logs(level="ERROR")` |

---

## 12. Dynamic Tool Budget

### 12a. Multi-categoría — distribución de budget

```
Necesito crear una issue en GitHub para el proyecto "backend-api" sobre el bug del login
```

**Verificar en logs:**
```bash
grep "Tool router: categories=\['projects', 'github'\]" data/localforge.log | tail -3
# Esperado: ambas categorías tienen tools en la lista (no solo projects)
```

### 12b. Meta-tool `request_more_tools`

Difícil de forzar manualmente (depende del clasificador). Verificar que está disponible:
```bash
grep "request_more_tools" data/localforge.log | tail -5
```

Si el LLM lo usa, ver: `request_more_tools: cats=[...], added=N: [tool_names]`.

---

## 13. Agent Mode — Sesiones agénticas

### 13a. Tarea simple de código

```
/agent Corrí los tests y mostrame si hay algún fallo
```

**Esperado**:
1. Respuesta inmediata: `🤖 Iniciando sesión de trabajo...`
2. En background: agente llama `run_command("pytest tests/ -v")` → parsea resultado
3. Respuesta final via WA con resultados del test
4. Logs: `Agent round 1/15`, `Tool run_command`, `Agent session completed`

### 13b. Tarea con múltiples steps

```
/agent Revisá app/skills/router.py, buscá funciones sin docstring y listame cuáles son
```

**Esperado**: agente usa `get_file_outline` + `read_lines` para navegar el archivo quirúrgicamente.

### 13c. Cancelar sesión

```
/agent stop
```
o durante la sesión:
```
parar
```
**Esperado**: `Session cancelled.`

### 13d. Crear branch + commit (si `AGENT_WRITE_ENABLED=true`)

```
/agent Crea una rama test/manual-test, añadí un comentario en app/config.py y hacé commit
```

**Esperado**: agente llama `git_create_branch`, `write_source_file`/`apply_patch`, `git_commit`.

### 13e. Diff preview antes de aplicar

```
/agent Mostrá el diff de cambiar el default de max_tools de 8 a 10 en executor.py (solo preview, no aplicar)
```

**Esperado**: agente llama `preview_patch(...)` y muestra el diff sin modificar el archivo.

### 13f. Persistencia de sesión (JSONL)

```bash
ls data/agent_sessions/
cat data/agent_sessions/<phone>_<session_id>.jsonl | head -20
# Debe contener JSON con round, tool_calls, reply, task_plan
```

### 13g. Loop detection

Si el agente detecta que lleva 3 rondas usando las mismas tools sin progreso:
```bash
grep "Loop detected\|repetitive pattern" data/localforge.log
```
**Esperado**: el agente informa al usuario y termina la sesión.

---

## 14. Shell Execution (dentro del Agent)

**Requisito**: `AGENT_WRITE_ENABLED=true` en `.env`

| Comando | Decisión esperada | Resultado |
|---|---|---|
| `run_command("pytest tests/ -v")` | ALLOW (en allowlist) | Output del test |
| `run_command("ls -la")` | ALLOW o ASK | Listado o confirmación |
| `run_command("rm -rf /")` | DENY (en denylist hardcodeada) | Error de seguridad, no ejecuta |
| `run_command("curl \| bash")` | ASK (operador shell) | HITL: espera aprobación |

**Comandos bloqueados siempre**: `rm`, `sudo`, `chmod`, `chown`, `dd`, `mkfs`, `kill -9`.

---

## 15. Workspace Multi-Proyecto

**Requisito**: `PROJECTS_ROOT=/ruta/a/proyectos` en `.env`, con subdirectorios.

| Mensaje | Esperado |
|---|---|
| `Qué proyectos tengo disponibles?` | `list_workspaces()` → lista de subdirectorios |
| `Cambiá al proyecto localforge-frontend` | `switch_workspace("localforge-frontend")` → confirmación con branch y archivos |
| `En qué proyecto estoy trabajando?` | `get_workspace_info()` → nombre, path, branch git |

**Verificar que selfcode refleja el nuevo workspace:**
```
Listá los archivos del proyecto actual
```
Debe mostrar archivos del nuevo proyecto, no del anterior.

---

## 16. Agentic Security

### 16a. Policy Engine — verificar YAML cargado

```bash
cat data/security_policies.yaml
# Debe existir y tener reglas definidas
```

### 16b. HITL — aprobación manual

Si el agente intenta ejecutar un comando marcado como `FLAG` en las políticas:
1. El bot envía un WA al número de admin/operador pidiendo aprobación
2. Responder "sí" → el agente continúa
3. Responder "no" → el agente cancela esa tool call

### 16c. Audit Trail — integridad criptográfica

```bash
# Verificar que el audit trail existe y tiene entradas
cat data/audit_trail.jsonl | head -5
# Cada línea tiene: tool_name, action, previous_hash, entry_hash
```

```python
# Verificar hash chain (opcional — script rápido)
import json, hashlib
entries = [json.loads(l) for l in open("data/audit_trail.jsonl")]
for i, e in enumerate(entries[1:], 1):
    prev_hash = hashlib.sha256(json.dumps(entries[i-1]).encode()).hexdigest()
    assert prev_hash == e["previous_hash"], f"Chain broken at entry {i}"
print("Hash chain OK")
```

---

## 17. Expand (MCP Registry)

| Mensaje | Esperado |
|---|---|
| `Buscá servidores MCP para Slack` | `search_mcp_registry("Slack")` → lista de resultados de Smithery |
| `Mostrá info del servidor brave-search de Smithery` | `get_mcp_server_info(...)` → descripción, tools disponibles |
| `Listá los servidores MCP activos` | `list_mcp_servers()` → tabla con nombre, tipo, estado |

> La instalación real (`install_from_smithery`) requiere confirmación y afecta `mcp_servers.json` — probar en entorno no productivo.

---

## 18. Eval Pipeline

### 18a. Guardrails en cada respuesta

```bash
# Buscar scores de guardrails en la última traza
sqlite3 data/localforge.db "
  SELECT check_name, value FROM trace_scores
  WHERE source = 'system'
  ORDER BY id DESC LIMIT 10;
"
```
**Esperado**: scores para `not_empty`, `language_match`, `no_pii`, `excessive_length`, `no_raw_tool_json`.

### 18b. Trazabilidad

```bash
# Ver última traza
sqlite3 data/localforge.db "
  SELECT id, input_preview, output_preview, duration_ms
  FROM traces ORDER BY id DESC LIMIT 3;
"
# Ver spans de la traza
sqlite3 data/localforge.db "
  SELECT name, kind, duration_ms FROM trace_spans
  WHERE trace_id = (SELECT id FROM traces ORDER BY id DESC LIMIT 1);
"
```

### 18c. Señales de usuario

1. Reaccionar con 👍 a una respuesta del bot → `SELECT value FROM trace_scores WHERE source='user' ORDER BY id DESC LIMIT 1;` → debe ser 1.0
2. Reaccionar con 👎 → debe ser 0.0
3. Enviar `/feedback Estuvo buenísima esa respuesta` → score positivo guardado

### 18d. Eval skill — resumen

```
/agent Mostrame el eval summary de las últimas 24 horas
```
**Esperado**: agente llama `get_eval_summary(hours=24)` → tabla con métricas por check.

### 18e. Dataset vivo

```bash
sqlite3 data/localforge.db "SELECT entry_type, COUNT(*) FROM eval_dataset GROUP BY entry_type;"
# Debe mostrar: failure, golden_candidate (y correction si hubo correcciones)
```

---

## 19. Rate limiting y graceful shutdown

### Rate limiting

Enviar >10 mensajes en menos de 60 segundos desde el mismo número:
```bash
grep "Rate limit exceeded" data/localforge.log
```
**Esperado**: algunos mensajes ignorados silenciosamente sin error 500.

### Graceful shutdown

```bash
docker compose stop localforge
grep "Graceful shutdown\|Waiting for.*in-flight" data/localforge.log | tail -5
```
**Esperado**: espera hasta 30s a que los background tasks terminen antes de cerrar.

---

## 20. Graceful degradation

| Escenario | Cómo simular | Esperado |
|---|---|---|
| Sin `nomic-embed-text` | `ollama rm nomic-embed-text` + restart | Fallback a todas las memorias, sin crash |
| Sin sqlite-vec | Desinstalar extension + restart | App funciona sin búsqueda vectorial |
| `SEMANTIC_SEARCH_ENABLED=false` | `.env` + restart | Sin búsqueda semántica, comportamiento clásico |
| MCP no conectado | Deshabilitar servidor en `mcp_servers.json` | Tools de ese servidor no disponibles, resto funciona |
| Ambos fetch servers desactivados | Deshabilitar puppeteer + mcp-fetch | LLM informa que no puede acceder a URLs |
| DuckDuckGo rate limit | Múltiples búsquedas seguidas | `Error performing search: ...` — no crash |
| `ONTOLOGY_ENABLED=false` | `.env` + restart | Sin knowledge graph, sin enrichment — tools de ontology no registrados |
| `PROVENANCE_ENABLED=false` | `.env` + restart | Sin audit log ni versioning — mutaciones se hacen sin registrar |
| `AUTOMATION_ENABLED=false` | `.env` + restart | Sin evaluación periódica de reglas — tools de automation no registrados |
| `TELEGRAM_ENABLED=false` | `.env` + restart | Sin webhook Telegram — solo WhatsApp activo |
| Langfuse no disponible | Detener Langfuse server | Traces guardados solo en SQLite, sin crash |

---

## 21. Prompt Engineering & Versioning (Plan 32)

### 21a. Ver prompts activos

| Mensaje / Comando | Esperado |
|---|---|
| `/prompts` | Lista de todos los prompts con versión activa |
| `/prompts system` | Contenido del prompt "system" (600 chars) + historial de versiones |
| `/prompts system 2` | Contenido completo de la versión 2 (800 chars) |

### 21b. Evolución de prompts

| Paso | Mensaje | Esperado |
|---|---|---|
| Proponer | `/agent Proponé un cambio al prompt "classifier" para mejorar la clasificación de proyectos` | `propose_prompt_change(...)` → nueva versión guardada |
| Aprobar | `/approve-prompt classifier 2` | Score advisory mostrado + prompt activado |

### 21c. Eval acoplado

```bash
sqlite3 data/localforge.db "SELECT prompt_name, version, is_active, created_by FROM prompt_versions ORDER BY id DESC LIMIT 5;"
```

---

## 22. Telegram Integration (Plan 35)

**Requisito**: `TELEGRAM_BOT_TOKEN` y `TELEGRAM_ENABLED=true` en `.env`

### 22a. Recepción de mensajes

| Acción | Esperado |
|---|---|
| Enviar mensaje de texto al bot | Respuesta del LLM, identificado como `tg_<chat_id>` |
| Enviar audio al bot | Transcripción via faster-whisper → respuesta |
| Enviar imagen | Vision via llava:7b → respuesta contextual |

### 22b. Comandos slash

| Comando | Esperado |
|---|---|
| `/remember Dato de Telegram` | Memoria guardada, identificada por `tg_<chat_id>` |
| `/memories` | Mismas memorias que por WhatsApp (si mismo phone pattern) |
| `/help` | Lista de comandos formateada en HTML (no Markdown) |

### 22c. Recordatorios cross-platform

1. Desde Telegram: `Recordame en 2 minutos que revise el deploy`
2. **Esperado**: el reminder llega por Telegram (enrutado via prefijo `tg_`)

### 22d. Formato de respuestas

**Verificar** que el bot usa HTML tags (`<b>`, `<i>`, `<code>`) en vez de Markdown (`**`, `*`, `` ` ``):
```bash
grep "telegram.*send_message\|HTML" data/localforge.log | tail -5
```

---

## 23. Ontology / Knowledge Graph (Plan 42)

**Requisito**: `ONTOLOGY_ENABLED=true` (default)

### 23a. Entidades auto-registradas

```bash
sqlite3 data/localforge.db "SELECT type, name, ref_id FROM entities ORDER BY id DESC LIMIT 10;"
# Debe mostrar entidades tipo: memory, note, project, topic
```

### 23b. Relaciones

```bash
sqlite3 data/localforge.db "
  SELECT e1.name, r.relation_type, e2.name
  FROM entity_relations r
  JOIN entities e1 ON r.source_id = e1.id
  JOIN entities e2 ON r.target_id = e2.id
  LIMIT 10;
"
# Esperado: relaciones como memory→HAS_TOPIC→topic, project→CONTAINS→task
```

### 23c. Tool de búsqueda en grafo

| Mensaje | Esperado |
|---|---|
| `Qué sabés sobre el proyecto Backend API? Mostrá el grafo` | `search_knowledge_graph(...)` → entidades y relaciones conectadas |

### 23d. Backfill CLI

```bash
python scripts/backfill_ontology.py --db data/localforge.db
# Debe indexar entidades existentes sin duplicados
```

---

## 24. Data Provenance & Lineage (Plan 44)

**Requisito**: `PROVENANCE_ENABLED=true` (default)

### 24a. Audit log de mutaciones

```bash
sqlite3 data/localforge.db "
  SELECT action, entity_type, entity_id, actor, created_at
  FROM entity_audit_log ORDER BY id DESC LIMIT 10;
"
# Esperado: acciones CREATE/UPDATE/DELETE con actor (user, llm_flush, tool, etc.)
```

### 24b. Historial de versiones de memorias

```bash
sqlite3 data/localforge.db "
  SELECT memory_id, version, content, changed_by
  FROM memory_versions ORDER BY id DESC LIMIT 5;
"
```

### 24c. Tools de provenance

| Mensaje | Esperado |
|---|---|
| `De dónde salió la memoria sobre mi cumpleaños?` | `trace_data_origin(...)` → actor, timestamp, trace_id |
| `Mostrá el historial de cambios de mis memorias` | `get_entity_history(...)` → lista cronológica de mutaciones |

---

## 25. Deployment Maturity (Plan 46)

### 25a. Health endpoints

| Endpoint | Esperado |
|---|---|
| `GET /healthz` | `{"status": "ok"}` (liveness) |
| `GET /readyz` | `{"status": "ready", ...}` con checks de DB, Ollama, embeddings |

```bash
curl -s http://localhost:8000/healthz | jq .
curl -s http://localhost:8000/readyz | jq .
```

### 25b. Docker healthcheck

```bash
docker inspect localforge --format='{{.State.Health.Status}}'
# Esperado: "healthy"
```

### 25c. Compose profiles

```bash
docker compose --profile monitoring up -d  # Incluye Grafana/Prometheus si configurados
docker compose --profile dev up -d         # Incluye hot-reload
```

---

## 26. Operational Automation (Plan 47)

**Requisito**: `AUTOMATION_ENABLED=true` en `.env`

### 26a. Reglas builtin

```bash
sqlite3 data/localforge.db "SELECT name, description, enabled, cooldown_minutes FROM automation_rules;"
# Debe mostrar 5 reglas: project_inactive, guardrail_degraded, embeddings_desync, db_large, consolidation_pending
```

### 26b. Tools de automation

| Mensaje | Esperado |
|---|---|
| `Mostrá las reglas de automatización` | `list_automation_rules()` → tabla con nombre, tipo, estado |
| `Deshabilitá la regla guardrail_degraded` | `toggle_automation_rule("guardrail_degraded", false)` → confirmación |
| `Mostrá el log de automatización` | `get_automation_log()` → últimas ejecuciones |

### 26c. Evaluación periódica

```bash
grep "Automation.*triggered\|evaluate_rules\|rule.*cooldown" data/localforge.log | tail -10
# Esperado: evaluación cada AUTOMATION_INTERVAL_MINUTES (default 15)
```

### 26d. Acciones de self-healing

- **embeddings_desync**: si >10 memorias sin embedding → auto-backfill
- **db_large**: si DB >500MB → auto-VACUUM
- **consolidation_pending**: si >30 memorias viejas → auto-consolidación

```bash
grep "run_task\|backfill\|vacuum\|consolidat" data/localforge.log | tail -5
```

---

## 27. Metrics & Benchmarking (Plans 38-39)

### 27a. Agent stats

| Mensaje | Esperado |
|---|---|
| `/agent Mostrá estadísticas de los últimos 7 días` | `get_agent_stats(days=7)` → tool efficiency, token consumption, context quality |

### 27b. Latency stats

| Mensaje | Esperado |
|---|---|
| `Mostrá las latencias p50/p95 del sistema` | `get_latency_stats()` → percentiles por span |

### 27c. Dashboard HTML

```bash
python scripts/dashboard.py --db data/localforge.db --output reports/dashboard.html
# Abre reports/dashboard.html en browser — secciones: summary, guardrails, latencias, tools, tokens, context
```

### 27d. Baseline benchmark

```bash
python scripts/baseline.py --db data/localforge.db
# Muestra: trace count, avg latency, guardrail pass rate, eval dataset size, tool metrics
```

---

## 28. Token Accuracy (Plan 45)

### 28a. Calibración runtime

```bash
grep "token.*calibrat\|EMA.*update" data/localforge.log | tail -5
# Esperado: calibración periódica basada en respuestas reales de Ollama
```

### 28b. Estimación de contexto

```bash
grep "token_breakdown\|context_budget\|largest_section" data/localforge.log | tail -5
# Esperado: INFO con breakdown por sección (memories, notes, projects, etc.)
```

---

## Verificación de logs por área

```bash
# Tool calls generales
grep "Tool router\|Tool iteration\|Tool.*->" data/localforge.log | tail -20

# Agent sessions
grep "Agent round\|Agent session" data/localforge.log | tail -10

# request_more_tools (dynamic budget)
grep "request_more_tools\|per_cat" data/localforge.log | tail -10

# Web fetch
grep "Fetch mode\|puppeteer\|mcp-fetch" data/localforge.log | tail -5

# Memoria y embeddings
grep "embed\|semantic\|backfill\|Synced from" data/localforge.log | tail -10

# Security
grep "PolicyEngine\|AuditTrail\|HITL\|blocked_by_policy" data/localforge.log | tail -10

# Cron jobs
grep "cron\|CronTrigger\|Restored" data/localforge.log | tail -5

# Ontology / Knowledge Graph
grep "entity_registry\|ontology\|backfill.*entit" data/localforge.log | tail -5

# Data Provenance
grep "audit_log\|provenance\|memory_version" data/localforge.log | tail -5

# Automation
grep "Automation\|evaluate_rules\|rule.*triggered\|cooldown" data/localforge.log | tail -10

# Telegram
grep "telegram\|tg_\|TelegramClient" data/localforge.log | tail -5

# Token estimation
grep "token_breakdown\|context_budget\|calibrat" data/localforge.log | tail -5

# Errores
grep -i "error\|exception\|traceback" data/localforge.log | tail -20
```

---

## Checklist de release

Marcar cada ítem antes de declarar la rama lista para merge/release:

### Core
- [ ] Chat básico sin tools funciona
- [ ] Audio → transcripción → respuesta
- [ ] Imagen → descripción → respuesta
- [ ] Todos los comandos slash responden correctamente
- [ ] Rate limiter activo (logs confirmados)
- [ ] Graceful shutdown sin jobs perdidos

### Tools
- [ ] Calculadora: operaciones básicas + rechazo de código peligroso
- [ ] Datetime: hora actual + conversión de timezones
- [ ] Clima: ciudad válida + ciudad inválida (no crash)
- [ ] Búsqueda web: retorna resultados
- [ ] Notas: crear, listar, buscar semántico, borrar
- [ ] Recordatorios one-shot: crear + entrega puntual

### Memoria
- [ ] `/remember` → aparece en MEMORY.md
- [ ] Edición manual de MEMORY.md → sincroniza a DB
- [ ] Búsqueda semántica: memoria relevante inyectada según pregunta
- [ ] `/clear` → snapshot creado en `data/memory/snapshots/`
- [ ] Backfill de embeddings en boot (logs confirmados)

### Proyectos
- [ ] Crear proyecto + tareas
- [ ] Ver progreso + marcar tareas como done
- [ ] Notas de proyecto con búsqueda semántica
- [ ] Archivar proyecto con resumen automático

### Agent Mode
- [ ] Sesión inicia en background, respuesta inmediata al usuario
- [ ] Agente ejecuta múltiples tool calls en secuencia
- [ ] Persistencia JSONL: sesión guardada en `data/agent_sessions/`
- [ ] Cancelación de sesión funciona
- [ ] Loop detection activo (si aplica)

### Shell + Git (requiere `AGENT_WRITE_ENABLED=true`)
- [ ] `run_command("pytest")` → output correcto
- [ ] Comandos peligrosos (rm, sudo) bloqueados por denylist
- [ ] Shell operators (pipe, &&) → ASK (HITL)
- [ ] `git_create_branch` + `git_commit` funcionan
- [ ] `preview_patch` muestra diff sin modificar archivos

### Cron Jobs
- [ ] `create_cron` persiste en DB y registra en APScheduler
- [ ] Cron sobrevive restart del container (logs: "Restored N cron jobs")
- [ ] `delete_cron` elimina de DB y del scheduler

### Web Fetch
- [ ] URL en mensaje → categoría "fetch" forzada automáticamente
- [ ] Puppeteer activo: retorna contenido real de la página
- [ ] Fallback a mcp-fetch cuando Puppeteer no disponible

### Seguridad
- [ ] `data/security_policies.yaml` existe con reglas
- [ ] `data/audit_trail.jsonl` crece con cada tool call
- [ ] HITL: bot solicita aprobación para acciones flaggeadas

### Dynamic Tool Budget
- [ ] Multi-categoría (projects + github): ambas representadas en el tool set
- [ ] `request_more_tools` aparece siempre en la lista de tools disponibles

### Eval
- [ ] `trace_scores` contiene scores de guardrails para respuestas recientes
- [ ] Reacciones (👍/👎) guardan scores con `source='user'`
- [ ] `eval_dataset` acumula entradas (failure + golden_candidate)

### Prompt Engineering (Plan 32)
- [ ] `/prompts` lista prompts activos
- [ ] `/approve-prompt` activa nueva versión con score advisory
- [ ] `prompt_versions` tabla con historial

### Telegram (Plan 35)
- [ ] Mensajes de texto recibidos y respondidos via Telegram
- [ ] Recordatorios enrutados correctamente por prefijo `tg_`
- [ ] Formato HTML (no Markdown) en respuestas Telegram

### Ontology (Plan 42)
- [ ] Entidades auto-registradas en tabla `entities`
- [ ] Relaciones creadas en `entity_relations`
- [ ] `search_knowledge_graph` retorna grafo conectado
- [ ] Backfill CLI funciona sin duplicados

### Data Provenance (Plan 44)
- [ ] `entity_audit_log` registra CREATE/UPDATE/DELETE con actor
- [ ] `memory_versions` mantiene historial
- [ ] `trace_data_origin` y `get_entity_history` tools funcionan

### Deployment Maturity (Plan 46)
- [ ] `/healthz` retorna 200
- [ ] `/readyz` retorna status con checks de DB y Ollama
- [ ] Docker healthcheck muestra "healthy"

### Operational Automation (Plan 47)
- [ ] 5 reglas builtin seeded en DB al startup
- [ ] `list_automation_rules` / `toggle_automation_rule` / `get_automation_log` tools funcionan
- [ ] Evaluación periódica corre según intervalo configurado
- [ ] Cooldown previene re-triggers prematuros

### Metrics & Token Accuracy (Plans 38-39, 45)
- [ ] `get_agent_stats` retorna métricas de tools, tokens, context
- [ ] `get_latency_stats` retorna percentiles
- [ ] `scripts/dashboard.py` genera HTML válido
- [ ] Token breakdown logueado en cada request

### Graceful degradation
- [ ] Sin nomic-embed-text: app funciona con fallback
- [ ] Sin fetch servers: LLM informa sin crash
- [ ] Features opcionales (ontology, provenance, automation, telegram) se desactivan sin crash
- [ ] `make check` pasa: 0 errores de lint, typecheck, tests

---

## Troubleshooting rápido

| Síntoma | Causa probable | Acción |
|---|---|---|
| `Tool not found: X` | Tool no registrado al boot | Verificar logs de arranque, `Registered tool: X` |
| LLM presenta plan en lugar de ejecutar | 0 tools disponibles para la categoría | Verificar `select_tools` en logs |
| Agent loop no inicia | `AGENT_WRITE_ENABLED` no seteado | Agregar a `.env` + restart |
| Cron no se dispara | Timezone incorrecto o cron expr inválida | `list_crons` via WA, verificar expr |
| MCP connection refused | `npx` no disponible en container | `docker compose exec localforge which npx` |
| Sin embeddings en vec_memories | nomic-embed-text no descargado | `ollama pull nomic-embed-text` dentro del container |
| `think` visible en respuestas con tools | Bug: `think=True` con tools activo | Verificar `chat_with_tools()` en `llm/client.py` |
| HITL no llega por WA | Token de WA expirado o número incorrecto | Verificar `.env` y logs de WhatsApp client |
| Hash chain roto en audit trail | Corrupción del JSONL | Investigar; NO borrar el archivo (evidencia) |
| Automation rules no se disparan | Cooldown activo o `AUTOMATION_ENABLED=false` | Verificar `last_triggered_at` y cooldown en DB |
| Telegram no recibe mensajes | Token inválido o webhook no registrado | Verificar `TELEGRAM_BOT_TOKEN` y logs de startup |
| Ontology backfill lento | Muchas entidades existentes | Normal en primera ejecución, idempotente |
| Provenance audit log vacío | `PROVENANCE_ENABLED=false` | Activar en `.env` + restart |
| Token budget WARNING >80% | Contexto demasiado largo | Revisar `memory_similarity_threshold`, reducir `history_verbatim_count` |
| `/healthz` retorna 503 | App no terminó de iniciar | Esperar warmup completo, verificar logs |
