# PRP: Deep Web Research Tool — `web_research` (Plan 51)

## Archivos Modificados

### Nuevos
- `app/skills/tools/web_extraction.py`: Pipeline de extracción — fetch, extract, chunk, embed, rank
- `tests/test_web_research.py`: Tests para el pipeline completo y cada fase

### Modificados
- `app/skills/tools/search_tools.py`: Registrar `web_research` tool
- `app/skills/router.py`: Auto-include `"fetch"` cuando `"search"` clasificado, agregar `web_research` a categoría `"search"`
- `pyproject.toml`: Agregar `trafilatura` dependency
- `app/config.py`: Settings para web_research (max_pages, chunk_size, fetch_timeout, etc.)

### Docs
- `docs/exec-plans/51-web_research_tool_prd.md`: PRD (nuevo)
- `docs/exec-plans/51-web_research_tool_prp.md`: PRP (nuevo)
- `docs/exec-plans/README.md`: Entrada del Plan 51

## Fases de Implementación

### Phase 0: Documentación
- [ ] Crear PRD
- [ ] Crear PRP
- [ ] Actualizar `docs/exec-plans/README.md` con Plan 51

### Phase 1: Quick Win — Auto-include `"fetch"` category
- [ ] En `router.py` `classify_intent()` o `select_tools()`: si `"search"` en categories y `"fetch"` existe en `TOOL_CATEGORIES`, auto-agregar `"fetch"`
- [ ] Test: clasificar "busca info sobre X" → categories incluye `"fetch"`
- [ ] Verificar que no rompe tests existentes de router

### Phase 2: Dependency + Extraction Infrastructure
- [ ] Agregar `trafilatura>=2.0` a `pyproject.toml`
- [ ] Crear `app/skills/tools/web_extraction.py` con funciones:
  - `async fetch_page(url: str, timeout: float = 8.0) -> str | None`: httpx GET + manejo de errores
  - `extract_text(html: str) -> str`: trafilatura fallback a regex stripping
  - `chunk_text(text: str, max_chunk_chars: int = 1500) -> list[str]`: split por headings (`##`/`###`/`<h1-h6>`), fallback a párrafos dobles (`\n\n`), con merge de chunks pequeños (<200 chars)
  - `async rank_chunks(query: str, chunks: list[str], ollama_client, top_k: int = 8) -> list[tuple[str, float]]`: embed query + chunks con nomic-embed-text, cosine similarity, return sorted
- [ ] Tests unitarios: fetch (mock httpx), extract (HTML samples), chunk (heading split, paragraph fallback), rank (mock embeddings)

### Phase 3: `web_research` Tool Registration
- [ ] En `search_tools.py`, registrar `web_research` tool con handler:
  ```
  name: "web_research"
  description: "Deep web research: searches, fetches page content, and extracts relevant information. Use when you need specific data (dates, times, prices, schedules, lists) from web pages — not just search snippets."
  parameters:
    query: str (required) — "Research query"
    max_pages: int (optional, default 8) — "Maximum pages to fetch and analyze"
  ```
- [ ] Handler `async web_research(query, max_pages=8)` orquesta el pipeline completo:
  1. Multi-query search (original + variante)
  2. Dedup URLs
  3. Parallel fetch + extract
  4. Chunk + rank
  5. Format output
- [ ] Agregar `"web_research"` a `TOOL_CATEGORIES["search"]` en `router.py`
- [ ] Test: mock DuckDuckGo + httpx + embeddings → verify pipeline end-to-end

### Phase 4: Multi-Query Search + Dedup
- [ ] `_generate_search_variant(query: str) -> str`: variante programática
  - Reordena keywords, agrega año actual si no está presente
  - Ej: "fixture Rosario Central 2026" → "Rosario Central calendario partidos 2026 fechas"
- [ ] Ambas búsquedas en `asyncio.gather` con `loop.run_in_executor` (DDGS es sync)
- [ ] Dedup por dominio + path (normalizar URLs: strip trailing slash, strip query params, etc.)
- [ ] `max_results=10` por búsqueda (up from 5) → ~20 resultados, ~12-15 únicos
- [ ] Test: verify dedup con URLs duplicadas, variants generation

