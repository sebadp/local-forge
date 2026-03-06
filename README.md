# WasAP

Asistente personal de WhatsApp con LLM local via Ollama. Privado, gratuito, dockerizado.

```
Tu celular ──► WhatsApp ──► Meta Cloud API ──► ngrok ──► FastAPI ──► Ollama (local)
                                                              │
                                                           SQLite
```

## Qué hace

- Recibe mensajes de WhatsApp via la API oficial de Meta (sin riesgo de ban)
- Los procesa con un LLM local corriendo en Ollama
- Mantiene historial de conversación persistente en SQLite
- Sistema de memorias avanzado: el asistente recuerda datos entre sesiones
- Memoria en 3 capas: semántica (hechos), episódica reciente (daily logs), episódica histórica (snapshots)
- **Búsqueda semántica**: embeddings locales (nomic-embed-text) + sqlite-vec — solo inyecta memorias y notas relevantes al contexto
- **MEMORY.md bidireccional**: editá el archivo a mano y los cambios se sincronizan a SQLite automáticamente
- Extracción automática de hechos y eventos antes de compactar historial
- Resumen automático de conversaciones largas
- Comandos interactivos via `/slash`
- **Skills con tool calling**: el asistente puede consultar el clima, calcular, tomar notas, decir la hora
- Audio entrante (transcripción local con Whisper) e imágenes (vision con llava)
- Formato WhatsApp, splitting de mensajes largos, rate limiting
- Cero costo de operación

## Quickstart

```bash
# 1. Configurar
cp .env.example .env
# Editar .env con tus credenciales (ver SETUP.md para detalle)

# 2. Levantar
docker compose up -d

# 3. Descargar modelos
docker compose exec ollama ollama pull qwen3:8b
docker compose exec ollama ollama pull llava:7b
docker compose exec ollama ollama pull nomic-embed-text

# 4. Configurar webhook en Meta Developer Portal
#    Callback URL: https://tu-dominio-ngrok/webhook
```

Ver [SETUP.md](SETUP.md) para la guía completa paso a paso.

## Comandos

| Comando | Descripción |
|---------|-------------|
| `/remember <dato>` | Guardar información importante |
| `/forget <dato>` | Olvidar un recuerdo guardado |
| `/memories` | Listar todos los recuerdos |
| `/clear` | Guardar snapshot + borrar historial |
| `/review-skill [nombre]` | Revisar skills y MCP servers instalados |
| `/help` | Mostrar comandos disponibles |

Las memorias persisten entre reinicios y se inyectan automáticamente en el contexto del LLM.

## Skills

El asistente tiene capacidades más allá de conversar, usando tool calling nativo de qwen3:

| Skill | Tools | Descripción |
|-------|-------|-------------|
| `datetime` | `get_current_datetime`, `convert_timezone` | Hora actual, conversión de zonas horarias |
| `calculator` | `calculate` | Cálculos matemáticos (evaluación segura via AST) |
| `weather` | `get_weather` | Clima actual y pronóstico via wttr.in |
| `notes` | `save_note`, `list_notes`, `search_notes`, `delete_note` | Notas persistentes en SQLite |
| `selfcode` | `get_version_info`, `read_source_file`, `list_source_files`, `get_runtime_config`, `get_system_health`, `search_source_code`, `get_skill_details` | Auto-inspección: versión, código fuente, configuración y salud del sistema |
| `expand` | `search_mcp_registry`, `get_mcp_server_info`, `install_from_smithery`, `install_mcp_server`, `remove_mcp_server`, `list_mcp_servers`, `preview_skill_from_url`, `install_skill_from_url`, `reload_capabilities` | Auto-expansión: buscar e instalar MCP servers (Smithery) y skills desde URLs en runtime |

Los skills se definen con archivos `SKILL.md` en `skills/`. Para agregar un skill nuevo: crear una carpeta con `SKILL.md` + registrar los handlers en Python.

