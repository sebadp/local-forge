# Testing Manual: Chat Funcional

> **Feature documentada**: [`docs/features/01-chat_funcional.md`](../features/01-chat_funcional.md)
> **Requisitos previos**: Container corriendo (`docker compose up -d`), Ollama con `qwen3.5:9b` descargado.

---

## Verificar que la feature está activa

```bash
docker compose logs -f localforge | head -40
```

Confirmar:
- `Ollama models warmed up`
- `Application startup complete`

---

## Casos de prueba principales

| Mensaje | Resultado esperado |
|---|---|
| `Hola` | Respuesta amigable en español |
| `¿Qué hora es?` (sin tools) | El LLM responde con una estimación o dice que no tiene acceso a la hora |
| Mensaje largo (>500 chars) | Respuesta coherente que tiene en cuenta todo el contenido |
| `What's your name?` | Responde en el idioma del usuario (inglés) |

---

## Edge cases y validaciones

| Escenario | Resultado esperado |
|---|---|
| Enviar 2 mensajes idénticos rápidamente | Solo procesa uno (dedup atómico) |
| Enviar un mensaje vacío (solo espacios) | WhatsApp no lo envía, pero si llega: respuesta genérica |
| Rate limit excedido | Mensaje de "demasiados mensajes, intenta de nuevo en X segundos" |
| Ollama caído | Respuesta de error: "Sorry, I'm having trouble..." |

---

## Verificar en logs

```bash
# Mensaje recibido y procesado
docker compose logs -f localforge 2>&1 | grep -i "incoming\|received\|handle_message"

# Llamada LLM
docker compose logs -f localforge 2>&1 | grep -i "ollama\|chat"

# Errores
docker compose logs -f localforge 2>&1 | grep -i "error\|exception"
```

---

## Verificar graceful degradation

1. Detener Ollama: `docker compose stop ollama`
2. Enviar un mensaje por WhatsApp
3. Verificar que responde con mensaje de error genérico (no crash)
4. Reiniciar: `docker compose start ollama`

---

## Troubleshooting

| Problema | Causa probable | Solución |
|---|---|---|
| Webhook no recibe mensajes | Token de verificación incorrecto o webhook no registrado en Meta | Verificar `WHATSAPP_VERIFY_TOKEN` y re-registrar webhook |
| Respuestas en inglés a pesar de escribir en español | System prompt no tiene instrucción de idioma | Verificar `SYSTEM_PROMPT` en `.env` |
| Timeout en respuestas | Modelo muy grande para el hardware | Usar modelo más ligero (qwen3:4b) o aumentar timeout |
| "Could not connect to ollama" | Container de Ollama no está en la misma red Docker | Verificar `docker compose` y `OLLAMA_BASE_URL` |

---

## Variables relevantes para testing

| Variable (`.env`) | Valor de test | Efecto |
|---|---|---|
| `OLLAMA_MODEL` | `qwen3.5:9b` | Modelo a usar |
| `RATE_LIMIT_MAX` | `5` (para testear) | Baja el límite para probar rate limiting |
| `LOG_LEVEL` | `DEBUG` | Muestra más detalle en logs |
