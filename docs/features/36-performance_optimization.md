# Feature: Performance Optimization — Análisis y Mejoras del Critical Path

> **Versión**: v1.0
> **Fecha de análisis**: 2026-03-07
> **Estado**: 📋 Análisis completado, implementación pendiente

---

## Contexto: por qué importa la performance en este proyecto

LocalForge es un asistente LLM que responde mensajes de WhatsApp y Telegram en tiempo real.
Cada milisegundo entre que el usuario envía un mensaje y recibe la respuesta es latencia
percibida. A diferencia de una web app donde 200ms es imperceptible, en un chat 2-3 segundos
se sienten como "el bot se colgó".

El pipeline de un mensaje típico atraviesa:

```
[Mensaje usuario]
    │
    ▼
[Parse + Dedup]           ~5ms
    │
    ▼
[Phase A: embed + save]   ~100-200ms   ← I/O parallelizable
    │
    ▼
[Phase B: memories,       ~150-300ms   ← I/O parallelizable
 notes, summary, history]
    │
    ▼
[Phase C: classify +      ~200-400ms   ← LLM call
 user facts]
    │
    ▼
[Phase D: build context   ~500-3000ms  ← LLM call principal
 + LLM response]
    │
    ▼
[Guardrails + format +    ~20-100ms    ← CPU-bound
 send response]
```

**Latencia total observada**: 1-4 segundos (sin tool calling), 3-8 segundos (con tools).
**Objetivo**: reducir a 0.8-2.5s (sin tools), 2-5s (con tools).

---

## Metodología de análisis

El análisis se realizó con 4 agentes de exploración en paralelo, cada uno especializado
en una capa del sistema:

1. **Webhook Router** — critical path de `_handle_message` y `_run_normal_flow`
2. **LLM Client + Executor** — `OllamaClient`, tool loop, router de intents
3. **Database + Memory** — queries, índices, I/O de archivos
4. **Startup + MCP + Formatting** — inicialización, MCP calls, guardrails

Cada agente leyó los archivos fuente completos e identificó bottlenecks con ubicación
exacta (archivo:línea), impacto estimado, y sugerencia de fix.

### Criterios de priorización

Los hallazgos se priorizaron con 3 ejes:

| Eje | Peso | Razonamiento |
|-----|------|--------------|
| **Latencia en critical path** | Alto | Si el bottleneck está en el camino que recorre cada mensaje, su impacto se multiplica por cada request |
| **Esfuerzo de implementación** | Medio | Un fix de 5 minutos con 50ms de ahorro vale más que uno de 2 días con 100ms |
| **Riesgo de regresión** | Bajo | Preferimos cambios quirúrgicos sobre refactors grandes |

---

## Hallazgos: Bottlenecks identificados

### Tier 1 — Quick Wins (alto impacto, bajo esfuerzo)

#### 1.1 `get_windowed_history()` — queries secuenciales innecesarias

**Ubicación**: `app/conversation/manager.py:52-60`
**Impacto**: ~50-150ms por mensaje (100% de requests)

```python
# ACTUAL: secuencial — la segunda query espera que termine la primera
history = await self._repo.get_recent_messages(conv_id, self._max_messages)
summary = await self._repo.get_latest_summary(conv_id)
```

**Problema**: `get_recent_messages()` y `get_latest_summary()` son queries completamente
independientes (acceden a tablas distintas: `messages` y `summaries`). Sin embargo, se
ejecutan secuencialmente — la segunda espera que la primera termine.

**Por qué importa**: Esta función se llama en `ConversationContext.build()` (Phase A/B),
que ya usa `asyncio.gather()` para paralelizar otras operaciones. Pero *dentro* de
`get_windowed_history()`, las queries son secuenciales. Es un "gather within gather" que
se nos escapó.

**Fix**:
```python
history, summary = await asyncio.gather(
    self._repo.get_recent_messages(conv_id, self._max_messages),
    self._repo.get_latest_summary(conv_id),
)
```