### Phase 5: Parallel Fetch + Extract
- [ ] `async fetch_multiple(urls: list[str], timeout: float, max_concurrent: int) -> list[tuple[str, str | None]]`
  - `asyncio.gather` con `return_exceptions=True`
  - `asyncio.Semaphore(max_concurrent)` para limitar conexiones (default 6)
  - User-Agent header razonable para evitar 403s
  - Return: `[(url, extracted_text | None), ...]`
- [ ] `trafilatura.extract()` via `asyncio.to_thread()` (es CPU-bound)
- [ ] Filtrar: descartar páginas con `None` o `len(text) < 100`
- [ ] Logging: `logger.info("web_research: fetched %d/%d pages successfully", ...)`
- [ ] Test: mock httpx responses (200, 403, timeout), verify filtering

### Phase 6: Chunk + Embed + Rank Pipeline
- [ ] Chunk cada texto extraído con `chunk_text()`
- [ ] Taggear cada chunk con su URL source: `(chunk_text, source_url)`
- [ ] Embed query con prefix `search_query:` via `OllamaClient.embed()`
- [ ] Embed todos chunks con prefix `search_document:` en batch (single `embed()` call si Ollama lo soporta, sino sequential)
- [ ] Cosine similarity: `dot(q, c) / (norm(q) * norm(c))` — no necesitamos sqlite-vec, puro numpy/math
- [ ] Top-K chunks (default 8), filtrar si similarity < threshold (0.2)
- [ ] Test: mock embeddings, verify ranking order, threshold filtering

### Phase 7: Output Formatting + Retry
- [ ] Format output:
  ```
  ## Results from web research: "{query}"

  ### Source: espn.com.ar/futbol/equipo/calendario/...
  [chunk content]

  ---
  ### Source: rosariocentral.com/noticia/fixture-...
  [chunk content]

  ---
  (N sources analyzed, M relevant sections found)
  ```
- [ ] Cap total output: max 12000 chars (para evitar compaction). Si excede, reducir chars por chunk proporcionalmente
- [ ] Retry logic: si `len(relevant_chunks) < 2` o `best_similarity < 0.25`:
  - Generar segunda variante de query
  - Buscar + fetch nuevas URLs (no repetir ya visitadas)
  - Re-rank con todos los chunks acumulados (rounds 1 + 2)
  - Max 2 rounds total
- [ ] Logging: `logger.info("web_research: %d chunks ranked, top similarity=%.3f, retry=%s", ...)`
- [ ] Test: verify retry triggers, output formatting, cap

### Phase 8: Settings
- [ ] `app/config.py` nuevos settings:
  ```python
  web_research_max_pages: int = 8
  web_research_fetch_timeout: float = 8.0
  web_research_max_concurrent: int = 6
  web_research_chunk_size: int = 1500
  web_research_top_k: int = 8
  web_research_similarity_threshold: float = 0.2
  web_research_max_output_chars: int = 12000
  ```
- [ ] Pasar `ollama_client` al handler de `web_research` via skill registry context (verificar si el handler recibe acceso al client — puede necesitar un `_context` pattern)
- [ ] Test: settings override

### Phase 9: Langfuse Observability — Spans jerárquicos para `web_research`

> **Patrón del proyecto**: `get_current_trace()` → `trace.span(name, kind=)` → `set_input()` / `set_output()` / `set_metadata()`.
> El executor ya crea `tool:web_research` (kind="tool") automáticamente con arguments como input.
> Acá creamos **sub-spans internos** dentro del handler para trazabilidad granular de cada fase.