## Arquitectura

```
app/
├── main.py                  # FastAPI app, lifespan, wiring
├── config.py                # Pydantic Settings (.env)
├── models.py                # Modelos de datos (ChatMessage, Memory, Note, etc.)
├── dependencies.py          # FastAPI dependency injection
│   └── splitter.py          # Split de mensajes largos
├── memory/
│   ├── markdown.py          # Sync SQLite <-> MEMORY.md (bidireccional)
│   ├── watcher.py           # File watcher (watchdog) para edición manual de MEMORY.md
│   ├── daily_log.py         # Daily logs + session snapshots
│   └── consolidator.py      # Dedup/merge de memorias via LLM
├── mcp/
│   └── manager.py           # McpManager (MCP server connections, hot-add/remove)
├── tracing/
│   ├── context.py           # ContextVars locales para aislar asincronismo
│   └── recorder.py          # Envia spans y scores a Langfuse & SQLite (Best-effort)
└── health/
    └── router.py            # GET /health

skills/                      # Definiciones de skills (SKILL.md)
├── datetime/SKILL.md
├── calculator/SKILL.md
├── weather/SKILL.md
├── notes/SKILL.md
├── selfcode/SKILL.md
└── expand/SKILL.md
```

### Stack

| Componente | Tecnología |
|------------|------------|
| Servidor | Python 3.11+ / FastAPI |
| WhatsApp | Cloud API oficial de Meta |
| Túnel | ngrok (free tier) |
| LLM | Ollama (local) |
| Embeddings | nomic-embed-text via Ollama (768 dims) |
| Base de datos | SQLite (WAL mode) + aiosqlite + sqlite-vec |
| Observabilidad | Langfuse (Server + PostgreSQL en Docker) |
| Contenedores | Docker + Docker Compose |

### Flujo de un mensaje

1. Mensaje llega de WhatsApp → Meta lo envía como webhook POST
2. ngrok tuneliza a `localhost:8000`
3. FastAPI valida firma HMAC, dedup atómico, extrae el mensaje
4. Si es audio → se transcribe con Whisper; si es imagen → se describe con llava
5. Si es un `/comando` → se ejecuta directamente, sin pasar por el LLM
6. Si es texto normal:
   - Se guarda en SQLite (con reply context si es respuesta a un mensaje)
   - Se computa el embedding del mensaje (una sola vez)
   - Se buscan memorias y notas relevantes via búsqueda semántica (KNN con sqlite-vec)
   - Se cargan memorias relevantes + notas relevantes + skills summary + resumen previo + historial reciente
   - Si hay skills disponibles → tool calling loop (LLM ↔ tools, max 5 iteraciones)
   - Si no hay skills → chat directo con Ollama
   - La respuesta se formatea (markdown→WhatsApp), se splitea si es larga, y se envía
   - Si el historial supera el threshold:
     - Se extraen hechos → memorias + eventos → daily log (pre-compaction flush)
     - Se auto-embeden los hechos extraídos
     - Se resume la conversación en background
     - Si se agregaron memorias nuevas, se consolidan (dedup via LLM)

### Base de datos

SQLite con 6 tablas + 2 tablas virtuales:

- **conversations** — una por número de teléfono
- **messages** — historial completo con `wa_message_id`
- **memories** — datos del usuario (soft-delete con `active` flag)
- **summaries** — resúmenes automáticos de conversaciones largas
- **notes** — notas del usuario (title, content) via skill de notas
- **processed_messages** — deduplicación atómica de webhooks (INSERT OR IGNORE)
- **vec_memories** — embeddings de memorias (sqlite-vec, float[768])
- **vec_notes** — embeddings de notas (sqlite-vec, float[768])

El archivo `data/MEMORY.md` es **bidireccional**: los cambios se sincronizan en ambas direcciones entre el archivo y SQLite.

### Memoria avanzada

El sistema de memoria tiene 3 capas:

