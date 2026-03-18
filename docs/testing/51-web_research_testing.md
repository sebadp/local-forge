# Testing Manual: Deep Web Research Tool (`web_research`)

> **Feature documentada**: [`docs/features/51-web_research.md`](../features/51-web_research.md)
> **Requisitos previos**: Container corriendo (`docker compose up -d`), modelos `qwen3.5:9b` y `nomic-embed-text` disponibles en Ollama.

---

## Verificar que la feature está activa

Al arrancar el container, buscar en los logs:

```bash
docker compose logs -f localforge | head -60
```

Confirmar las siguientes líneas:
- `Registered tool: web_research (skill: search)` — tool registrado correctamente
- `Registered tool: web_search (skill: search)` — web_search sigue disponible

---

## Casos de prueba principales

| # | Mensaje | Resultado esperado |
|---|---|---|
| 1 | "necesito el fixture de Rosario Central con fechas y horas" | Respuesta con fechas reales de partidos, citando fuentes (ESPN, sitio oficial, etc.) |
| 2 | "precio del dólar hoy en Argentina" | Cotización actualizada con valores numéricos concretos |
| 3 | "horarios de la próxima fecha de la Liga Profesional" | Tabla o lista con horarios específicos y equipos |
| 4 | "specs del iPhone 16 Pro Max" | Especificaciones técnicas reales (RAM, batería, cámara) |
| 5 | "receta de pasta al pesto con cantidades" | Ingredientes con cantidades exactas (gramos, cucharadas) |
| 6 | "resultados de la Champions League de ayer" | Marcadores reales con equipos y goles |
| 7 | "qué restaurantes hay cerca de Plaza España Rosario" | Lista de restaurantes con nombres y direcciones reales |

---

## Edge cases y validaciones

| # | Escenario | Resultado esperado |
|---|---|---|
| E1 | Query muy genérica: "noticias" | `web_research` retorna contenido (puede usar snippets + fetched pages) |
| E2 | Query imposible: "alksjdflkajsdflkajsdf" | Mensaje indicando que no encontró resultados relevantes |
| E3 | Sitios que bloquean fetch (403/timeout) | Respuesta parcial con los sitios que sí funcionaron, o fallback a snippets |
| E4 | Query en inglés: "latest NASA discoveries 2026" | Funciona correctamente (DuckDuckGo es agnóstico de idioma) |
| E5 | Segundo mensaje de seguimiento: "y cuándo juega de local?" | El LLM debería usar sticky categories para reclasificar como search |
| E6 | `web_research` + `web_search` disponibles | LLM elige `web_research` para datos específicos, `web_search` para queries simples |

---

## Verificar en logs

```bash
# Actividad de web_research
docker compose logs -f localforge 2>&1 | grep -i "web_research"

# Ver chunks rankeados y similarity
docker compose logs -f localforge 2>&1 | grep "chunks ranked"

# Ver retry (si se activa)
docker compose logs -f localforge 2>&1 | grep "retry"

# Errores de fetch
docker compose logs -f localforge 2>&1 | grep -i "fetch_page failed"

# Errores de embedding
docker compose logs -f localforge 2>&1 | grep -i "embedding/ranking failed"
```

Logs esperados para un request exitoso:
```
INFO: Searching web for: fixture Rosario Central (time_range=None, depth=quick)
INFO: web_research: 6 chunks ranked, top_sim=0.742, retry=False, 8532ms
```

---

## Verificar en Langfuse

1. Abrir Langfuse (`http://localhost:3000`)
2. Buscar trace reciente con input que contenga "fixture" o la query usada
3. Verificar jerarquía de spans:

```
tool:web_research (creado automáticamente por executor)
└── web_research:pipeline
    ├── web_research:search    → queries, URLs encontradas
    ├── web_research:fetch     → status por URL (ok/empty), chars
    ├── web_research:rank      → similarity scores, chunks preview
    └── web_research:retry     → (solo si retry fue necesario)
```

