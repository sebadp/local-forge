# Feature 35 — Telegram Integration (Multi-platform)

## Descripción

Soporte de Telegram en paralelo con WhatsApp. Ambas plataformas comparten toda la lógica de negocio (LLM, memoria, tools, comandos, tracing, guardrails, dataset curation, security layer).

**Testing guide:** [`docs/testing/35-telegram_testing.md`](../testing/35-telegram_testing.md)

---

## Arquitectura — Patrón Adapter + Protocol

```
Telegram Update  POST /telegram/webhook
    │
    ▼ app/telegram/parser.py
IncomingMessage(platform="telegram", user_id="tg_12345678", ...)
    │
    ▼ TelegramClient (implementa PlatformClient Protocol)
process_message_generic(msg: IncomingMessage, platform_client: PlatformClient, ...)
    │           ─────── lógica de negocio sin cambios ───────
    ├── platform_client.download_media(media_id)
    ├── platform_client.send_typing_indicator(user_id)
    ├── platform_client.format_text(reply)   ← telegram_md.py
    └── platform_client.send_message(user_id, text)

WhatsApp  POST /webhook  ─── sin cambios estructurales ───
    │
    ▼ webhook/parser.py (existente)
WhatsAppMessage → _wa_msg_to_incoming() → IncomingMessage
    │
    ▼ WhatsAppPlatformAdapter(wa_client, message_id)
process_message_generic(msg, platform_client)
```

---

## Archivos nuevos

| Archivo | Descripción |
|---------|-------------|
| `app/platforms/__init__.py` | Módulo vacío |
| `app/platforms/base.py` | `PlatformClient` Protocol (runtime_checkable) |
| `app/platforms/models.py` | `Platform` StrEnum + `IncomingMessage` Pydantic model |
| `app/telegram/__init__.py` | Módulo vacío |
| `app/telegram/client.py` | `TelegramClient` vía httpx |
| `app/telegram/parser.py` | Telegram Update → `IncomingMessage` |
| `app/telegram/router.py` | FastAPI `POST /telegram/webhook` |
| `app/formatting/telegram_md.py` | Markdown → HTML para `parse_mode=HTML` |

## Archivos modificados

| Archivo | Cambio |
|---------|--------|
| `app/config.py` | `telegram_bot_token`, `telegram_webhook_secret`, `allowed_telegram_chat_ids`, `telegram_enabled`, `telegram_webhook_url` |
| `app/webhook/router.py` | `WhatsAppPlatformAdapter`, `_wa_msg_to_incoming()`, refactor `_handle_message` → `PlatformClient`, nuevo `process_message_generic` |
| `app/skills/tools/scheduler_tools.py` | `_platform_clients` dict, `set_scheduler(**clients)`, `_send_reminder` enruta por prefijo `tg_` |
| `app/skills/tools/selfcode_tools.py` | `_SENSITIVE` incluye `telegram_bot_token`, `telegram_webhook_secret` |
| `app/main.py` | Init `TelegramClient` condicional, `set_scheduler(**clients)`, `app.include_router(telegram_router)` |
| `app/dependencies.py` | `get_telegram_client()` |

---

## PlatformClient Protocol

```python
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

---

## Identificación de usuarios

| Plataforma | `phone_number` / `user_id` en DB |
|-----------|-------------------------------|
| WhatsApp  | `"5491234567890"` (sin cambio) |
| Telegram  | `"tg_123456789"` (prefijo nuevo) |

Zero migración de schema. El prefijo `tg_` también es usado por `_send_reminder` en `scheduler_tools.py` para enrutar al cliente correcto.

---

## Telegram formatter (`telegram_md.py`)

Convierte Markdown → HTML para `parse_mode=HTML`.

Approach:
1. Proteger code blocks con placeholders (antes del HTML escaping)
2. Escapar `&` → `&amp;`, `<` → `&lt;`, `>` → `&gt;` en texto plano
3. Convertir markdown a tags HTML (`**`, `*`, `~~`, `#`, `[]()` no son HTML chars → sin conflicto)
4. Restaurar code blocks con su propio HTML escaping

| Markdown | HTML |
|----------|------|
| `**bold**` / `__bold__` | `<b>bold</b>` |
| `*italic*` / `_italic_` | `<i>italic</i>` |
| `` `code` `` | `<code>code</code>` |
| ` ```block``` ` | `<pre>block</pre>` |
| `~~strike~~` | `<s>strike</s>` |
| `# Header` | `<b>Header</b>` |
| `[text](url)` | `<a href="url">text</a>` |

---

## Scheduler multi-plataforma

`set_scheduler(scheduler, **clients)` acepta clientes nombrados:

```python
set_scheduler(scheduler, whatsapp=wa_client, telegram=tg_client)
```

`_send_reminder(user_id, message)` detecta la plataforma por prefijo:
- `tg_*` → busca `_platform_clients["telegram"]`
- otherwise → `_platform_clients["whatsapp"]`

---

## Variables de entorno

```bash
TELEGRAM_BOT_TOKEN=bot123456:ABC-...
TELEGRAM_WEBHOOK_SECRET=secreto_random_32chars
ALLOWED_TELEGRAM_CHAT_IDS=123456789,987654321  # vacío = todos permitidos
TELEGRAM_ENABLED=true
TELEGRAM_WEBHOOK_URL=https://tu-dominio.com    # opcional, auto-registra webhook
```

---

## Gotchas y decisiones de diseño

- **`process_message` backward compat**: la firma original de `process_message(msg: WhatsAppMessage, ..., wa_client: WhatsAppClient, ...)` se preserva intacta — es un thin wrapper que crea el adapter y llama `process_message_generic`.
- **`_handle_message` no cambia nombre**: sigue siendo un `_` (interno). Solo cambia la firma de tipos.
- **`CommandContext.wa_client`**: se pasa `platform_client` como `wa_client` (campo `Any`) — el agent loop usa `wa_client.send_message()` que está en ambos clients.
- **Markdown en agent loop**: `app/agent/loop.py` todavía usa `markdown_to_whatsapp()` directamente en sus mensajes de estado. Para Telegram estos mensajes se entregarán sin formateo HTML. Fuera del scope de este plan.
- **Typing indicator en Telegram**: `sendChatAction(typing)` expira automáticamente en ~5s. `remove_typing_indicator` es no-op.
- **Security layer (Plan 34)**: `PolicyEngine` y `AuditTrail` aplican automáticamente a todos los tool calls incluyendo Telegram — sin cambios adicionales.
- **`StrEnum` en Python 3.11+**: `Platform` hereda de `StrEnum` (disponible en Python 3.11) en vez de `str, Enum` — evita el warning de ruff UP042.
