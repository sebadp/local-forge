# PRP: Web Search Enhancement — Smart Fetch & LLM Extract (Plan 52)

## Archivos Modificados

### Nuevos (si Plan 51 no se implementó antes)
- `app/skills/tools/web_extraction.py`: Utilidades compartidas de fetch + extract (reutilizable)

### Modificados
- `app/skills/tools/search_tools.py`: Agregar `depth` param a `web_search`, handler de auto-fetch + LLM extract
- `app/config.py`: Settings para web_search detailed mode
- `pyproject.toml`: Agregar `trafilatura` si no está (dependency compartida con Plan 51)
- `tests/test_search_tools.py` (o nuevo `tests/test_web_search_enhanced.py`): Tests para depth="detailed"

### Docs
- `docs/exec-plans/52-web_search_enhancement_prd.md`: PRD (nuevo)
- `docs/exec-plans/52-web_search_enhancement_prp.md`: PRP (nuevo)
- `docs/exec-plans/README.md`: Entrada del Plan 52

## Fases de Implementación

### Phase 0: Documentación
- [ ] Crear PRD
- [ ] Crear PRP
- [ ] Actualizar `docs/exec-plans/README.md` con Plan 52

### Phase 1: Shared Extraction Utilities
> Si Plan 51 ya fue implementado, esta fase se salta — reutilizar `web_extraction.py`.

- [ ] Crear `app/skills/tools/web_extraction.py` con:
  - `async fetch_page(url: str, timeout: float = 8.0) -> str | None`: httpx GET con headers razonables
  - `extract_text(html: str) -> str`: `trafilatura.extract()` con fallback a regex stripping
  - `async fetch_and_extract(url: str, timeout: float = 8.0) -> tuple[str, str | None]`: convenience wrapper
  - `async fetch_multiple(urls: list[str], timeout: float = 8.0, max_concurrent: int = 4) -> list[tuple[str, str | None]]`: parallel fetch+extract con semaphore
- [ ] Agregar `trafilatura>=2.0` a `pyproject.toml` (si no está)
- [ ] Tests unitarios para cada función (mock httpx)

### Phase 2: LLM Extraction Prompt
- [ ] Definir prompt de extracción en `search_tools.py` (constante):
  ```python
  _EXTRACT_PROMPT = (
      "Extract the most relevant information from these web pages to answer the query.\n"
      "RULES:\n"
      "- Include EXACT data: dates, times, names, prices, numbers — never approximate\n"
      "- Preserve URLs for sources\n"
      "- If a page has no relevant info, skip it\n"
      "- Format as a clear, structured summary\n"
      "- Keep it concise — only information that directly answers the query\n"
  )
  ```
- [ ] Función `async _llm_extract(query: str, pages: list[tuple[str, str]], ollama_client) -> str`:
  - Construye messages: system=_EXTRACT_PROMPT, user="Query: {query}\n\n" + page contents
  - Trunca cada página a `web_search_extract_page_limit` chars (default 2500)
  - Llama a `ollama_client.chat(messages, think=False)`
  - Return: texto extraído
- [ ] Test: mock ollama_client, verify prompt construction, verify think=False

### Phase 3: `web_search` Enhancement
- [ ] Agregar parámetro `depth` al tool schema:
  ```python
  "depth": {
      "type": "string",
      "enum": ["quick", "detailed"],
      "description": "Search depth: 'quick' returns snippets only (fast), 'detailed' also fetches and extracts content from top result pages (use for specific data like dates, prices, schedules)",
  }
  ```
- [ ] En handler `web_search()`:
  ```python
  async def web_search(query, time_range=None, depth="quick"):
      results = await _perform_search_async(query, time_range)
      snippets = _format_snippets(results)

      if depth != "detailed" or not results:
          return snippets

      # Auto-fetch top N pages
      urls = [r["href"] for r in results[:web_search_fetch_top_n]]
      pages = await fetch_multiple(urls, timeout=settings.web_search_fetch_timeout)
      successful = [(url, text) for url, text in pages if text]

      if not successful:
          return snippets + "\n\n(Could not fetch page content. Results above are search snippets only.)"

      # LLM extraction
      extracted = await _llm_extract(query, successful, ollama_client)
      return f"{snippets}\n\n---\n## Extracted content from top results:\n\n{extracted}"
  ```
- [ ] `depth` default es `"quick"` → backward compatible, zero behavior change sin opt-in
- [ ] Test: verify quick mode unchanged, detailed mode calls fetch+extract

### Phase 4: OllamaClient Access in Handler
- [ ] Verificar cómo el handler de `web_search` accede a `ollama_client`:
  - Option A: via `SkillRegistry.context` (si existe pattern para pasar state a handlers)
  - Option B: via global app state import (como hace `compact_tool_output`)
  - Option C: via closure sobre el client en `register()`
- [ ] Implementar el pattern elegido. Si `register(registry)` ya recibe el client via registry context, usarlo. Si no, documentar el approach
- [ ] Test: verify client is accessible from handler

