# Adapter Pattern — Deep Dive Técnico

> Documento de preparación para prueba técnica. Explica los patrones de diseño
> implementados en LocalForge para soportar múltiples plataformas de mensajería
> con una base de código compartida.

---

## Problema

LocalForge comenzó como un bot de WhatsApp. Cuando se agregó soporte para
Telegram, surgió la pregunta: **¿cómo evitar duplicar la lógica de negocio
(RAG, tool calling, memoria, comandos) para cada plataforma?**

Lo que cambia entre plataformas es poco:

| Aspecto | WhatsApp | Telegram |
|---------|----------|----------|
| Cómo llega el mensaje | POST `/webhook` (Meta Cloud API) | POST `/telegram/webhook` (Bot API) |
| Cómo se envía la respuesta | REST a Graph API | REST a Bot API |
| Formato de texto | Wikitext (`*bold*`, `_italic_`) | HTML (`<b>`, `<i>`) |
| Typing indicator | Emoji reaction ⏳ | `sendChatAction(typing)` (auto-expira 5s) |
| Read receipts | `mark_as_read` | No existe en bots |
| Descarga de media | GET `/{media_id}` → redirect → bytes | `getFile` → `file_path` → GET bytes |

Lo que NO cambia: el 95% del flujo (conversación, LLM, tools, memoria, guardrails, tracing).

---

## Solución: Protocol + Adapter

Se usan dos patrones del GoF combinados:

### 1. Protocol (Structural Typing)

```python
# app/platforms/base.py
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

### 2. Adapter (WhatsApp)

WhatsApp ya tenía `WhatsAppClient` con una interfaz diferente. En lugar de
reescribirlo, se creó un adapter:

```python
# app/webhook/router.py
class WhatsAppPlatformAdapter:
    """Adapts WhatsAppClient to the PlatformClient Protocol."""

    def __init__(self, client: WhatsAppClient, current_message_id: str = "") -> None:
        self._client = client
        self._msg_id = current_message_id

    async def send_message(self, to_id: str, text: str) -> str | None:
        return await self._client.send_message(to_id, text)

    async def send_typing_indicator(self, to_id: str) -> None:
        # WhatsApp no tiene typing nativo — usa emoji reaction como indicador
        if self._msg_id:
            await self._client.send_reaction(self._msg_id, to_id, "⏳")

    async def remove_typing_indicator(self, to_id: str, indicator_id: str | None = None) -> None:
        # Remover el emoji = reaction vacía
        mid = indicator_id or self._msg_id
        if mid:
            await self._client.send_reaction(mid, to_id, "")

    def format_text(self, text: str) -> str:
        return markdown_to_whatsapp(text)

    def platform_name(self) -> str:
        return "whatsapp"
```

### 3. Implementación directa (Telegram)

`TelegramClient` se diseñó desde cero con la interfaz del Protocol:

```python
# app/telegram/client.py
class TelegramClient:
    async def send_message(self, chat_id: str, text: str) -> str | None:
        cid = chat_id.removeprefix("tg_")   # user_id llega con prefijo
        chunks = split_message(text)
        for chunk in chunks:
            resp = await self._http.post(
                self._url("sendMessage"),
                json={"chat_id": cid, "text": chunk, "parse_mode": "HTML"},
            )
            ...

    async def send_typing_indicator(self, chat_id: str) -> None:
        # Telegram tiene typing nativo que auto-expira en ~5s
        cid = chat_id.removeprefix("tg_")
        await self._http.post(
            self._url("sendChatAction"),
            json={"chat_id": cid, "action": "typing"},
        )

    async def remove_typing_indicator(self, chat_id: str, indicator_id: str | None = None) -> None:
        pass  # No-op: auto-expira

    def format_text(self, text: str) -> str:
        return markdown_to_telegram_html(text)

    def platform_name(self) -> str:
        return "telegram"
