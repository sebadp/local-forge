# PRD: Performance Optimization — Reducir latencia del critical path

## 1. Objetivo y Contexto

LocalForge procesa mensajes de WhatsApp/Telegram en tiempo real. El pipeline actual
(parse → embed → context build → LLM → guardrails → send) tiene una latencia end-to-end
de 1.5-4 segundos para mensajes simples y 3-8 segundos con tool calling.

Un análisis exhaustivo del codebase identificó **~30 bottlenecks** distribuidos en 4 capas:
webhook router, LLM client/executor, database/memory, y startup/MCP/formatting.

**Problema**: Varias operaciones independientes se ejecutan secuencialmente cuando podrían
paralelizarse. Faltan índices en tablas frecuentemente consultadas. Operaciones CPU-bound
bloquean el event loop de asyncio. El startup bloquea aceptación de requests con tareas
no críticas.

**Objetivo**: Reducir la latencia percibida por el usuario en un 20-40%, mejorar el
startup time en 50%, y eliminar bloqueos del event loop — sin cambiar la arquitectura
del sistema ni el modelo LLM.

## 2. Alcance

### In Scope

**Tier 1 — Quick Wins** (cada mensaje se beneficia):
- Paralelizar queries en `get_windowed_history()`
- Agregar índices faltantes en `summaries` y `memories`
- Agregar PRAGMAs de SQLite (`mmap_size`, `busy_timeout`)
- Mover `langdetect.detect()` a `asyncio.to_thread()`
- Pre-compilar regexes en `client.py`, `router.py`, `whatsapp.py`

**Tier 2 — Impacto alto** (esfuerzo medio):
- Paralelizar o consolidar N+1 de project progress
- Mover embedding backfill a post-yield background task
- Batch commits en embedding backfill
- Optimizar compaction de tool outputs (priorizar JSON extraction)
- Combinar flush + summarize en un solo LLM call

**Tier 3 — Menores** (polish):
- `INSERT OR IGNORE` en `get_or_create_conversation`
- Reducir MCP connect timeout a 10s
- LRU en `_conv_id_cache`
- Cache `_tools_by_server` en MCP manager

### Out of Scope

- Cambiar de SQLite a PostgreSQL (innecesario para nuestro throughput)
- Cambiar el modelo LLM (qwen3:8b es fixed)
- Refactorizar el image flow (separado, menor prioridad)
- Connection pooling (SQLite es embebido, no aplica)
- Caching de respuestas LLM (cada mensaje es único)
- Async driver alternativo a aiosqlite (riesgo alto, beneficio marginal)

## 3. Casos de Uso Críticos

### 3.1 Mensaje simple "hola"

**Antes**: 1.5-3s (embed query → fetch memories secuencial → fetch summary secuencial →
classify → LLM → guardrails).

**Después**: 1.0-2.0s. `get_windowed_history` paraleliza `get_recent_messages` y
`get_latest_summary`. Índices en `memories` y `summaries` aceleran lookups.
`langdetect` no bloquea event loop.

### 3.2 Mensaje con tools y proyectos activos

**Antes**: 3-8s. N+1 en project progress (5 queries secuenciales). Compaction de tool
outputs puede invocar 3 LLM calls paralelos que Ollama serializa.

**Después**: 2-5s. Project progress resuelto con un solo query JOIN. Compaction prioriza
JSON extraction sin LLM.

### 3.3 Cold start / restart del servicio

**Antes**: 5-10s (backfill de 500 embeddings bloquea startup).

**Después**: 2-4s (backfill en background post-yield; warmup ya es paralelo).

### 3.4 Carga concurrente (5 usuarios simultáneos)

**Antes**: `langdetect` serializa requests (bloquea event loop 20-100ms por usuario).
Thread pool se satura con daily log writes.

**Después**: `langdetect` en thread separado. Event loop libre para despachar requests
concurrentemente.

## 4. Restricciones Arquitectónicas

- **Zero-downtime**: Los cambios son backward-compatible. No requieren migración de datos.
  Los nuevos índices se crean con `IF NOT EXISTS`.
- **Fail-open**: Todas las optimizaciones mantienen el patrón fail-open existente. Si un
  gather falla parcialmente, los resultados exitosos se usan normalmente.
- **Sin dependencias nuevas**: Todo se resuelve con stdlib de Python y features existentes
  de SQLite. No se agrega ninguna librería.
- **Measurability**: Cada cambio debe ser verificable con los spans de tracing existentes
  o con un benchmark simple (`time` / logging de duración).

## 5. Métricas de éxito

| Métrica | Baseline | Target |
|---------|----------|--------|
| Latencia p50 (mensaje simple) | ~2s | < 1.5s |
| Latencia p50 (con tools) | ~5s | < 3.5s |
| Startup time (hasta health check OK) | ~7s | < 3s |
| Event loop blocking time | 20-100ms/msg | < 5ms/msg |
| Queries SQLite por mensaje | 8-12 | 4-6 |

## 6. Riesgos

| Riesgo | Probabilidad | Mitigación |
|--------|-------------|------------|
| `asyncio.gather` en aiosqlite da menos ganancia de la esperada (thread único interno) | Media | Medir antes/después con tracing spans |
| `mmap_size` causa OOM en container con memoria limitada | Baja | Valor conservador (30MB); monitorear RSS |
| Backfill post-yield compite con primeros requests por Ollama | Baja | Backfill usa modelo de embeddings (diferente al chat); best-effort |
| Cambio en summarizer prompt (combinar flush+summary) afecta calidad | Media | Test A/B con dataset de eval existente |