| Capa | Archivo | Propósito |
|------|---------|-----------|
| **Semántica** | `data/MEMORY.md` + tabla `memories` | Hechos estables, preferencias |
| **Episódica Reciente** | `data/memory/YYYY-MM-DD.md` | Eventos y actividad del día |
| **Episódica Histórica** | `data/memory/snapshots/*.md` | Conversaciones guardadas al hacer `/clear` |

- **Búsqueda semántica**: solo las memorias y notas relevantes al mensaje se inyectan en el contexto (no todas)
- **Embeddings automáticos**: cada memoria y nota se embede al crearse; backfill al iniciar la app
- **MEMORY.md bidireccional**: editá el archivo → se sincroniza a SQLite (watchdog + inotify)
- **Daily logs**: se cargan automáticamente en el contexto del LLM (hoy + ayer)
- **Pre-compaction flush**: antes de borrar mensajes viejos, el LLM extrae hechos y eventos
- **Session snapshots**: `/clear` guarda los últimos 15 mensajes como snapshot
- **Consolidación**: el LLM revisa memorias periódicamente para eliminar duplicados

## Configuración

Variables de entorno (ver [.env.example](.env.example)):

| Variable | Descripción | Default |
|----------|-------------|---------|
| `WHATSAPP_ACCESS_TOKEN` | Token de la API de Meta | (requerido) |
| `WHATSAPP_PHONE_NUMBER_ID` | ID del número de WhatsApp | (requerido) |
| `WHATSAPP_VERIFY_TOKEN` | Token de verificación del webhook | (requerido) |
| `WHATSAPP_APP_SECRET` | Secret de la app de Meta | (requerido) |
| `ALLOWED_PHONE_NUMBERS` | Números permitidos (comma-separated) | (requerido) |
| `OLLAMA_BASE_URL` | URL de Ollama | `http://ollama:11434` |
| `OLLAMA_MODEL` | Modelo de chat | `qwen3:8b` |
| `VISION_MODEL` | Modelo de visión | `llava:7b` |
| `SYSTEM_PROMPT` | Prompt del sistema | (ver .env.example) |
| `CONVERSATION_MAX_MESSAGES` | Mensajes recientes en contexto | `20` |
| `DATABASE_PATH` | Ruta al archivo SQLite | `data/wasap.db` |
| `SUMMARY_THRESHOLD` | Mensajes antes de resumir | `40` |
| `SKILLS_DIR` | Directorio de skills | `skills` |
| `MEMORY_DIR` | Directorio para daily logs y snapshots | `data/memory` |
| `DAILY_LOG_DAYS` | Días de daily logs a cargar en contexto | `2` |
| `MEMORY_FLUSH_ENABLED` | Extraer hechos/eventos antes de compactar | `true` |
| `EMBEDDING_MODEL` | Modelo de embeddings Ollama | `nomic-embed-text` |
| `EMBEDDING_DIMENSIONS` | Dimensiones del vector | `768` |
| `SEMANTIC_SEARCH_ENABLED` | Habilitar búsqueda semántica | `true` |
| `SEMANTIC_SEARCH_TOP_K` | Cantidad de resultados semánticos | `10` |
| `MEMORY_FILE_WATCH_ENABLED` | Watch de MEMORY.md para sync bidireccional | `true` |
| `LOG_LEVEL` | Nivel de logging | `INFO` |
| `LANGFUSE_PUBLIC_KEY` | Llave pública de tu instancia local de Langfuse | `""` |
| `LANGFUSE_SECRET_KEY` | Llave privada de Langfuse | `""` |
| `LANGFUSE_HOST` | Host para telemetría | `http://localhost:3000` |

## Tests

```bash
# Con el venv local (requiere make dev primero)
make test

# O directo
.venv/bin/python -m pytest tests/ -v

# Con Docker
docker compose run --rm wasap python -m pytest tests/ -v
```