```

---

## Diagrama de flujo

```
                    ┌─────────────────────┐
                    │   Meta Cloud API    │
                    │   POST /webhook     │
                    └─────────┬───────────┘
                              │
                    ┌─────────▼───────────┐
                    │   WhatsApp Router   │
                    │   parse payload     │
                    │   dedup + rate limit│
                    └─────────┬───────────┘
                              │
              WhatsAppPlatformAdapter
              (adapta WhatsAppClient)
                              │
                    ┌─────────▼───────────────────────┐
                    │                                   │
                    │   process_message_generic()       │
                    │                                   │
                    │   - mark_as_read()                │
                    │   - send_typing_indicator()       │
                    │   - transcribe audio (si aplica)  │
                    │   - commands (/remember, etc.)    │
                    │   - LLM + tool calling            │
                    │   - guardrails                    │
                    │   - format_text()                 │
                    │   - send_message()                │
                    │   - remove_typing_indicator()     │
                    │                                   │
                    └─────────▲───────────────────────┘
                              │
                    TelegramClient
                    (implementa Protocol directo)
                              │
                    ┌─────────┴───────────┐
                    │   Telegram Router   │
                    │   parse payload     │
                    │   dedup + rate limit│
                    └─────────┬───────────┘
                              │
                    ┌─────────▼───────────┐
                    │   Telegram Bot API  │
                    │   POST /telegram/   │
                    │        webhook      │
                    └─────────────────────┘
```

---

## Decisiones de diseño y trade-offs

### ¿Por qué `Protocol` y no `ABC`?

| Aspecto | `Protocol` (structural typing) | `ABC` (nominal typing) |
|---------|-------------------------------|----------------------|
| Herencia requerida | No — duck typing | Sí — `class X(ABC)` |
| Retrocompatibilidad | Puede adaptar clases existentes sin modificarlas | Requiere reescribir o heredar |
| Runtime check | `isinstance(obj, PlatformClient)` con `@runtime_checkable` | `isinstance(obj, Base)` nativo |
| Flexibilidad | Cualquier objeto con los métodos correctos funciona | Solo subclases explícitas |
| Type checking | mypy verifica structural conformance | mypy verifica nominal |

**Decisión**: `Protocol` porque `WhatsAppClient` ya existía y no queríamos
modificarlo. El Adapter adapta su interfaz sin herencia.

**Trade-off**: el Protocol no fuerza implementación en tiempo de escritura (a
diferencia de `@abstractmethod`). Un método faltante solo se detecta en runtime
o con mypy, no al heredar. En la práctica, mypy + tests lo cubren.

### ¿Por qué `@runtime_checkable`?

Permite `isinstance(obj, PlatformClient)` en runtime para type guards y
assertions defensivas. Sin él, el Protocol solo funciona en type checking
estático (mypy). El costo es mínimo (un `__instancecheck__` más lento).

### ¿Por qué el Adapter vive en `webhook/router.py` y no en un archivo propio?

`WhatsAppPlatformAdapter` es un thin wrapper (~30 líneas) que solo se instancia
en el webhook handler. No tiene lógica propia — delega todo a `WhatsAppClient`.
Extraerlo a un archivo propio agregaría un nivel de indirección sin beneficio.
Si creciera (ej: rate limiting interno), se mueve a `app/whatsapp/adapter.py`.

### ¿Por qué `IncomingMessage` en vez de usar el payload nativo?

```python
# app/platforms/models.py
class IncomingMessage(BaseModel):
    platform: Platform          # enum: "whatsapp" | "telegram"
    user_id: str                # "5491234567890" | "tg_12345678"
    message_id: str             # namespaced: "tg_123_42" para Telegram
    timestamp: str
    text: str
    type: Literal["text", "audio", "image"]
    media_id: str | None = None
    reply_to_message_id: str | None = None
