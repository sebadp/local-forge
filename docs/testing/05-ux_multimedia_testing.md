# Testing Manual: UX y Multimedia

> **Feature documentada**: [`docs/features/05-ux_multimedia.md`](../features/05-ux_multimedia.md)
> **Requisitos previos**: Container corriendo, modelos `qwen3.5:9b` y `llava:7b` descargados en Ollama.

---

## Casos de prueba principales

| Mensaje / Acción | Resultado esperado |
|---|---|
| Enviar un audio corto (~10s) | El agente transcribe y responde al contenido del audio |
| Enviar una foto de un documento | El agente describe el contenido de la imagen |
| Enviar una foto con caption "¿Qué es esto?" | El agente responde a la pregunta sobre la imagen |
| Enviar un mensaje con **markdown** | La respuesta usa formato WhatsApp (bold, listas, etc.) |

---

## Edge cases y validaciones

| Escenario | Resultado esperado |
|---|---|
| Audio > 2 minutos | Transcripción más lenta pero funcional |
| Imagen corrupta o formato no soportado | Error graceful, no crash |
| Mensaje con solo un emoji | Respuesta corta y coherente |
| Respuesta del LLM > 4096 chars | Se divide en múltiples mensajes |

---

## Verificar en logs

```bash
# Transcripción de audio
docker compose logs -f localforge 2>&1 | grep -i "transcri\|whisper\|audio"

# Procesamiento de imagen
docker compose logs -f localforge 2>&1 | grep -i "image\|vision\|llava"

# Formato y split
docker compose logs -f localforge 2>&1 | grep -i "split\|format\|chunk"
```

---

## Troubleshooting

| Problema | Causa probable | Solución |
|---|---|---|
| Audio no se transcribe | faster-whisper no instalado o modelo no descargado | `pip install faster-whisper` y verificar `WHISPER_MODEL` |
| Imagen no se analiza | LLaVA no descargado en Ollama | `ollama pull llava:7b` |
| Formato roto en WhatsApp | Markdown no estándar en la respuesta del LLM | Revisar `markdown_to_wa.py` |
| Timeout en audio largo | Whisper en CPU con audio > 60s | Usar GPU o modelo más liviano (`tiny`) |