### Phase 5: Tool Description Update
- [ ] Actualizar description de `web_search`:
  ```
  "Search the internet for information. Returns search result snippets by default (fast). "
  "Set depth='detailed' when you need specific data like dates, times, prices, schedules, "
  "or detailed lists — this will fetch and extract actual content from the top result pages."
  ```
- [ ] Verificar que la description cabe en el token budget del tool schema (~100 tokens)
- [ ] No cambiar el nombre del tool (backward compat con classify_intent patterns)

### Phase 6: Settings
- [ ] `app/config.py` nuevos settings:
  ```python
  web_search_fetch_top_n: int = 3          # pages to fetch in detailed mode
  web_search_fetch_timeout: float = 8.0    # per-page fetch timeout
  web_search_extract_page_limit: int = 2500 # chars per page sent to LLM extraction
  ```
- [ ] Defaults conservadores: 3 pages, 8s timeout, 2500 chars — balancean calidad vs latencia
- [ ] Test: settings override

### Phase 7: Langfuse Observability — Spans para `web_search` enhanced

> **Contexto**: El executor ya crea `tool:web_search` (kind="tool") con `set_input(arguments)` y `set_output(content[:1000])`.
> Para `depth="quick"`, no se agrega nada nuevo (comportamiento actual).
> Para `depth="detailed"`, creamos sub-spans internos que capturan cada fase del pipeline.

- [ ] **Span raíz** `web_search:detailed` (kind="span") — solo cuando `depth="detailed"`
  - `set_input`:
    ```python
    {
        "query": query,
        "depth": "detailed",
        "time_range": time_range,
        "urls_to_fetch": urls[:fetch_top_n],
        "fetch_top_n": settings.web_search_fetch_top_n,
    }
    ```
  - `set_output` (al final):
    ```python
    {
        "search_results_count": len(search_results),
        "pages_attempted": len(urls_to_fetch),
        "pages_successful": len(successful_pages),
        "pages_failed": len(failed_pages),
        "extraction_chars": len(extracted_text),
        "latency_search_ms": search_elapsed,
        "latency_fetch_ms": fetch_elapsed,
        "latency_extract_ms": extract_elapsed,
        "latency_total_ms": total_elapsed,
    }
    ```

- [ ] **Sub-span** `web_search:fetch` (kind="span") — fetch paralelo de páginas
  - `set_input`:
    ```python
    {
        "urls": urls_to_fetch,
        "timeout": settings.web_search_fetch_timeout,
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
        "success_rate": f"{ok}/{attempted}",
        "latency_ms": fetch_elapsed,
    }
    ```

- [ ] **Sub-span** `llm:web_extract` (kind="generation") — LLM extraction call
  - `set_input`:
    ```python
    {
        "query": query,
        "pages_count": len(successful_pages),
        "pages": [
            {"url": url, "chars": len(text), "preview": text[:300]}
            for url, text in successful_pages
        ],
        "total_input_chars": sum(len(t) for _, t in successful_pages),
        "page_limit_chars": settings.web_search_extract_page_limit,
    }
    ```
  - `set_metadata`:
    ```python
    {
        "gen_ai.request.model": model_name,
        "think": False,
    }
    ```
  - `set_output`:
    ```python
    {
        "extracted_chars": len(extracted),
        "extracted_preview": extracted[:500],
        "latency_ms": extract_elapsed,
    }
    ```

- [ ] **Span `web_search:quick`** (kind="span") — para `depth="quick"` (opcional, baja prioridad)
  - Solo si queremos comparar quick vs detailed en Langfuse dashboards
  - `set_input`: `{"query": query, "depth": "quick", "time_range": time_range}`
  - `set_output`: `{"results_count": len(results), "latency_ms": elapsed}`
  - Nota: esto agrega overhead mínimo pero permite filtrar por depth en Langfuse

- [ ] Tests: mock `get_current_trace()`, verify:
  - `depth="quick"` → no sub-spans (o solo `web_search:quick`)
  - `depth="detailed"` → `web_search:detailed` + `web_search:fetch` + `llm:web_extract` creados
  - Cada span tiene set_input/set_output con keys esperadas
  - kind="generation" en `llm:web_extract`, kind="span" en los demás

### Phase 8: Validación
- [ ] `ruff check` pass
- [ ] `mypy app/` pass
- [ ] `pytest` pass (todos los tests existentes + nuevos)
- [ ] Test manual: deploy, enviar "precio del dólar hoy" → verificar que extrae datos reales
- [ ] Test manual: enviar "capital de Francia" → verificar que NO hace fetch (depth=quick por default)
- [ ] Verificar en Langfuse:
  - `tool:web_search` (executor) muestra `depth` en arguments
  - `web_search:detailed` muestra resumen con success_rate y latencias
  - `web_search:fetch` muestra status de cada URL individualmente
  - `llm:web_extract` muestra preview del input (páginas) y output (extracción)
  - Para quick mode: no sub-spans innecesarios (o span liviano)
