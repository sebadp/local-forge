# Feature: Web Search Enhanced — Smart Fetch & LLM Extract

> **Versión**: v1.0
> **Fecha de implementación**: 2026-03-18
> **Exec Plan**: Plan 52
> **Estado**: ✅ Implementada

---

## ¿Qué hace?

Mejora la tool `web_search` con un parámetro `depth`. En modo `"quick"` (default) funciona igual que antes: retorna snippets de DuckDuckGo. En modo `"detailed"`, además de los snippets, fetcha las páginas top y usa un LLM para extraer datos concretos (precios, fechas, horarios, listas) del contenido real de las páginas.

---

## Arquitectura

```
[Usuario pregunta "precio del dólar hoy"]
        │
        ▼
[LLM selecciona web_search(depth="detailed")]
        │
        ▼
[DuckDuckGo search] ──► snippets (siempre)
        │
        │  depth="detailed"
        ▼
[fetch_multiple] ──► httpx GET top 3 URLs en paralelo
        │                 │
        │           [trafilatura extract_text]
        │                 │
        ▼                 ▼
[_llm_extract] ──► qwen3.5:9b (think=False)
        │              extracts: "Dólar oficial: $1050, Blue: $1180"
        ▼
[snippets + "## Extracted content from top results:\n\n" + extracted]
```

Para `depth="quick"`, el flujo se corta después de DuckDuckGo y retorna solo los snippets.

---

## Archivos clave

| Archivo | Rol |
|---|---|
| `app/skills/tools/search_tools.py` | Handler `web_search` con `depth` param, `_llm_extract()`, `_EXTRACT_PROMPT`, spans Langfuse |
| `app/skills/tools/web_extraction.py` | Utilidades compartidas: `fetch_page`, `extract_text`, `fetch_multiple` |
| `app/config.py` | Settings: `web_search_fetch_top_n`, `web_search_fetch_timeout`, `web_search_extract_page_limit` |
| `app/skills/tools/__init__.py` | Pasa `ollama_client` y `settings` a `register_search()` |
| `tests/test_web_search_enhanced.py` | Tests para ambos modos, extracción, observabilidad |
| `tests/test_search_tools.py` | Tests originales (backward compat) |

---

## Walkthrough técnico: cómo funciona

### Modo Quick (default)

1. **DuckDuckGo search**: `_perform_search()` ejecuta `DDGS().text()` en `run_in_executor` (sync → async) → `search_tools.py:32`
2. **Format snippets**: `_format_snippets()` convierte resultados en markdown numerado → `search_tools.py:42`
3. **Return**: los snippets se retornan directamente → `search_tools.py:126`

### Modo Detailed

1. **DuckDuckGo search**: igual que quick → `search_tools.py:112`
2. **URL extraction**: toma `href` de los top N resultados (default 3) → `search_tools.py:131`
3. **Parallel fetch**: `fetch_multiple()` hace httpx GET en paralelo con semaphore (max 4 concurrent) → `web_extraction.py:72`
4. **Content extraction**: `trafilatura.extract()` convierte HTML a texto limpio (fallback: regex stripping) → `web_extraction.py:40`
5. **LLM extraction**: `_llm_extract()` envía las páginas exitosas + query al LLM con `think=False` → `search_tools.py:53`
   - System prompt: `_EXTRACT_PROMPT` pide datos exactos, fechas, precios → `search_tools.py:21`
   - Cada página se trunca a `page_limit` chars (default 2500)
   - El LLM retorna un resumen estructurado con la información relevante
6. **Combine output**: snippets originales + `"---\n## Extracted content..."` + texto extraído → `search_tools.py:256`
7. **Langfuse spans**: si tracing está activo, crea sub-spans jerárquicos → `search_tools.py:137`

### Graceful degradation

- Si `ollama_client` no está disponible → detailed degrada a quick → `search_tools.py:125`
- Si todos los fetches fallan → retorna snippets + mensaje de fallback → `search_tools.py:193`
- Si el search no retorna resultados → mensaje "No results found" → `search_tools.py:119`

---

## Cómo extenderla

- **Cambiar cuántas páginas fetchar**: `WEB_SEARCH_FETCH_TOP_N=5` en `.env`
- **Ajustar el timeout de fetch**: `WEB_SEARCH_FETCH_TIMEOUT=12.0` en `.env`
- **Más contenido por página al LLM**: `WEB_SEARCH_EXTRACT_PAGE_LIMIT=4000` en `.env`
- **Modificar el prompt de extracción**: editar `_EXTRACT_PROMPT` en `search_tools.py:21`
- **Reutilizar web_extraction.py**: Plan 51 (`web_research`) comparte estas utilidades

---

## Guía de testing

→ Ver [`docs/testing/52-web_search_enhanced_testing.md`](../testing/52-web_search_enhanced_testing.md)

---

## Decisiones de diseño

| Decisión | Alternativa descartada | Motivo |
|---|---|---|
| LLM extraction con `think=False` | Embeddings + cosine similarity (Approach B) | Más simple, single call, no requiere pipeline de chunking. Approach B es Plan 51 |
| Closure sobre `ollama_client` en `register()` | Global app state import | Consistente con el pattern de `notes_tools.py`, `eval_tools.py`, etc. |
| trafilatura para HTML→texto | BeautifulSoup, readability | trafilatura tiene F1=0.937, ~100ms/page, incluye tablas. Fallback regex si falla |
| Truncar a 2500 chars/página | Enviar página completa | 3 páginas × 2500 = 7500 chars → cabe en context window de 32K. Más estable |
| `depth` default `"quick"` | Default `"detailed"` | Backward compat: zero behavior change para calls existentes sin el param |
| Spans Langfuse solo en detailed | Spans en ambos modos | Quick no agrega valor de observabilidad (ya lo traza el executor) |

---

## Gotchas y edge cases

- **Sitios que bloquean fetch**: algunos sitios retornan 403/captcha. `fetch_page` retorna `None` y se saltea. Si todos fallan, el usuario recibe snippets + mensaje explicativo
- **Contenido corto (<50 chars)**: `fetch_and_extract` descarta páginas con <50 chars post-extraction (probablemente páginas de error o redirects)
- **`think=False` obligatorio**: el prompt de extraction es utilitario, no necesita chain-of-thought. Si se omite, qwen3 activa thinking por default y agrega latencia innecesaria
- **`ollama_client._model`**: el span `llm:web_extract` accede a `_model` (private) para metadata. Si la interfaz cambia, actualizar el span
- **Backward compat de tests**: los tests originales en `test_search_tools.py` llaman `register(reg)` sin `ollama_client` — funciona porque el parámetro es optional y quick mode no lo necesita

---

## Variables de configuración relevantes

| Variable (`config.py`) | Default | Efecto |
|---|---|---|
| `web_search_fetch_top_n` | `3` | Cuántas páginas fetchar en modo detailed |
| `web_search_fetch_timeout` | `8.0` | Timeout por página (segundos) |
| `web_search_extract_page_limit` | `2500` | Chars por página enviados al LLM de extracción |
