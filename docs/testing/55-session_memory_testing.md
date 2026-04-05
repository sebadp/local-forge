# Testing Manual: Session Memory — LLM-Powered Fact Extraction

> **Feature documentada**: [`docs/features/55-session_memory.md`](../features/55-session_memory.md)
> **Requisitos previos**: Container corriendo (`docker compose up -d`), Ollama disponible.

---

## Verificar que la feature está activa

```bash
docker compose logs -f localforge | grep -i "session_extract"
```

Confirmar: `session_extract_enabled: true` en config.

---

## Casos de prueba principales

| Mensaje / Acción | Resultado esperado |
|---|---|
| Enviar 10 mensajes variados que incluyan datos personales (ej: `soy developer de Python`, `prefiero respuestas cortas`, `trabajo en una fintech`) | Después del mensaje 10, se dispara extracción en background. Logs muestran `_run_session_extraction` |
| Verificar memorias después de la extracción | Nuevas memorias con categorías: preference, personal, technical |
| Enviar más mensajes mencionando algo temporal (ej: `mañana tengo un deploy importante`) | Categoría `temporal` extraída |
| Corregir un dato previo (ej: `en realidad no soy dev Python, soy de Go`) | Categoría `correction` extraída |

---

## Edge cases y validaciones

| Escenario | Resultado esperado |
|---|---|
| Menos de N mensajes enviados | No se triggerea extracción (counter no llega) |
| LLM retorna JSON inválido | Se loguea el error, no se guardan facts, no crashea |
| Server reinicia | Counter in-memory se resetea — worst case es una extracción extra |
| `SESSION_EXTRACT_ENABLED=false` | No se registra el trigger, nunca corre |
| Mensajes repetidos o sin contenido útil | LLM retorna lista vacía de facts — no-op |

---

## Verificar en logs

```bash
# Counter de mensajes
docker compose logs -f localforge 2>&1 | grep -i "should_extract"

# Extracción ejecutándose
docker compose logs -f localforge 2>&1 | grep -i "session_extract\|_run_session_extraction"

# Facts extraídos
docker compose logs -f localforge 2>&1 | grep -i "extracted.*fact"
```

---

## Queries de verificación en DB

```bash
# Ver memorias con categoría (las extraídas por session memory tienen prefijo de categoría)
sqlite3 data/localforge.db "SELECT id, content, created_at FROM memories ORDER BY created_at DESC LIMIT 20;"

# Contar memorias recientes (post-extracción)
sqlite3 data/localforge.db "SELECT COUNT(*) FROM memories WHERE created_at > datetime('now', '-1 hour');"
```

---

## Verificar interacción con otros sistemas

| Sistema | Verificación |
|---|---|
| **fact_extractor (regex)** | Después de la extracción, los regex facts siguen funcionando normalmente en cada mensaje |
| **Auto-Dream** | Las memorias creadas por session memory se consolidan en el próximo dream |
| **MEMORY.md** | Nuevas memorias aparecen en el archivo después del sync |

---

## Tests automatizados

```bash
.venv/bin/python -m pytest tests/test_session_extractor.py -v
# 17 tests: counter, extraction, parsing, categories, error handling
```

---

## Troubleshooting

| Problema | Causa probable | Solución |
|---|---|---|
| Extracción nunca se ejecuta | `SESSION_EXTRACT_ENABLED=false` o interval muy alto | Verificar `.env`, bajar `SESSION_EXTRACT_INTERVAL` |
| Facts no se guardan | LLM retorna JSON inválido | Revisar logs, verificar que qwen3.5:9b responde correctamente |
| Memorias duplicadas | Extracción genera facts que ya existen | El LLM recibe "known facts" — si persiste, revisar prompt |

---

## Variables relevantes para testing

| Variable (`.env`) | Valor de test | Efecto |
|---|---|---|
| `SESSION_EXTRACT_ENABLED` | `true` | Activa/desactiva |
| `SESSION_EXTRACT_INTERVAL` | `3` (para testing, default 10) | Cada cuántos mensajes correr |