316 tests cubriendo: repository, conversation manager, comandos, parser, markdown memory, summarizer, daily logs, memory flush, session snapshots, consolidator, embeddings, sqlite-vec, semantic search, memory watcher, webhook (verificación, mensajes, comandos), health check, cliente Ollama, cliente WhatsApp, validación de firma, skill loader, skill registry, tool executor, tool router, MCP, y cada skill (datetime, calculator, weather, notes, search, news, scheduler, tools).

## Desarrollo local

```bash
# Instalar deps + pre-commit hooks
make dev

# Lint + typecheck + tests (todo junto antes de commitear)
make check

# Por separado
make lint       # ruff check
make format     # ruff format
make typecheck  # mypy app/
make test       # pytest
```

## CI/CD

Tres jobs en GitHub Actions (`.github/workflows/ci.yml`), trigger en push/PR a `master`:

| Job | Qué hace |
|-----|----------|
| `lint` | `ruff check` + `ruff format --check` |
| `typecheck` | `mypy app/` |
| `test` | `pytest` (depende de que pasen lint y typecheck) |

En PRs también corre `.github/workflows/ai-review.yml`:
- **AI Code Review**: analiza el diff con Gemini 2.0-flash y postea un comment
- **AI PR Description**: genera título y cuerpo del PR automáticamente (solo en PRs nuevos)

Requiere configurar el secret `GEMINI_API_KEY` en GitHub → Settings → Secrets → Actions.

## Docker

```bash
# Levantar todo
docker compose up -d

# Con GPU NVIDIA
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d

# Ver logs
docker compose logs -f wasap

# Rebuild después de cambios
docker compose up -d --build wasap
```

El container corre como usuario no-root (`appuser`, UID=1000) para que los archivos en `data/` tengan los permisos correctos. Si venís de una versión anterior donde `data/` quedó como root, corregí los permisos una vez con:

```bash
sudo chown -R $(id -u):$(id -g) data/
```

## Modelos recomendados

| Modelo | Params | RAM | Español | Uso |
|--------|--------|-----|---------|-----|
| `qwen3:8b` | 8B | 8GB | Excelente | Chat + tool calling |
| `llava:7b` | 7B | 8GB | Limitado | Vision (imágenes) |
| `nomic-embed-text` | 137M | 1GB | Si | Embeddings (768 dims) |
| `llama3.2:8b` | 8B | 8GB | Bueno | Chat alternativo |
| `llama3.2:3b` | 3B | 4GB | Aceptable | Hardware limitado |

Para más información acerca de cómo descargar, eliminar y configurar Ollama para el uso persistente de estos y otros modelos, puedes revisar la referencia en [docs/OLLAMA_MODELS.md](docs/OLLAMA_MODELS.md).

## Roadmap

- [x] **Fase 1**: Chat funcional (webhook, Ollama, historial en memoria)
- [x] **Fase 2**: Persistencia y memoria (SQLite, comandos, summarization)
- [x] **Fase 3**: UX y multimedia (audio, imágenes, formato WhatsApp, rate limiting, logging)
- [x] **Fase 4**: Skills y herramientas (tool calling, datetime, calculator, weather, notes) + reliability (dedup atómico, reply context, graceful shutdown)
- [x] **Fase 5**: Memoria avanzada (daily logs, pre-compaction flush, session snapshots, consolidación)
- [x] **Fase 6**: Búsqueda semántica (embeddings, sqlite-vec, RAG, MEMORY.md bidireccional)
- [x] **Fase 7**: CI/CD — pre-commit hooks (ruff, mypy, pytest) + GitHub Actions + AI code review
- [x] **Fase 8**: Observabilidad y Tracing (Langfuse, OpenTelemetry, métricas detalladas).
- [ ] **Fase 9**: Evaluación y Mejora Continua (Guardrails pre-entrega, Evaluación en 3 capas, Dataset vivo).

Ver [PRODUCT_PLAN.md](PRODUCT_PLAN.md) para el detalle de cada fase.