```

**Motivación**: desacoplar parsing del payload (plataforma-específico) del
procesamiento (plataforma-agnóstico). Cada plataforma tiene su parser:

- `app/webhook/parser.py` → `extract_messages(payload) → list[WhatsAppMessage]`
- `app/telegram/parser.py` → `extract_telegram_messages(payload) → list[IncomingMessage]`

### Identificación de usuarios entre plataformas

Un mismo asistente puede hablar con el mismo usuario por WhatsApp y Telegram.
Para evitar colisiones en la DB, los `user_id` llevan prefijo por plataforma:

| Plataforma | Prefijo | Ejemplo | Columna DB |
|------------|---------|---------|------------|
| WhatsApp | (ninguno) | `5491234567890` | `phone_number` |
| Telegram | `tg_` | `tg_6823940266` | `phone_number` |
| Discord | `dc_` | `dc_12345678901` | `phone_number` |

Esto permite zero schema migration: la columna `phone_number` acepta cualquier
string. Conversaciones, memorias, proyectos y recordatorios se vinculan por
este ID.

**Routing de recordatorios**: el scheduler identifica la plataforma por prefijo:

```python
# app/skills/tools/scheduler_tools.py
async def _send_reminder(phone: str, message: str) -> None:
    if phone.startswith("tg_"):
        await _platform_clients["telegram"].send_message(phone, message)
    else:
        await _platform_clients["whatsapp"].send_message(phone, message)
```

### No-ops para capacidades ausentes

Si una plataforma no soporta una capacidad (ej: Telegram no tiene read
receipts), el método es un no-op:

```python
async def mark_as_read(self, message_id: str) -> None:
    """No-op: Telegram has no read receipts."""
```

**Regla**: nunca lanzar `NotImplementedError` para capacidades opcionales.
El flujo principal llama `mark_as_read()` incondicionalmente — un raise
rompería el procesamiento de mensajes. Solo usar `raise` si el método es
verdaderamente requerido (ej: `send_message`).

---

## Principios SOLID aplicados

### S — Single Responsibility
- `TelegramClient`: solo habla con la API de Telegram.
- `telegram/parser.py`: solo convierte payloads → `IncomingMessage`.
- `telegram/router.py`: solo recibe webhooks, valida firma, dedup, rate limit.
- `process_message_generic()`: solo orquesta el flujo de procesamiento.

### O — Open/Closed
Agregar Discord no requiere modificar `process_message_generic()`. Solo crear:
`app/discord/client.py` (implementa Protocol) + `app/discord/parser.py` + router.

### L — Liskov Substitution
`WhatsAppPlatformAdapter` y `TelegramClient` son intercambiables en
`process_message_generic()`. Cualquier objeto que satisfaga el Protocol funciona.

### I — Interface Segregation
`PlatformClient` tiene 7 métodos, todos usados por `process_message_generic`.
No hay métodos "sobrantes". Si una plataforma futura necesita capacidades extras
(ej: reactions), se crea un Protocol extendido opcional.

### D — Dependency Inversion
`process_message_generic()` depende de la abstracción (`PlatformClient`), no
de implementaciones concretas (`WhatsAppClient`, `TelegramClient`). La inyección
ocurre en los routers:

```python
# WhatsApp router
adapter = WhatsAppPlatformAdapter(wa_client, msg.message_id)
await process_message_generic(msg=incoming, platform_client=adapter, ...)

