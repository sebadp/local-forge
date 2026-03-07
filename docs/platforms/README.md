# Guía de Plataformas — Cómo agregar un nuevo canal de mensajería

LocalForge soporta múltiples plataformas de mensajería a través de una capa de abstracción
basada en el patrón **Adapter + Protocol**. Esta guía explica cómo funciona la arquitectura
y cómo agregar un canal nuevo (Discord, Slack, SMS, etc.).

---

## El patrón Adapter + Protocol

**Problema que resuelve:** la lógica de procesamiento de mensajes (RAG, tool calling, memoria,
comandos) es idéntica sin importar el canal. Lo que cambia entre plataformas es:

- Cómo llega el mensaje (webhook POST con estructura distinta)
- Cómo se envía la respuesta (API distinta)
- Cómo se formatea el texto (HTML, Markdown, WhatsApp wikitext)
- Qué capacidades soporta (typing indicator, mark as read, etc.)

La solución: definir una interfaz común (`PlatformClient`) que cada plataforma implementa.
El núcleo del asistente (`process_message_generic`) solo conoce esta interfaz.

```
Webhook WhatsApp ──► WhatsAppPlatformAdapter ──┐
                                                ├──► process_message_generic ──► Ollama
Webhook Telegram ──► TelegramClient ────────────┘
```

---

## PlatformClient Protocol

Definido en `app/platforms/base.py`:

```python
from typing import Protocol, runtime_checkable

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

### Descripción de cada método

| Método | Descripción | Notas |
|--------|-------------|-------|
| `send_message` | Enviar texto al usuario. Puede splitear mensajes largos internamente. Retorna message_id o None. | Obligatorio |
| `download_media` | Descargar bytes de un media por su ID nativo. Retorna los bytes raw. | Obligatorio (puede raise NotImplementedError si no aplica) |
| `mark_as_read` | Marcar un mensaje como leído. | No-op si la plataforma no lo soporta |
| `send_typing_indicator` | Mostrar indicador de "escribiendo...". Retorna `None`. | No-op si no aplica |
| `remove_typing_indicator` | Remover el indicador de escritura. En Telegram es no-op (el typing auto-expira en ~5s). | No-op si no aplica |
| `format_text` | Convertir Markdown del LLM al formato nativo de la plataforma. | Obligatorio |
| `platform_name` | String identifier de la plataforma ("whatsapp", "telegram", ...). | Obligatorio |

---

## IncomingMessage

Definido en `app/platforms/models.py`:

```python
class Platform(StrEnum):
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"

class IncomingMessage(BaseModel):
    platform: Platform
    user_id: str          # Identificador único del usuario (ver convención de prefijos)
    message_id: str       # ID nativo del mensaje en la plataforma
    timestamp: str        # ISO 8601 o timestamp Unix como string
    text: str             # Texto del mensaje (transcripción si es audio)
    type: Literal["text", "audio", "image"]
    media_id: str | None = None              # ID nativo del media (audio/imagen)
    reply_to_message_id: str | None = None   # ID del mensaje al que responde
```

### Convención de `user_id`

El `user_id` se usa como clave primaria del usuario en toda la aplicación (conversaciones,
memorias, proyectos, etc.). Para evitar colisiones entre plataformas, se usa un prefijo:

| Plataforma | Prefijo | Ejemplo | Notas |
|------------|---------|---------|-------|
| WhatsApp | (ninguno) | `5491234567890` | Número E.164 sin `+` |
| Telegram | `tg_` | `tg_123456789` | chat_id numérico |
| Discord | `dc_` | `dc_12345678901234` | user_id Discord (snowflake) |
| Slack | `sl_` | `sl_U012AB3CD` | Slack user ID |
| SMS | `sms_` | `sms_5491234567890` | Número E.164 sin `+` |

> **Importante:** los prefijos determinan cómo el scheduler enruta recordatorios
> (`_send_reminder` en `app/skills/tools/scheduler_tools.py`). Al agregar una nueva
> plataforma hay que actualizar ese método.

---

## Implementaciones existentes como referencia

### WhatsApp vs Telegram — Comparación

| Método | WhatsApp (`WhatsAppPlatformAdapter`) | Telegram (`TelegramClient`) |
|--------|--------------------------------------|-----------------------------|
| `send_message` | `POST /messages` (text type) + split por `splitter.py` | `sendMessage` con `parse_mode=HTML` + split por `splitter.py` |
| `download_media` | GET `/{media_id}` → redirect → download bytes | `getFile` → `file_path` → GET download |
| `mark_as_read` | `POST /messages` con `{"status": "read"}` | No-op (Telegram no tiene read receipts en bots) |
| `send_typing_indicator` | `POST /messages` con `{"type": "typing"}` | `sendChatAction` con `action=typing` (auto-expira ~5s) |
| `remove_typing_indicator` | `POST /messages` con `{"type": "paused"}` | No-op (auto-expira) |
| `format_text` | `markdown_to_whatsapp()` → wikitext de WA | `markdown_to_telegram_html()` → HTML |
| `platform_name` | `"whatsapp"` | `"telegram"` |

### Formateo de texto

Cada plataforma tiene su propio módulo de formateo en `app/formatting/`:

- `markdown_to_wa.py` — Markdown → WhatsApp wikitext (`*bold*`, `_italic_`, `` `code` ``)
- `telegram_md.py` — Markdown → Telegram HTML (`<b>`, `<i>`, `<code>`)
  - **Regla crítica**: HTML escape PRIMERO (`&`, `<`, `>`), luego aplicar tags Markdown→HTML
    para evitar double-escape.

---

## Checklist para agregar una nueva plataforma

```
[ ] app/<platform>/__init__.py
[ ] app/<platform>/client.py          — implementa PlatformClient Protocol
[ ] app/<platform>/parser.py          — payload nativo → list[IncomingMessage]
[ ] app/<platform>/router.py          — FastAPI router + validación de firma
[ ] app/formatting/<platform>_md.py   — Markdown → formato nativo
[ ] app/config.py                     — variables con feature flag <platform>_enabled
[ ] app/main.py                       — init condicional + include_router condicional
[ ] app/dependencies.py               — get_<platform>_client()
[ ] app/skills/tools/scheduler_tools.py — agregar prefix en _send_reminder
[ ] app/skills/tools/selfcode_tools.py  — agregar tokens a _SENSITIVE (ocultar en logs)
[ ] tests/test_<platform>_parser.py
[ ] tests/test_<platform>_client.py
[ ] docs/features/NN-<platform>.md
[ ] docs/testing/NN-<platform>_testing.md
[ ] docs/platforms/README.md          — actualizar tabla de prefijos
[ ] SETUP.md                          — nueva sección de setup (Paso N)
[ ] .env.example                      — nuevas variables con comentarios
[ ] README.md                         — diagrama ASCII, stack table, config table, tests count, roadmap
```

### Detalles de cada archivo

**`app/<platform>/client.py`**
```python
class DiscordClient:
    def __init__(self, token: str, ...): ...

    async def send_message(self, to_id: str, text: str) -> str | None:
        # to_id es el channel_id o user_id de Discord
        ...

    def format_text(self, text: str) -> str:
        from app.formatting.discord_md import markdown_to_discord
        return markdown_to_discord(text)

    def platform_name(self) -> str:
        return "discord"