**Referencia**: [Python docs — asyncio.gather](https://docs.python.org/3/library/asyncio-task.html#asyncio.gather):
*"Run awaitable objects concurrently. If all awaitables are completed successfully, the
result is an aggregate list of returned values."*

**Nota para juniors**: La regla general es: si dos `await` no dependen entre sí (el
resultado de uno no es input del otro), deberían ir en `asyncio.gather()`. Es el
equivalente async de "hacer dos cosas al mismo tiempo en vez de una después de otra".

---

#### 1.2 Índices faltantes en tablas del critical path

**Ubicación**: `app/database/db.py` (schema)
**Impacto**: ~20-100ms por mensaje

**Tablas afectadas**:

| Tabla | Query frecuente | Índice faltante |
|-------|----------------|-----------------|
| `summaries` | `WHERE conversation_id=? ORDER BY id DESC LIMIT 1` | `(conversation_id, id)` |
| `memories` | `WHERE active=1` | `(active, id)` |

**Por qué importa**: Sin índice, SQLite hace un *full table scan* — lee **todas** las filas
de la tabla para encontrar las que cumplen el `WHERE`. Con índice, va directo a las filas
relevantes via B-tree lookup.

**Evidencia**: SQLite usa un único archivo. Cada tabla sin índice requiere leer
secuencialmente todos los registros. Para una tabla `memories` con 500 filas, esto
significa ~500 comparaciones en vez de ~9 (log2(500)) con un B-tree index.

**Fix**:
```sql
CREATE INDEX IF NOT EXISTS idx_summaries_conversation ON summaries(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_memories_active ON memories(active, id);
```

**Referencia**: [SQLite Query Planning](https://www.sqlite.org/queryplanner.html):
*"Without an index, SQLite must do a full table scan to find the desired rows."*

**Por qué un índice compuesto `(active, id)`**: El query típico es
`WHERE active=1 ORDER BY id DESC`. Un índice compuesto permite que SQLite satisfaga
tanto el filtro como el ordenamiento usando el mismo índice, sin un sort adicional.

---

#### 1.3 PRAGMAs de SQLite faltantes

**Ubicación**: `app/database/db.py:292-296`
**Impacto**: ~5-10% throughput general

**PRAGMAs actuales** (bien configurados):
```sql
PRAGMA journal_mode=WAL;      -- Write-Ahead Logging
PRAGMA synchronous=NORMAL;    -- Menos fsyncs
PRAGMA cache_size=-32000;     -- 32MB page cache
PRAGMA temp_store=MEMORY;     -- Temp tables en RAM
PRAGMA foreign_keys=ON;
```

**PRAGMAs faltantes**:
```sql
PRAGMA mmap_size=30000000;    -- 30MB memory-mapped I/O
PRAGMA busy_timeout=5000;     -- 5s espera en contención (evita SQLITE_BUSY)
```

**Por qué `mmap_size`**: Memory-mapped I/O permite que SQLite acceda al archivo de la DB
directamente desde la memoria virtual del proceso, bypaseando la capa de `read()` syscalls.
Para una DB que cabe en RAM (la nuestra sí), esto acelera lecturas un 5-10%.

**Por qué `busy_timeout`**: Sin este PRAGMA, si dos operaciones async compiten por un
write lock, una falla inmediatamente con `SQLITE_BUSY`. Con `busy_timeout=5000`, SQLite
reintenta por hasta 5 segundos antes de fallar. Esto previene errores espurios bajo carga.

**Referencia**: [SQLite PRAGMA documentation](https://www.sqlite.org/pragma.html),
[SQLite mmap I/O](https://www.sqlite.org/mmap.html):
*"Memory-mapped I/O can provide a performance increase for read-intensive applications."*

---

#### 1.4 `langdetect.detect()` bloquea el event loop

**Ubicación**: `app/guardrails/checks.py:72-77`
**Impacto**: ~20-100ms por mensaje (bloqueante)

```python
# ACTUAL: sync call en función async — bloquea el event loop
user_lang = detect(user_text)
reply_lang = detect(reply)
```

**Por qué importa**: `langdetect` es una librería que ejecuta un modelo probabilístico
de N-gramas en Python puro. No es async. Cuando se llama dentro de una coroutine sin
`asyncio.to_thread()`, bloquea el event loop de asyncio — **ninguna otra coroutine puede
ejecutarse** durante esos 10-50ms.

En un sistema con múltiples usuarios concurrentes, esto serializa requests: si 3 usuarios
envían mensajes al mismo tiempo, los 3 se procesan uno tras otro en la parte de
language detection, en vez de concurrentemente.

**Fix**:
```python
user_lang, reply_lang = await asyncio.gather(
    asyncio.to_thread(detect, user_text),
    asyncio.to_thread(detect, reply),
)
```

**Referencia**: [Python docs — asyncio.to_thread](https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread):
*"Asynchronously run function func in a separate thread. Any *args and **kwargs supplied
for this function are directly passed to func."*

**Nota para juniors**: La regla de oro de asyncio es: **nunca ejecutes código CPU-bound
o I/O bloqueante directamente en una coroutine**. Si necesitás llamar código sync que
tarda más de ~5ms, usá `asyncio.to_thread()` para moverlo a un thread del pool.

---

#### 1.5 Regex de `<think>` sin pre-compilar

**Ubicación**: `app/llm/client.py:95-98`
**Impacto**: ~10-25ms total por mensaje (2-5ms × 5+ iteraciones)

```python
# ACTUAL: re.sub recompila la regex en cada llamada
content = re.sub(r"<think>.*?</think>\n*", "", content, flags=re.DOTALL)
```

**Por qué importa**: `re.sub()` con un string pattern llama internamente a `re.compile()`
cada vez. Python tiene un cache interno de ~512 patterns, pero la compilación todavía
tiene overhead. `chat_with_tools()` se llama 5+ veces por mensaje (una por iteración del
tool loop), así que este costo se multiplica.

Además, las operaciones de string `.split("</think>")[-1]` y `.split("<think>")[0]` son
redundantes si la regex ya limpió el contenido — solo son necesarias como fallback para
tags truncados.

**Fix**:
```python
# Module-level (compilado una sola vez al importar)
_RE_THINK = re.compile(r"<think>.*?</think>\n*", re.DOTALL)

# En el método:
content = _RE_THINK.sub("", content)
content = content.split("</think>")[-1]  # Edge case: tag de cierre huérfano
content = content.split("<think>")[0].strip()  # Edge case: tag de apertura truncado
```

**Referencia**: [Python re module docs](https://docs.python.org/3/library/re.html#re.compile):
*"The compiled versions of the most recent patterns passed to re.compile() and the
module-level matching functions are cached, [...] but using re.compile() and saving the
resulting regular expression object for reuse is more efficient when the expression will
be used several times in a single program."*

---

### Tier 2 — Optimizaciones de impacto alto (esfuerzo medio)

#### 2.1 Project progress: N+1 query pattern

**Ubicación**: `app/context/conversation_context.py:163-164`
**Impacto**: ~100-250ms para usuarios con proyectos activos

```python
# ACTUAL: N+1 — un query por proyecto
for p in capped:
    progress = await repository.get_project_progress(p.id)  # 5 queries secuenciales!
```

**El patrón N+1** es uno de los anti-patterns de performance más conocidos en bases de
datos. Se llama "N+1" porque hacés 1 query para obtener la lista y luego N queries más
(una por cada item) para obtener los detalles de cada uno.

**Solución A** — Paralelizar (rápido de implementar):
```python
progress_list = await asyncio.gather(
    *(repository.get_project_progress(p.id) for p in capped)
)
```

**Solución B** — Query único con JOIN (óptimo):
```sql
SELECT p.id, p.name,
       COUNT(CASE WHEN pt.status='done' THEN 1 END) as done,
       COUNT(pt.id) as total
FROM projects p
LEFT JOIN project_tasks pt ON pt.project_id = p.id
WHERE p.phone_number = ? AND p.status = 'active'
GROUP BY p.id
LIMIT 5
```

La Solución B es superior porque un solo roundtrip a SQLite es siempre más rápido que 5+,
incluso si los 5 son paralelos (el driver aiosqlite serializa los queries al usar una
sola conexión).

**Referencia**: [Rails Guides — N+1 Queries](https://guides.rubyonrails.org/active_record_querying.html#eager-loading-associations) (el concepto aplica a cualquier ORM/DB):
*"This code looks fine at the first sight. But the problem lies within the total number of
queries executed."*

---

#### 2.2 Embedding backfill bloquea el startup

**Ubicación**: `app/main.py:261-271`
**Impacto**: 2-5 segundos de startup time

```python
# ACTUAL: bloquea startup — la app no acepta requests hasta que termine
await backfill_embeddings(repository, ollama_client, model)
await backfill_note_embeddings(repository, ollama_client, model)
```

**Por qué importa**: En el patrón `lifespan` de FastAPI, todo lo que está antes del
`yield` es código de startup. FastAPI **no acepta requests HTTP hasta que el lifespan
genera su yield**. Si el backfill tarda 5 segundos (500 memorias sin embedding ×
~10ms por embed), el app es inaccesible durante ese tiempo.

Esto es especialmente grave en deploys con rolling updates o restarts automáticos:
el container nuevo no pasa el health check hasta que el backfill termine.

**Fix**: Mover a un background task post-yield:
```python
yield  # App ya acepta requests
# Backfill corre en background, no bloquea nada
asyncio.create_task(_safe_backfill(repository, ollama_client, model))
```

**Referencia**: [FastAPI Lifespan Events](https://fastapi.tiangolo.com/advanced/events/):
*"The code before the yield will be executed before the application starts. The code after
the yield will be executed after the application has finished."*

---

#### 2.3 Embedding saves secuenciales en backfill

**Ubicación**: `app/embeddings/indexer.py`
**Impacto**: ~250-500ms por batch de 50 embeddings

Después del batch embed (un solo HTTP call a Ollama), cada embedding se guarda con
`repository.save_embedding()` que hace `INSERT` + `COMMIT` individualmente. 50 embeddings
= 50 commits = 50 fsyncs al disco.

**Por qué importa**: En SQLite, cada `COMMIT` en modo WAL hace un fsync al WAL file.
Un fsync en SSD toma 0.5-5ms. 50 fsyncs = 25-250ms de pura espera de disco.

**Fix**: Usar `executemany()` + un solo commit:
```python
for (mem_id, _), emb in zip(batch, embeddings, strict=False):
    blob = struct.pack(f"{len(emb)}f", *emb)
    await conn.execute("INSERT OR REPLACE INTO vec_memories ...", (mem_id, blob))
await conn.commit()  # Un solo fsync
```

**Referencia**: [SQLite FAQ — INSERT performance](https://www.sqlite.org/faq.html#q19):
*"SQLite can handle approximately 50,000 INSERT statements per second on an average
desktop computer. But it will only handle a few dozen transactions per second. Transaction
speed is limited by the rotational speed of your disk drive."*

---

#### 2.4 Compaction de tool outputs: contención GPU en Ollama

**Ubicación**: `app/skills/executor.py:251`
**Impacto**: variable — puede agregar 1-3 segundos bajo carga

Cuando 3+ tools se ejecutan en paralelo (via `asyncio.gather`) y cada una produce output
largo, las 3 llaman a `compact_tool_output()` que potencialmente invoca al LLM para
resumir. Ollama **serializa internamente** las requests a la GPU — solo una inferencia
corre a la vez.

El efecto es que los 3 compaction requests se encolan en Ollama, y el tool loop espera a
que los 3 terminen antes de enviar los resultados al LLM principal. Esto puede agregar
1-3 segundos de latencia por la serialización interna.

**Fix**: Priorizar la extracción JSON (sin LLM) con un threshold más agresivo. Solo caer
al LLM compaction si el output supera un límite alto (ej. 2000 chars post-JSON-extraction).

---

#### 2.5 Summarizer hace 2 LLM calls separadas

**Ubicación**: `app/conversation/summarizer.py`
**Impacto**: 1-3 segundos (background task, no critical path directo)

`flush_to_memory()` extrae facts del historial con un LLM call, y luego la summarización
hace otro LLM call sobre el mismo historial. Son 2 inferencias cuando 1 bastaría.

**Fix**: Combinar en un solo prompt:
```
Given this conversation, extract:
1. Key facts about the user (as bullet points)
2. Important events (as bullet points)
3. A concise summary of the conversation

Respond in JSON: {"facts": [...], "events": [...], "summary": "..."}
```

**Nota**: Esto es una optimización de background — no afecta la latencia percibida por el
usuario directamente, pero libera la GPU de Ollama para procesar mensajes más rápido.

---

### Tier 3 — Optimizaciones menores

| ID | Bottleneck | Ubicación | Ahorro | Detalle |
|----|-----------|-----------|--------|---------|
| 3.1 | URL regex recompilada en `classify_intent` | `router.py:194` | ~1ms/call | Pre-compilar a module-level |
| 3.2 | Regexes en `markdown_to_wa` recompiladas | `formatting/whatsapp.py` | ~1ms/msg | Pre-compilar a module-level |
| 3.3 | `get_or_create_conversation` hace 2 roundtrips | `repository.py:16-34` | ~5-10ms | Usar `INSERT OR IGNORE` atómico |
| 3.4 | MCP connect timeout 30s excesivo | `mcp/manager.py:24` | Evita bloqueo | Reducir a 10s + circuit breaker |
| 3.5 | `_conv_id_cache` crece sin límite | `manager.py:11` | Memory leak lento | Agregar LRU con OrderedDict |
| 3.6 | `_register_fetch_category()` triple scan de tools | `mcp/manager.py:331-340` | ~1ms startup | Cache `_tools_by_server` |

---

## Decisiones de diseño y trade-offs

### Por qué no usar connection pooling

SQLite es una base de datos embebida (in-process). No tiene un servidor separado que
acepte conexiones concurrentes. `aiosqlite` usa un thread dedicado internamente.
Connection pooling no aplica — lo que sí aplica es minimizar commits (cada commit es un
fsync) y usar PRAGMAs adecuados.

### Por qué `asyncio.gather()` y no `asyncio.TaskGroup`

`TaskGroup` (Python 3.11+) es más robusto para error handling (cancela tasks hermanas si
una falla). Sin embargo, en nuestro caso la mayoría de las operaciones son fail-open
(si una falla, las otras deben continuar). `asyncio.gather(return_exceptions=True)` es
más adecuado para este patrón.

### Por qué pre-compilar regexes importa

Python cachea las últimas ~512 regex compiladas internamente
([`_MAXCACHE = 512`](https://github.com/python/cpython/blob/main/Lib/re/__init__.py)),
pero el lookup en el cache tiene overhead (hashing del pattern + flags). Pre-compilar a
module-level elimina este overhead completamente y es el patrón recomendado por la
documentación oficial.

### Por qué no cambiar a PostgreSQL

SQLite con WAL mode, mmap, y los PRAGMAs correctos maneja fácilmente el throughput de
este proyecto (decenas de mensajes/segundo). PostgreSQL agregaría latencia de red
(~1-5ms por query), complejidad operacional (otro servicio que mantener), y overhead de
conexión. La ganancia solo aparecería con >100 escrituras concurrentes/segundo, que está
muy lejos de nuestro caso de uso.

### Por qué mover backfill a post-yield y no eliminarlo

El backfill garantiza que memorias/notas creadas antes de tener semantic search activo
se indexen retroactivamente. Sin backfill, los usuarios no encontrarían memorias antiguas
con búsqueda semántica. Moverlo a post-yield es la solución correcta: el app acepta
requests inmediatamente, y el backfill corre en background sin afectar la experiencia.

---

## Métricas para validar mejoras

### Cómo medir el impacto

1. **Latencia end-to-end**: `total_duration_ms` en `ChatResponse` + spans de tracing
2. **Latencia por fase**: Spans existentes en `TraceContext` (classify, tool_loop, guardrails)
3. **Throughput**: Mensajes procesados por segundo bajo carga (usar `locust` o similar)
4. **Startup time**: Medir tiempo desde `docker compose up` hasta primer health check OK

### Benchmarks esperados (antes/después)

| Métrica | Antes (estimado) | Después T1 | Después T1+T2 |
|---------|-------------------|------------|---------------|
| Latencia mensaje simple | 1.5-3s | 1.2-2.5s | 1.0-2.0s |
| Latencia con tools | 3-8s | 2.5-6s | 2-5s |
| Startup time | 5-10s | 5-10s | 2-4s |
| Queries/mensaje | 8-12 | 6-8 | 4-6 |

---

## Gotchas y riesgos

- **`asyncio.gather` en aiosqlite**: aiosqlite usa un solo thread interno. Dos queries
  "paralelas" en realidad se serializan en ese thread. El beneficio viene de que mientras
  una query espera I/O de disco, la otra puede preparar su SQL. El ahorro real es menor
  que con un DB server real, pero sigue siendo medible (~30-50% del tiempo secuencial).

- **`PRAGMA mmap_size` en Docker**: Si el container tiene limitación de memoria
  (ej. `--memory=512m`), `mmap_size=30MB` consume dirección de memoria virtual.
  En práctica, esto no es un problema a menos que la DB supere 30MB.

- **Backfill post-yield**: Si un usuario envía un mensaje en los primeros 2 segundos
  después del startup, el backfill y el mensaje compiten por Ollama. Mitigación: el
  backfill ya es best-effort (try/except), y el message handler tiene prioridad porque
  usa el modelo de chat, no el de embeddings.

---

## Variables de configuración relevantes

| Variable (`config.py`) | Default | Efecto |
|---|---|---|
| `semantic_search_enabled` | `True` | Controla si se hacen embeddings |
| `semantic_search_top_k` | `10` | Límite de resultados semánticos |
| `memory_similarity_threshold` | `1.0` | Threshold de distancia L2 |
| `history_verbatim_count` | `8` | Mensajes recientes sin comprimir |
| `max_tools_per_call` | `8` | Tools por iteración (afecta compaction) |