# Telegram router
await process_message_generic(msg=msg, platform_client=telegram_client, ...)
```

---

## Preguntas tipo entrevista

### 1. "¿Por qué no usar herencia clásica?"

Porque `WhatsAppClient` ya existía con una interfaz distinta (usa `from_number`
en vez de `to_id`, typing indicator vía emoji reactions, etc.). Modificarlo
rompería backward compatibility. El Adapter permite adaptar sin tocar el
código original — classic GoF Adapter pattern.

### 2. "¿Cómo agregarías Discord?"

1. `app/discord/client.py` — implementar los 7 métodos del Protocol
2. `app/discord/parser.py` — payload Discord → `IncomingMessage` (user_id: `dc_<id>`)
3. `app/discord/router.py` — webhook + validación Ed25519
4. `app/formatting/discord_md.py` — Markdown → Discord markdown (similar a estándar)
5. `app/config.py` — `discord_enabled`, `discord_bot_token`
6. `app/main.py` — init condicional + include_router
7. `scheduler_tools.py` — agregar prefijo `dc_` en `_send_reminder`

Zero cambios en `process_message_generic()`, `executor.py`, `OllamaClient`, o
cualquier lógica de negocio.

### 3. "¿Qué pasa si una plataforma necesita un método que las demás no tienen?"

Ejemplo: Discord tiene "reactions" nativas que WhatsApp no tiene (WA usa emoji
hackeado). Opciones:

- **a) Método opcional en el Protocol**: agregar `async def send_reaction(...)`
  con default no-op. Rompe ISP si la mayoría de plataformas no lo usa.
- **b) Protocol extendido**: `class ReactionCapable(Protocol)` separado.
  `process_message_generic` hace `if isinstance(client, ReactionCapable)`.
- **c) Feature detection**: `if hasattr(client, 'send_reaction')`.

La opción (b) es la más limpia — preserva ISP y es type-safe.

### 4. "¿Cómo testeas esto?"

- **Unit tests del client**: mockear `httpx.AsyncClient`, verificar que se
  llamen los endpoints correctos con los parámetros correctos.
- **Unit tests del parser**: pasar payloads JSON reales → verificar campos
  de `IncomingMessage` (plataforma, user_id con prefijo, media_id, etc.)
- **Integration tests del router**: `TestClient` (sync) → POST al webhook
  con payload mockeado → verificar que se llama `process_message_generic`.
- **Protocol conformance**: mypy verifica que las clases satisfacen el Protocol.
  Opcionalmente, un test con `isinstance(client, PlatformClient)` gracias a
  `@runtime_checkable`.

### 5. "¿Cuál fue el bug más sutil de esta implementación?"

La deduplicación de mensajes. `try_claim_message()` retorna `True` si el
mensaje es duplicado (INSERT fue ignorada). El router de WhatsApp lo usaba
correctamente:

```python
if await repository.try_claim_message(msg.message_id):   # True = duplicado → skip
```

Pero el router de Telegram tenía la lógica invertida:

```python
if not await repository.try_claim_message(msg.message_id):  # BUG: skip mensajes nuevos
```

El efecto: todos los mensajes nuevos se descartaban como "duplicados" y los
duplicados reales se procesaban. Difícil de detectar porque los logs decían
"Duplicate message ignored" — parecía que Telegram re-enviaba webhooks.

**Lección**: cuando un método retorna un booleano, el nombre debe hacer obvia
la semántica. `try_claim_message` → `True` no es intuitivo. Un nombre como
`is_duplicate(msg_id)` hubiera prevenido el error.

### 6. "¿Cómo manejas el formateo de texto entre plataformas?"

Cada plataforma tiene su módulo en `app/formatting/`:

```
LLM genera Markdown estándar
        │
        ├── markdown_to_whatsapp()  → *bold*, _italic_ (wikitext)
        └── markdown_to_telegram_html() → <b>bold</b>, <i>italic</i>
```

El `format_text()` del Protocol abstrae esto: `process_message_generic` llama
`platform_client.format_text(response)` sin saber qué plataforma es.

**Gotcha de Telegram HTML**: hay que hacer HTML escape (`&`, `<`, `>`) ANTES
de aplicar las transformaciones Markdown → HTML. Si se hace después, los tags
`<b>`, `<i>` se escapean y aparecen como texto plano.

---

## Archivos clave (referencia rápida)

| Archivo | Responsabilidad |
|---------|----------------|
| `app/platforms/base.py` | `PlatformClient` Protocol (7 métodos) |
| `app/platforms/models.py` | `IncomingMessage`, `Platform` StrEnum |
| `app/webhook/router.py` | `WhatsAppPlatformAdapter` + `process_message_generic()` |
| `app/telegram/client.py` | `TelegramClient` (implementa Protocol) |
| `app/telegram/parser.py` | Telegram Update JSON → `IncomingMessage` |
| `app/telegram/router.py` | POST `/telegram/webhook` + dedup + whitelist |
| `app/formatting/markdown_to_wa.py` | Markdown → WhatsApp wikitext |
| `app/formatting/telegram_md.py` | Markdown → Telegram HTML |
| `app/formatting/splitter.py` | Split mensajes >4096 chars |
| `app/skills/tools/scheduler_tools.py` | Routing de recordatorios por prefijo `tg_` |
