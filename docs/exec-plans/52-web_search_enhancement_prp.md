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
- [x] Crear PRD
- [x] Crear PRP
- [x] Actualizar `docs/exec-plans/README.md` con Plan 52

### Phase 1: Shared Extraction Utilities
> Si Plan 51 ya fue implementado, esta fase se salta — reutilizar `web_extraction.py`.

- [x] Crear `app/skills/tools/web_extraction.py` con:
  - `async fetch_page(url: str, timeout: float = 8.0) -> str | None`: httpx GET con headers razonables
  - `extract_text(html: str) -> str`: `trafilatura.extract()` con fallback a regex stripping
  - `async fetch_and_extract(url: str, timeout: float = 8.0) -> tuple[str, str | None]`: convenience wrapper
  - `async fetch_multiple(urls: list[str], timeout: float = 8.0, max_concurrent: int = 4) -> list[tuple[str, str | None]]`: parallel fetch+extract con semaphore
- [x] Agregar `trafilatura>=2.0` a `pyproject.toml` (si no está)
- [x] Tests unitarios para cada función (mock httpx)

### Phase 2: LLM Extraction Prompt
- [x] Definir prompt de extracción en `search_tools.py` (constante `_EXTRACT_PROMPT`)
- [x] Función `async _llm_extract(query: str, pages: list[tuple[str, str]], ollama_client) -> str`:
  - Construye messages: system=_EXTRACT_PROMPT, user="Query: {query}\n\n" + page contents
  - Trunca cada página a `web_search_extract_page_limit` chars (default 2500)
  - Llama a `ollama_client.chat(messages, think=False)`
  - Return: texto extraído
- [x] Test: mock ollama_client, verify prompt construction, verify think=False

### Phase 3: `web_search` Enhancement
- [x] Agregar parámetro `depth` al tool schema (enum: `["quick", "detailed"]`)
- [x] En handler `web_search()`: quick returns snippets, detailed fetches + extracts
- [x] `depth` default es `"quick"` → backward compatible, zero behavior change sin opt-in
- [x] Test: verify quick mode unchanged, detailed mode calls fetch+extract

### Phase 4: OllamaClient Access in Handler
- [x] Implementado via Option C: closure sobre el client en `register(ollama_client=...)`
- [x] `__init__.py` actualizado para pasar `ollama_client` y `settings` a `register_search()`
- [x] Test: verify client is accessible from handler (detailed mode works)

### Phase 5: Tool Description Update
- [x] Actualizar description de `web_search` con mención de `depth='detailed'`
- [x] Verificar que la description cabe en el token budget del tool schema
- [x] No cambiar el nombre del tool (backward compat con classify_intent patterns)

### Phase 6: Settings
- [x] `app/config.py` nuevos settings:
  ```python
  web_search_fetch_top_n: int = 3          # pages to fetch in detailed mode
  web_search_fetch_timeout: float = 8.0    # per-page fetch timeout
  web_search_extract_page_limit: int = 2500 # chars per page sent to LLM extraction
  ```
- [x] Defaults conservadores: 3 pages, 8s timeout, 2500 chars — balancean calidad vs latencia
- [x] Test: settings override

### Phase 7: Langfuse Observability — Spans para `web_search` enhanced

> **Contexto**: El executor ya crea `tool:web_search` (kind="tool") con `set_input(arguments)` y `set_output(content[:1000])`.
> Para `depth="quick"`, no se agrega nada nuevo (comportamiento actual).
> Para `depth="detailed"`, creamos sub-spans internos que capturan cada fase del pipeline.

- [x] **Span raíz** `web_search:detailed` (kind="span") — solo cuando `depth="detailed"`
- [x] **Sub-span** `web_search:fetch` (kind="span") — fetch paralelo de páginas
- [x] **Sub-span** `llm:web_extract` (kind="generation") — LLM extraction call
- [ ] **Span `web_search:quick`** (kind="span") — para `depth="quick"` (opcional, baja prioridad)
- [x] Tests: mock `get_current_trace()`, verify spans created with correct names and kinds

### Phase 8: Validación
- [x] `ruff check` pass
- [x] `mypy app/` pass
- [x] `pytest` pass (802 tests, todos existentes + 17 nuevos)
- [ ] Test manual: deploy, enviar "precio del dólar hoy" → verificar que extrae datos reales
- [ ] Test manual: enviar "capital de Francia" → verificar que NO hace fetch (depth=quick por default)
- [ ] Verificar en Langfuse spans