- [ ] **Span raíz** `web_research:pipeline` (kind="span") — engloba todo el pipeline
  - `set_input`:
    ```python
    {
        "query": query,
        "max_pages": max_pages,
        "is_multiquery": True,  # flag explícito para filtrar en Langfuse
        "query_variant": variant_query,  # la variante generada
    }
    ```
  - `set_output` (al final del pipeline):
    ```python
    {
        "total_urls_found": len(all_urls),
        "unique_urls": len(deduped_urls),
        "pages_fetched": pages_attempted,
        "pages_successful": pages_ok,
        "pages_failed": pages_failed,
        "total_chunks": total_chunks,
        "relevant_chunks": len(top_chunks),
        "top_similarity": round(best_similarity, 4),
        "retry_triggered": bool,
        "retry_rounds": rounds_executed,
        "output_chars": len(final_output),
        "latency_search_ms": search_elapsed,
        "latency_fetch_ms": fetch_elapsed,
        "latency_embed_ms": embed_elapsed,
        "latency_total_ms": total_elapsed,
    }
    ```

- [ ] **Sub-span** `web_research:search` (kind="span") — fase de búsqueda
  - `set_input`:
    ```python
    {
        "queries": [original_query, variant_query],
        "max_results_per_query": 10,
    }
    ```
  - `set_output`:
    ```python
    {
        "results_per_query": [len(results_1), len(results_2)],
        "total_unique_urls": len(deduped),
        "urls": deduped[:10],  # primeras 10 para inspección
        "latency_ms": elapsed,
    }
    ```

- [ ] **Sub-span** `web_research:fetch` (kind="span") — fase de fetch paralelo
  - `set_input`:
    ```python
    {
        "urls_to_fetch": urls[:max_pages],
        "timeout": fetch_timeout,
        "max_concurrent": max_concurrent,
    }
    ```
  - `set_output`:
    ```python
    {
        "results": [
            {
                "url": url,
                "status": "ok" | "error" | "timeout" | "empty",
                "chars_extracted": len(text) if text else 0,
                "error": str(err)[:200] if err else None,
            }
            for url, text, err in fetch_results
        ],
        "success_rate": f"{pages_ok}/{pages_attempted}",
        "latency_ms": elapsed,
    }
    ```

- [ ] **Sub-span** `web_research:rank` (kind="span") — fase de chunk + embed + rank
  - `set_input`:
    ```python
    {
        "total_chunks": len(all_chunks),
        "chunks_per_source": {url: count for url, count in source_counts.items()},
        "embedding_model": "nomic-embed-text",
        "top_k": top_k,
        "similarity_threshold": threshold,
    }
    ```
  - `set_output`:
    ```python
    {
        "top_chunks": [
            {
                "source": url,
                "similarity": round(sim, 4),
                "preview": chunk_text[:200],
            }
            for chunk_text, url, sim in top_chunks
        ],
        "above_threshold": chunks_above_threshold,
        "below_threshold": chunks_below_threshold,
        "latency_embed_ms": embed_elapsed,
        "latency_rank_ms": rank_elapsed,
    }
    ```

- [ ] **Sub-span** `web_research:retry` (kind="span") — solo si retry se activa
  - `set_input`:
    ```python
    {
        "reason": "insufficient_chunks" | "low_similarity",
        "best_similarity_round1": round(best_sim, 4),
        "relevant_chunks_round1": count,
        "new_query": refined_query,
    }
    ```
  - `set_output`:
    ```python
    {
        "new_urls_fetched": len(new_urls),
        "new_chunks_added": new_chunk_count,
        "best_similarity_after_retry": round(new_best_sim, 4),
        "total_relevant_chunks": final_count,
    }
    ```

- [ ] Tests: mock `get_current_trace()`, verify cada sub-span se crea con nombre y kind correctos, verify set_input/set_output llamados con keys esperadas

### Phase 10: Validación
- [ ] `ruff check` pass
- [ ] `mypy app/` pass
- [ ] `pytest` pass (todos los tests existentes + nuevos)
- [ ] Test manual: deploy a docker, enviar "necesito fixture de Rosario Central con fechas y horas"
- [ ] Verificar en Langfuse:
  - Span `tool:web_research` (creado por executor) contiene arguments
  - Sub-span `web_research:pipeline` muestra resumen completo
  - Sub-span `web_research:search` muestra queries usadas y URLs encontradas
  - Sub-span `web_research:fetch` muestra estado de cada URL (ok/error/timeout/empty)
  - Sub-span `web_research:rank` muestra chunks con similarity scores y previews
  - Sub-span `web_research:retry` (si aplica) muestra razón y resultado