```

**`app/<platform>/parser.py`**
```python
def parse_discord_payload(payload: dict) -> list[IncomingMessage]:
    # Convertir el payload nativo de Discord a IncomingMessage
    # user_id debe usar el prefijo correcto: f"dc_{payload['author']['id']}"
    ...
```

**`app/<platform>/router.py`**
```python
router = APIRouter(prefix="/discord", tags=["discord"])

@router.post("/webhook")
async def discord_webhook(request: Request, ...):
    # 1. Validar firma
    # 2. Parsear payload → list[IncomingMessage]
    # 3. Para cada msg: BackgroundTasks.add_task(process_message_generic, msg, client, ...)
    ...
```

**`app/config.py`** — agregar settings:
```python
discord_enabled: bool = False
discord_bot_token: str = ""
discord_webhook_secret: str = ""
allowed_discord_user_ids: str = ""  # comma-separated, vacío = todos
```

**`app/main.py`** — init condicional:
```python
if settings.discord_enabled:
    discord_client = DiscordClient(settings.discord_bot_token)
    app.state.discord_client = discord_client
    app.include_router(discord_router)
```

**`app/skills/tools/scheduler_tools.py`** — routing por prefijo:
```python
async def _send_reminder(phone: str, message: str) -> None:
    if phone.startswith("tg_"):
        await _platform_clients["telegram"].send_message(phone, message)
    elif phone.startswith("dc_"):
        await _platform_clients["discord"].send_message(phone, message)
    else:
        await _platform_clients["whatsapp"].send_message(phone, message)
```

---

## Validación de firma

Cada plataforma tiene su mecanismo de autenticación de webhooks:

| Plataforma | Mecanismo | Header |
|------------|-----------|--------|
| WhatsApp | HMAC-SHA256 del body con `APP_SECRET` | `X-Hub-Signature-256` |
| Telegram | Token secreto fijo | `X-Telegram-Bot-Api-Secret-Token` |
| Discord | Ed25519 signature | `X-Signature-Ed25519` + `X-Signature-Timestamp` |
| Slack | HMAC-SHA256 con timestamp anti-replay | `X-Slack-Signature` |

**Siempre implementar validación de firma.** Sin ella, cualquiera puede enviar mensajes
falsos al bot. Ver `app/webhook/security.py` como referencia para WhatsApp.

---

## Notas de implementación

1. **Identificación de usuarios**: usar siempre el prefijo correcto. Una vez que hay datos en
   la DB para un usuario `tg_123`, cambiar el prefijo rompe toda su historia.

2. **Feature flags**: cada plataforma debe tener `<platform>_enabled=false` por defecto.
   Nunca activar automáticamente.

3. **Tokens sensibles**: agregar los tokens de la nueva plataforma a `_SENSITIVE` en
   `selfcode_tools.py` para que no aparezcan en logs ni en `get_runtime_config`.

4. **Typing indicator**: si la plataforma no tiene typing indicator nativo, implementar
   `send_typing_indicator` y `remove_typing_indicator` como no-ops (no lanzar excepciones).

5. **Formateo**: nunca devolver Markdown crudo. Siempre convertir al formato nativo antes
   de enviar. Cada plataforma renderiza diferente y los asteriscos de Markdown son ruido.

6. **Tests**: mockear siempre las llamadas HTTP a las APIs externas. Ver
   `tests/test_telegram_client.py` como referencia.
