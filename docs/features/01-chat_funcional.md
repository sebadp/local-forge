# Feature: Chat Funcional

> **Versión**: v1.0
> **Fecha de implementación**: 2025-12
> **Fase**: Fase 1
> **Estado**: ✅ Implementada

---

## ¿Qué hace?

El usuario envía un mensaje de texto por WhatsApp y recibe una respuesta generada por un LLM local (Ollama). El sistema mantiene un historial conversacional para dar respuestas contextuales dentro de una sesión.

---

## Arquitectura

```
[WhatsApp Cloud API]
        │ webhook POST /webhook
        ▼
[webhook/router.py — _handle_message]
        │
        ├─ parse payload (webhook/parser.py)
        ├─ validate signature (webhook/security.py)
        ├─ rate limiting (webhook/rate_limiter.py)
        │
        ▼
[ConversationManager — get/save messages]
        │
        ▼
[OllamaClient.chat(messages)]
        │
        ▼
[format reply → split → send via WhatsApp]
```

---

## Archivos clave

| Archivo | Rol |
|---|---|
| `app/main.py` | FastAPI app + lifespan, inyecta dependencias en `app.state` |
| `app/webhook/router.py` | Endpoint `/webhook`, orquesta `_handle_message` |
| `app/webhook/parser.py` | Extrae mensajes del payload de WhatsApp (text, audio, image) |
| `app/webhook/security.py` | Validación HMAC de firma de WhatsApp |
| `app/webhook/rate_limiter.py` | Rate limiter por número de teléfono |
| `app/llm/client.py` | `OllamaClient` — chat, embeddings, tool calling |
| `app/whatsapp/client.py` | `WhatsAppClient` — send_message, mark_as_read, send_reaction |
| `app/conversation/manager.py` | `ConversationManager` — historial por conversación |
| `app/config.py` | `Settings` — variables de entorno con pydantic-settings |
| `app/models.py` | `ChatMessage`, `WhatsAppMessage`, etc. |
| `app/formatting/markdown_to_wa.py` | Convierte markdown → formato WhatsApp |
| `app/formatting/splitter.py` | Divide mensajes largos en chunks ≤4096 chars |

---

## Walkthrough técnico: cómo funciona

1. **Webhook recibe POST** de WhatsApp Cloud API → `_handle_message` en `router.py`
2. **Parser** extrae texto, número del remitente, tipo de mensaje (`webhook/parser.py`)
3. **Security** valida la firma HMAC del payload (`webhook/security.py`)
4. **Rate limiter** verifica que el usuario no exceda el límite configurable
5. **ConversationManager** busca o crea la conversación para ese número de teléfono y carga los últimos N mensajes
6. **Se construye el contexto** con system prompt + historial + mensaje actual
7. **OllamaClient.chat()** envía al modelo local (qwen3.5:9b por defecto) y recibe respuesta
8. **Formato + split**: el markdown se convierte a formato WhatsApp y se divide si excede 4096 chars
9. **WhatsAppClient.send_message()** envía la respuesta (con reintentos internos)

---

## Cómo extenderla

- **Cambiar modelo**: variable `OLLAMA_MODEL` en `.env` (default: `qwen3.5:9b`)
- **Cambiar max mensajes en historial**: `CONVERSATION_MAX_MESSAGES` en `.env` (default: `20`)
- **Agregar otro canal**: Implementar un nuevo client similar a `WhatsAppClient`
- **System prompt custom**: `SYSTEM_PROMPT` en `.env` o via auto-evolución (Fase 8)

---

## Guía de testing

→ Ver [`docs/testing/01-chat_funcional_testing.md`](../testing/01-chat_funcional_testing.md)

---

## Decisiones de diseño

| Decisión | Alternativa descartada | Motivo |
|---|---|---|
| Ollama local | OpenAI API / Anthropic cloud | Costo cero, privacidad, sin dependencia de red externa |
| qwen3.5:9b como modelo default | llama3.1, mistral | Mejor rendimiento calidad/latencia en 8B params |
| Historial en memoria primero | SQLite desde el inicio | Iterar rápido en Fase 1, migrar a DB en Fase 2 |
| FastAPI lifespan pattern | startup events | Permite cleanup limpio (graceful shutdown) |
| HMAC signature validation | IP whitelisting | Estándar de WhatsApp Cloud API, más seguro |

---

## Gotchas y edge cases

- **El webhook de verificación** (GET /webhook) retorna el `hub.challenge` — es necesario para que Meta active el webhook. Si falla, ningún mensaje llega.
- **Mensajes largos se dividen** en chunks de ≤4096 chars. El split respeta saltos de línea para no cortar palabras.
- **`think: True`** solo se usa con qwen3 sin tools. Cuando hay tools en el payload, se desactiva para evitar XML tags en la respuesta.
- **Rate limiter** es por número de teléfono, no global. Un usuario puede enviar `rate_limit_max` mensajes en `rate_limit_window` segundos.

---

## Variables de configuración relevantes

| Variable (`config.py`) | Default | Efecto |
|---|---|---|
| `OLLAMA_MODEL` | `qwen3.5:9b` | Modelo principal para chat |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | URL del servidor Ollama |
| `CONVERSATION_MAX_MESSAGES` | `20` | Mensajes máximos en historial |
| `WHATSAPP_ACCESS_TOKEN` | (requerido) | Token de la app de WhatsApp |
| `WHATSAPP_PHONE_NUMBER_ID` | (requerido) | Phone number ID de la API |
| `WHATSAPP_VERIFY_TOKEN` | (requerido) | Token para verificar webhook |
| `RATE_LIMIT_MAX` | `30` | Mensajes máximos por ventana |
| `RATE_LIMIT_WINDOW` | `60` | Ventana del rate limiter (segundos) |
