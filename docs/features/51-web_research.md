# Feature: Deep Web Research Tool (`web_research`)

> **Versión**: v1.0
> **Fecha de implementación**: 2026-03-18
> **Plan**: Exec Plan 51
> **Estado**: ✅ Implementada

---

## ¿Qué hace?

Cuando el usuario pide datos específicos de la web (fechas, precios, horarios, fixtures, listas), el asistente ejecuta **una sola tool** que busca en múltiples queries, descarga las páginas, extrae el contenido relevante y lo rankea semánticamente. El LLM recibe los fragmentos más informativos en lugar de simples snippets de búsqueda.

**Antes:** El LLM buscaba 3 veces y decía "no encontré info" porque nunca fetcheaba las páginas.
**Después:** Una llamada a `web_research` retorna contenido real rankeado por relevancia.

---

## Arquitectura

```
[Usuario: "fixture Rosario Central"]
        │
        ▼
[classify_intent → "search"]
        │
        ▼
[LLM selecciona web_research(query="fixture Rosario Central")]
        │
        ▼
┌── web_research handler ──────────────────────────────────┐
│                                                          │
│  1. Multi-query search (original + variante rotada)      │
│     └── asyncio.gather(DDGS × 2)                        │
│                                                          │
│  2. Dedup URLs (normalize domain+path)                   │
│                                                          │
│  3. Parallel fetch + trafilatura extract                  │
│     └── asyncio.gather(httpx × N, Semaphore)             │
│                                                          │
│  4. Chunk by headings / paragraphs                       │
│                                                          │
│  5. Embed + Cosine Rank (nomic-embed-text)               │
│     └── search_query: / search_document: prefixes        │
│                                                          │
│  6. Retry si insufficient (< 2 chunks o sim < 0.25)      │
│                                                          │
│  7. Format output (### Source: URL + content, capped)    │
└──────────────────────────────────────────────────────────┘
        │
        ▼
[LLM sintetiza respuesta con chunks rankeados]
```

---

## Archivos clave

| Archivo | Rol |
|---|---|
| `app/skills/tools/search_tools.py` | Handler `web_research`, helpers (`_generate_search_variant`, `_dedup_urls`, `_format_research_output`, `_close_pipeline`) |
| `app/skills/tools/web_extraction.py` | `chunk_text()`, `_cosine_similarity()`, `rank_chunks()` + utilidades compartidas (`fetch_page`, `extract_text`, `fetch_multiple`) |
| `app/skills/router.py` | `"web_research"` en `TOOL_CATEGORIES["search"]`, auto-include `"fetch"` cuando `"search"` clasificado |
| `app/config.py` | 7 settings: `web_research_max_pages`, `_fetch_timeout`, `_max_concurrent`, `_chunk_size`, `_top_k`, `_similarity_threshold`, `_max_output_chars` |
| `tests/test_web_research.py` | 38 tests: chunking, cosine sim, ranking, variants, dedup, pipeline, observability, router |

---

## Walkthrough técnico: cómo funciona

1. **classify_intent** clasifica el mensaje → `["search"]` → `select_tools()` incluye `web_research` (y auto-añade `"fetch"` si hay MCP fetch tools disponibles) → `router.py:select_tools`

2. **LLM llama** `web_research(query="fixture Rosario Central")` → `search_tools.py:web_research`

3. **Genera variante**: `_generate_search_variant()` rota keywords + agrega año actual → ej: `"Rosario Central 2026 fixture"`

4. **Búsqueda paralela**: `asyncio.gather` ejecuta `DDGS().text()` con `max_results=10` para ambas queries en threads separados → ~20 resultados

5. **Dedup URLs**: `_dedup_urls()` normaliza URLs (strip query params, trailing slash) → ~12-15 URLs únicas

6. **Fetch paralelo**: `fetch_multiple()` con `asyncio.Semaphore(6)` → httpx GET + `trafilatura.extract()` via `asyncio.to_thread()` → descarta páginas vacías/cortas

