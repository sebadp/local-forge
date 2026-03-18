# Testing Manual: Web Search Enhanced (Plan 52)

> **Feature documentada**: [`docs/features/52-web_search_enhanced.md`](../features/52-web_search_enhanced.md)
> **Requisitos previos**: Container corriendo (`docker compose up -d`), Ollama con `qwen3.5:9b` disponible.

---

## Verificar que la feature está activa

Al arrancar el container, buscar en los logs:

```bash
docker compose logs -f localforge | head -60
```

Confirmar las siguientes líneas:
- `Registered tool: web_search (skill: search)` — la tool se registró correctamente

Verificar que el depth param está en el schema:

```bash
docker compose logs -f localforge 2>&1 | grep "web_search"
```

---

## Casos de prueba principales — Modo Quick

| Mensaje / Acción | Resultado esperado |
|---|---|
| "¿Cuál es la capital de Francia?" | Responde directamente. El LLM usa `depth="quick"` (default), retorna snippets sin fetchar páginas |
| "Busca información sobre Python 3.12" | Snippets de DuckDuckGo, respuesta rápida (<3s) |
| "¿Qué es Docker?" | Quick mode, solo snippets. Sin "Extracted content" en la respuesta |

### Verificar en logs (Quick mode)

```bash
docker compose logs -f localforge 2>&1 | grep "Searching web"
```

Esperado:
```
Searching web for: capital de Francia (time_range=None, depth=quick)
Found 5 results for: capital de Francia
```

**No debe aparecer** `web_search detailed:` en los logs para queries simples.

---

## Casos de prueba principales — Modo Detailed

| Mensaje / Acción | Resultado esperado |
|---|---|
| "¿Cuál es el precio del dólar hoy?" | El LLM usa `depth="detailed"`, fetcha páginas de Ámbito/DolarHoy, extrae precios reales |
| "Fixture de Rosario Central con fechas y horas" | Detailed mode, fetcha ESPN/sitio oficial, extrae tabla de partidos con fechas |
| "Receta de pasta carbonara con ingredientes" | Fetcha páginas de recetas, extrae lista de ingredientes y pasos |
| "Horarios del cine en Rosario hoy" | Fetcha cartelera, extrae películas y horarios concretos |

### Verificar en logs (Detailed mode)

```bash
docker compose logs -f localforge 2>&1 | grep "web_search detailed"
```

Esperado:
```
web_search detailed: fetched 2/3 pages, extracted 847 chars in 6523ms
```

---

## Edge cases y validaciones

| Escenario | Resultado esperado |
|---|---|
| Query con resultados pero todas las páginas bloquean fetch (403s) | Retorna snippets + "(Could not fetch page content...)" |
| Query sin resultados en DuckDuckGo | "No results found for '...'" (aplica a ambos modos) |
| `depth="detailed"` pero Ollama no disponible | Degrada silenciosamente a modo quick (solo snippets) |
| Página con contenido muy corto (<50 chars) | Se descarta, no se envía al LLM |
| `depth="invalid_value"` | Se trata como quick (no es "detailed") |
| Query con `time_range="d"` + `depth="detailed"` | Combina ambos: busca últimas 24h y fetcha páginas |

---

## Verificar en Langfuse

1. Abrir Langfuse → Traces → buscar trace reciente
2. Encontrar span `tool:web_search` (creado por executor)
3. Verificar `arguments` incluye `depth` en el input

### Para depth="detailed"

Dentro del span `tool:web_search`, verificar sub-spans:

- **`web_search:detailed`** (kind="span"):
  - Input: `query`, `depth`, `urls_to_fetch`, `fetch_top_n`
  - Output: `pages_attempted`, `pages_successful`, `pages_failed`, `extraction_chars`, latencias desglosadas

- **`web_search:fetch`** (kind="span"):
  - Input: `urls`, `timeout`
  - Output: status por URL (`ok`/`empty`), `success_rate`, `latency_ms`

- **`llm:web_extract`** (kind="generation"):
  - Input: `query`, `pages_count`, previews por página, `page_limit_chars`
  - Metadata: `gen_ai.request.model`, `think: false`
  - Output: `extracted_chars`, `extracted_preview`, `latency_ms`

### Para depth="quick"

- **No debe haber** sub-spans `web_search:detailed`, `web_search:fetch`, ni `llm:web_extract`

---

## Verificar en logs

```bash
# Actividad general de web_search
docker compose logs -f localforge 2>&1 | grep "Searching web"

# Solo modo detailed
docker compose logs -f localforge 2>&1 | grep "web_search detailed"

# Fetch errors
docker compose logs -f localforge 2>&1 | grep "fetch_page failed"

# Search errors
docker compose logs -f localforge 2>&1 | grep "Search failed"
```

---

## Tests automatizados

```bash
# Tests específicos de Plan 52 (17 tests)
.venv/bin/python -m pytest tests/test_web_search_enhanced.py -v

# Tests originales de backward compat (4 tests)
.venv/bin/python -m pytest tests/test_search_tools.py -v

# Ambos juntos
.venv/bin/python -m pytest tests/test_web_search_enhanced.py tests/test_search_tools.py -v
```

---

## Verificar graceful degradation

1. **Sin Ollama**: Detener Ollama → enviar query → detailed degrada a quick (solo snippets)
2. **Sin internet**: Sin conexión → search falla → retorna "Error performing search: ..."
3. **Sitios bloqueados**: Si todas las páginas retornan 403 → snippets + mensaje de fallback
4. **trafilatura falla**: Si trafilatura no puede extraer → fallback a regex stripping de HTML

---

## Troubleshooting

| Problema | Causa probable | Solución |
|---|---|---|
| Detailed mode siempre retorna solo snippets | `ollama_client` no se pasa a `register()` | Verificar `__init__.py` pasa `ollama_client` |
| Fetch siempre falla (0/3 pages) | Sitios bloquean el User-Agent | Verificar `_HEADERS` en `web_extraction.py` |
| LLM extraction retorna contenido irrelevante | Prompt necesita ajuste | Editar `_EXTRACT_PROMPT` en `search_tools.py` |
| "Error performing search" | DuckDuckGo rate limiting | Esperar unos minutos, o verificar conectividad |
| Spans no aparecen en Langfuse | Tracing deshabilitado | `TRACING_ENABLED=true` en `.env` |
| Extraction muy lenta (>15s) | Páginas pesadas o Ollama saturado | Reducir `WEB_SEARCH_FETCH_TOP_N` o `WEB_SEARCH_EXTRACT_PAGE_LIMIT` |

---

## Variables relevantes para testing

| Variable (`.env`) | Valor de test | Efecto |
|---|---|---|
| `WEB_SEARCH_FETCH_TOP_N` | `3` (default) | Reducir a `1` para tests rápidos |
| `WEB_SEARCH_FETCH_TIMEOUT` | `8.0` (default) | Reducir a `3.0` en entornos lentos |
| `WEB_SEARCH_EXTRACT_PAGE_LIMIT` | `2500` (default) | Aumentar a `4000` si la extracción es insuficiente |
| `TRACING_ENABLED` | `true` | Necesario para verificar spans en Langfuse |
