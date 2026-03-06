# Testing Guide — Feature 35: Telegram Integration

## Tests unitarios (automatizados)

### `tests/test_telegram_parser.py`
Cubre `extract_telegram_messages()`:
- Texto, voz/audio, foto, `edited_message`
- Reply context (`reply_to_message_id`)
- Tipos no soportados → lista vacía
- Sin mensaje → lista vacía
- Que `message_id` y `timestamp` son strings

### `tests/test_telegram_md.py`
Cubre `markdown_to_telegram_html()`:
- Bold (`**`, `__`), italic (`*`, `_`)
- `_` dentro de palabras NO se convierte a italic
- Strikethrough (`~~`)
- Headers (`#`, `##`, `###`)
- Links con `&` en URL (escapado correctamente como `&amp;`)
- Inline code y code blocks
- Contenido de code blocks escapado (HTML special chars)
- Sin double-escaping (`& → &amp;` sólo una vez)
- Texto plano sin cambios

### `tests/test_platform_adapter.py`
Cubre `WhatsAppPlatformAdapter`:
- Satisface `PlatformClient` Protocol (`isinstance` check)
- `platform_name()` == `"whatsapp"`
- `format_text("**bold**")` → `"*bold*"`
- `send_typing_indicator` → `send_reaction(msg_id, to, "⏳")`
- Sin `message_id` → typing indicator no-op
- `remove_typing_indicator` con y sin `indicator_id`
- `mark_as_read` y `send_message` delegan al `WhatsAppClient`

### `tests/test_telegram_client.py`
Cubre `TelegramClient` (httpx mockeado):
- `platform_name()` == `"telegram"`
- `format_text("**bold**")` → `"<b>bold</b>"`
- `send_message` strips prefijo `tg_` del chat_id
- `send_message` usa `parse_mode=HTML`
- `send_message` retorna `None` en error de API
- `mark_as_read` y `remove_typing_indicator` son no-ops
- `send_typing_indicator` → `sendChatAction(action="typing")`
- `download_media` → `getFile` + GET del file
- `download_media` raises `ValueError` si `ok=False`
- `set_webhook` → POST con `url` y `secret_token`

---

## Tests de regresión (CI)

Correr antes de pushear:

```bash
make check  # lint + typecheck + test
```

Asegurarse de que los tests de WhatsApp existentes pasen sin cambios:
- `tests/test_webhook_incoming.py`
- `tests/test_webhook_commands.py`
- `tests/test_scheduler_tools.py`
- `tests/test_webhook_verification.py`

---

## Verificación end-to-end (manual)

### Pre-requisitos
1. Crear bot en [@BotFather](https://t.me/BotFather) → obtener `BOT_TOKEN`
2. Configurar `.env`:
   ```bash
   TELEGRAM_BOT_TOKEN=bot123456:ABC-...
   TELEGRAM_WEBHOOK_SECRET=secreto_random_32chars
   TELEGRAM_ENABLED=true
   # Opcional: ALLOWED_TELEGRAM_CHAT_IDS=tu_chat_id
   ```
3. Exponer localmente: `ngrok http 8000`
4. Setear `TELEGRAM_WEBHOOK_URL=https://xxx.ngrok.io` y reiniciar
   (o manualmente: `curl -X POST https://api.telegram.org/botTOKEN/setWebhook -d url=... -d secret_token=...`)

### Casos a verificar

| # | Acción | Resultado esperado |
|---|--------|--------------------|
| 1 | Enviar "hola" al bot | Respuesta en Telegram (texto) |
| 2 | Enviar una voice note | Transcripción + respuesta en Telegram |
| 3 | Enviar una imagen | Descripción (LLaVA) + respuesta contextual |
| 4 | `/remember clave=valor` | "Recordado ✅" |
| 5 | `/memories` | Lista con la memoria guardada en paso 4 |
| 6 | "recuérdame en 2 minutos prueba" | Recordatorio llega por Telegram con `⏰` |
| 7 | Enviar desde WhatsApp con misma config | WA sigue funcionando sin regresiones |
| 8 | Webhook con `secret_token` incorrecto | HTTP 403 |
| 9 | Mensaje desde chat_id no en whitelist | Ignorado silenciosamente |
| 10 | `TELEGRAM_ENABLED=false` | Endpoint `/telegram/webhook` retorna 200 vacío |

---

## Debug

Si el webhook no funciona:
```bash
# Verificar que el webhook está registrado
curl https://api.telegram.org/botTOKEN/getWebhookInfo
```

Si hay errores de parsing de mensajes, revisar logs JSON con:
```bash
docker logs -f <container> | jq 'select(.logger=="app.telegram.parser")'
```
