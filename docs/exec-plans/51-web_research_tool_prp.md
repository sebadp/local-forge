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
- [x] Crear PRD
- [x] Crear PRP
- [x] Actualizar `docs/exec-plans/README.md` con Plan 51

### Phase 1: Quick Win — Auto-include `"fetch"` category
- [x] En `router.py` `select_tools()`: si `"search"` en categories y `"fetch"` existe en `TOOL_CATEGORIES`, auto-agregar `"fetch"`
- [x] Test: `test_search_auto_includes_fetch_when_available` + `test_search_without_fetch_category_works`
- [x] Verificar que no rompe tests existentes de router

### Phase 2: Dependency + Extraction Infrastructure
- [x] `trafilatura>=2.0` ya en `pyproject.toml` (Plan 52)
- [x] `web_extraction.py` extendido con:
  - `chunk_text(text, max_chunk_chars=1500) -> list[str]`: split por headings, fallback párrafos, merge small, hard-split oversized, filter <50 chars
  - `_cosine_similarity(a, b) -> float`: pure Python cosine similarity
  - `async rank_chunks(query, chunks, ollama_client, embed_model, top_k, threshold) -> list[tuple[str, str, float]]`: nomic-embed-text con prefixes `search_query:`/`search_document:`, cosine sim, sorted
- [x] Tests unitarios: chunk (headings, paragraphs, merge, oversized, filter, empty), cosine sim (identical, orthogonal, opposite, zero), rank (order, threshold, empty)

### Phase 3: `web_research` Tool Registration
- [x] En `search_tools.py`, registrado `web_research` tool con handler
- [x] Handler `async web_research(query, max_pages=None)` orquesta pipeline completo
- [x] `"web_research"` agregado a `TOOL_CATEGORIES["search"]` en `router.py`
- [x] Test: pipeline end-to-end con mocks

### Phase 4: Multi-Query Search + Dedup
- [x] `_generate_search_variant(query)`: rota keywords, agrega año si no presente
- [x] `_generate_retry_variant(query)`: rotación diferente (mid-split)
- [x] Ambas búsquedas en `asyncio.gather` con `run_in_executor`
- [x] `_dedup_urls()`: normaliza URLs (strip trailing slash, query params), dedup por domain+path
- [x] `max_results=10` por búsqueda
- [x] Tests: dedup (duplicates, trailing slash, query params, empty, preserves original), variants (adds year, year present, rotates, different, retry different)

### Phase 5: Parallel Fetch + Extract
- [x] Reusa `fetch_multiple()` existente de web_extraction.py (asyncio.gather + Semaphore + return_exceptions)
- [x] Configurable via `web_research_fetch_timeout` y `web_research_max_concurrent`
- [x] Test: `test_respects_max_pages`, `test_no_successful_fetches`

### Phase 6: Chunk + Embed + Rank Pipeline
- [x] Chunk cada texto con `chunk_text()`, tag con URL source
- [x] Embed query con prefix `search_query:` + chunks con `search_document:`
- [x] Cosine similarity pura Python (`_cosine_similarity`)
- [x] Top-K + threshold filtering
- [x] Fallback si no hay ollama_client: chunks unranked
- [x] Tests: ranking order, threshold filtering, without ollama

### Phase 7: Output Formatting + Retry
- [x] `_format_research_output()`: markdown con `### Source:` headers, footer stats, char cap
- [x] Cap: drops lowest-ranked chunks until under limit
- [x] Retry: si `len(top_chunks) < 2` o `best_similarity < 0.25`, busca con `_generate_retry_variant`, re-rank combinado
- [x] Logging: `web_research: %d chunks ranked, top_sim=%.3f, retry=%s`
- [x] Tests: output format, char limit, empty chunks

### Phase 8: Settings
- [x] `app/config.py` nuevos settings:
  ```python
  web_research_max_pages: int = 8
  web_research_fetch_timeout: float = 8.0
  web_research_max_concurrent: int = 6
  web_research_chunk_size: int = 1500
  web_research_top_k: int = 8
  web_research_similarity_threshold: float = 0.2
  web_research_max_output_chars: int = 12000
  ```
- [x] `ollama_client` capturado via closure en `register()` (ya recibe el param)
- [x] `embedding_model` leído de settings

### Phase 9: Langfuse Observability — Spans jerárquicos para `web_research`

- [x] **Span raíz** `web_research:pipeline` (kind="span") — set_input con query/max_pages/is_multiquery/variant, set_output con stats completas
- [x] **Sub-span** `web_research:search` (kind="span") — queries usadas, URLs encontradas, latency
- [x] **Sub-span** `web_research:fetch` (kind="span") — status por URL, success_rate, latency
- [x] **Sub-span** `web_research:rank` (kind="span") — chunks con similarity scores, above/below threshold, latency
- [x] **Sub-span** `web_research:retry` (kind="span") — condicional, razón del retry, resultados
- [x] Tests: mock `get_current_trace()`, verify span names y kinds

### Phase 10: Validación
- [x] `ruff check` pass
- [x] `mypy app/` pass
- [x] `pytest` pass (857 tests, 0 failures)
- [ ] Test manual: deploy a docker, enviar "necesito fixture de Rosario Central con fechas y horas"
- [ ] Verificar en Langfuse spans