**Métricas a monitorear:**
- `success_rate` de fetch (% de páginas con contenido) — idealmente > 50%
- `top_similarity` — si es consistentemente < 0.3, revisar embedding model o chunking
- `retry_triggered` — si es > 50%, la primera query no es suficiente
- `latency_total_ms` — target < 10s para pipeline pre-LLM

---

## Verificar graceful degradation

1. **Sin Ollama embeddings**: parar el modelo `nomic-embed-text`:
   ```bash
   docker compose exec ollama ollama rm nomic-embed-text
   ```
   - Verificar que `web_research` retorna chunks unranked (sin error)
   - En logs: `"web_research: embedding/ranking failed, using unranked chunks"`
   - Restaurar: `docker compose exec ollama ollama pull nomic-embed-text`

2. **Sin internet / DuckDuckGo caído**: desconectar red del container
   - Verificar: retorna "No results found" o "Error performing web research"
   - No debe crashear el server

3. **Todos los fetch fallan (403)**: si todos los sitios bloquean
   - Verificar: retorna snippets de búsqueda como fallback
   - En logs: `"Could not fetch page content"`

---

## Test cases automatizados

Los 38 tests unitarios cubren:

```bash
# Correr solo tests de web_research
.venv/bin/python -m pytest tests/test_web_research.py -v
```

| Test Class | Qué cubre |
|---|---|
| `TestChunkText` (6 tests) | Split por headings, paragraphs, merge, oversized, filter, empty |
| `TestCosineSimilarity` (4 tests) | Identical, orthogonal, opposite, zero vectors |
| `TestRankChunks` (3 tests) | Ranking order, threshold filter, empty input |
| `TestSearchVariant` (5 tests) | Adds year, year present, rotates, different from original, retry different |
| `TestDedupUrls` (5 tests) | Removes dupes, trailing slash, query params, empty, preserves original |
| `TestFormatOutput` (3 tests) | Basic format, char limit, empty chunks |
| `TestWebResearchRegistration` (2 tests) | Tool registered, description |
| `TestWebResearchPipeline` (6 tests) | Full pipeline, no results, no fetch, error handling, max_pages, no ollama |
| `TestWebResearchObservability` (1 test) | Span names and kinds |
| `TestRouterAutoIncludeFetch` (3 tests) | Auto-include fetch, without fetch, web_research in category |

---

## Troubleshooting

| Problema | Causa probable | Solución |
|---|---|---|
| `web_research` nunca se selecciona | Classifier no clasifica como `"search"` | Verificar que `"web_research"` está en `TOOL_CATEGORIES["search"]` |
| Respuestas sin datos concretos | Todos los fetch fallan (403/timeout) | Revisar User-Agent en `web_extraction.py`; probar con otro query |
| Similarity scores muy bajos (< 0.2) | Query y contenido de páginas no matchean | Ajustar `web_research_similarity_threshold` a 0.1 o 0.0 |
| Timeout del tool | fetch_timeout muy bajo o muchas páginas | Aumentar `web_research_fetch_timeout` o reducir `web_research_max_pages` |
| Output truncado / compactado | Output > 20000 chars triggerea compaction | Reducir `web_research_max_output_chars` o `web_research_top_k` |
| "embedding/ranking failed" en logs | Modelo `nomic-embed-text` no disponible | `docker compose exec ollama ollama pull nomic-embed-text` |

---

## Variables relevantes para testing

| Variable (`.env`) | Valor de test | Efecto |
|---|---|---|
| `WEB_RESEARCH_MAX_PAGES` | `4` | Reduce páginas a fetchear (más rápido para test) |
| `WEB_RESEARCH_FETCH_TIMEOUT` | `5.0` | Timeout más corto para testing |
| `WEB_RESEARCH_TOP_K` | `4` | Menos chunks en output |
| `WEB_RESEARCH_SIMILARITY_THRESHOLD` | `0.0` | Incluye todos los chunks (sin filtrar por relevancia) |
| `WEB_RESEARCH_MAX_OUTPUT_CHARS` | `6000` | Output más corto para verificar cap |