7. **Chunk**: `chunk_text()` split por markdown headings (`\n##`), fallback a párrafos (`\n\n`), merge chunks pequeños, hard-split oversized, filtrar < 50 chars

8. **Embed + Rank**: `rank_chunks()` embeds con `nomic-embed-text` usando prefixes `search_query:` / `search_document:` → cosine similarity → top-K por threshold

9. **Retry** (condicional): si < 2 chunks relevantes o best_sim < 0.25 → genera otra variante (`_generate_retry_variant`), busca URLs nuevas, re-rank combinado

10. **Format output**: `_format_research_output()` genera markdown con `### Source: URL` + chunk content, footer con stats, cap a 12000 chars (drops lowest-ranked chunks si excede)

11. **Langfuse spans**: `web_research:pipeline` (root) → `web_research:search`, `web_research:fetch`, `web_research:rank`, `web_research:retry` (condicional)

---

## Cómo extenderla

- **Agregar más resultados por búsqueda**: cambiar `max_results=10` en la llamada a `_perform_search()` dentro del handler
- **Cambiar chunking strategy**: modificar `chunk_text()` en `web_extraction.py` (ej: agregar split por `<h1-h6>` tags HTML)
- **Usar otro embedding model**: cambiar `embedding_model` en `.env` o `config.py`
- **Ajustar ranking**: `web_research_similarity_threshold` y `web_research_top_k` en `.env`
- **Ajustar output size**: `web_research_max_output_chars` en `.env`

---

## Guía de testing

→ Ver [`docs/testing/51-web_research_testing.md`](../testing/51-web_research_testing.md)

---

## Decisiones de diseño

| Decisión | Alternativa descartada | Motivo |
|---|---|---|
| Cosine similarity pura Python | numpy / sqlite-vec | Evita dependencia extra; ~30 chunks × 768 dims = ~1ms, negligible |
| trafilatura para extraction | BeautifulSoup / regex only | F1=0.937, maneja tables, ~100ms/page; regex fallback incluido |
| No LLM en pipeline pre-ranking | LLM extraction (como Plan 52) | Pipeline determinístico más rápido; LLM principal sintetiza al final |
| Multi-query rotación programática | LLM para generar variantes | Zero latency; variantes suficientes para diversificar resultados |
| nomic-embed-text con prefixes | Embeddings sin prefixes | `search_query:` / `search_document:` mejora accuracy según docs del modelo |
| Retry automático | Retry manual por el LLM | Modelo 9B no encadena tools confiablemente; mejor retry interno |

---

## Gotchas y edge cases

- **Sin ollama_client**: si no se pasa `ollama_client` al register, ranking es imposible → fallback a chunks unranked (primeros N chunks)
- **Embedding failures**: si `ollama_client.embed()` falla → catch silencioso, usa chunks unranked
- **Todas las páginas fallan fetch**: retorna snippets de búsqueda como fallback (no error)
- **Chunks muy cortos**: `chunk_text()` filtra chunks < 50 chars; si todos son filtrados → retorna raw text truncado
- **Pipeline span close**: `_close_pipeline()` usa `asyncio.ensure_future()` fire-and-forget para no bloquear el return
- **`_perform_search` max_results**: el parámetro tiene default `MAX_RESULTS=5` para backward-compat con `web_search`; `web_research` lo overridea a 10

---

## Variables de configuración relevantes

| Variable (`config.py`) | Default | Efecto |
|---|---|---|
| `web_research_max_pages` | `8` | Máximo de páginas a fetchear y analizar |
| `web_research_fetch_timeout` | `8.0` | Timeout por página (segundos) |
| `web_research_max_concurrent` | `6` | Máximo de fetches concurrentes (Semaphore) |
| `web_research_chunk_size` | `1500` | Máximo chars por chunk |
| `web_research_top_k` | `8` | Máximo chunks en el resultado |
| `web_research_similarity_threshold` | `0.2` | Mínimo cosine similarity para incluir un chunk |
| `web_research_max_output_chars` | `12000` | Máximo chars del output total (evita compaction) |
| `embedding_model` | `nomic-embed-text` | Modelo de embeddings para ranking |
