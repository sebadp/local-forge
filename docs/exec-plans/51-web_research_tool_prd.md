# PRD: Deep Web Research Tool — `web_research` (Plan 51)

## Objetivo y Contexto

### Problema

En sesión real (2026-03-18), el LLM ejecutó `web_search` 3 veces para "fixture Rosario Central 2026" y obtuvo URLs relevantes (ESPN, rosariocentral.com) pero **nunca fetcheó el contenido real** de esas páginas. Respondió "no encontré información" cuando los datos estaban a un click.

**Root cause**: `classify_intent()` retorna `["search", "time"]` → `select_tools()` solo incluye `web_search` + tools de tiempo. Los fetch tools (Puppeteer/mcp-fetch) están en categoría `"fetch"` separada. Un modelo 9B no encadena tools confiablemente (consensus de industria: Firecrawl, Jina, Tavily convergen en composite tools para modelos <30B).

### Solución

Crear un tool composite `web_research` que internamente hace: **multi-query search → parallel fetch → content extraction → chunk + embed + semantic rank → return top chunks**. El LLM llama 1 tool y recibe contenido relevante de múltiples páginas.

**Pattern de industria**: "Scout then Deep-Read" (Tavily Extract, Perplexity Sonar, Crawl4AI cosine-sim filter).

## Alcance

### In Scope
- **`web_research` composite tool**: search + fetch + extract + chunk + embed + rank
- **Auto-include `"fetch"` cuando `"search"` es clasificado**: quick win en router.py
- **Content extraction pipeline**: httpx + trafilatura para HTML→texto limpio
- **Chunk + Embed + Rank**: split por headings → embed con nomic-embed-text → cosine similarity → top-K chunks
- **Multi-query search**: query original + variante programática en paralelo
- **Retry mechanism**: si no hay chunks relevantes, buscar con query refinado (max 2 rounds)
- **Dependency nueva**: `trafilatura` para content extraction (F1=0.937, ~100ms/page)

### Out of Scope
- Cambios al `web_search` existente (Plan 52 separado)
- LLM-based extraction (eso es Plan 52, Approach C)
- Puppeteer/browser rendering (MCP tools siguen disponibles para JS-heavy sites)
- Fine-tuning de embeddings o modelos
- Persistencia de chunks en sqlite-vec (no se guarda, es ephemeral per-request)
- Reranking con cross-encoder (overkill para este use case, cosine similarity es suficiente)

## Casos de Uso Críticos

### 1. Usuario pide datos específicos de la web → recibe datos reales

**Antes:** "Fixture de Rosario Central" → LLM busca 3 veces, nunca fetcha, dice "no encontré info".
**Después:** "Fixture de Rosario Central" → `web_research` busca, fetcha ESPN + rosariocentral.com, extrae la tabla de fixtures, retorna chunks con fechas y horarios.

### 2. Información distribuida en múltiples fuentes → consolidación automática

**Antes:** LLM solo ve snippets de DuckDuckGo ("Rosario Central Calendario 2026 - ESPN (AR)").
**Después:** `web_research` fetcha 8 páginas en paralelo, rankea chunks por relevancia, retorna los fragmentos más informativos de cada fuente.

### 3. Datos enterrados profundo en una página → extracción precisa

**Antes:** Truncar a 1500 chars pierde la tabla de fixtures que está en posición 3000+.
**Después:** Chunk por headings, embed cada chunk, cosine similarity contra la query → el chunk con la tabla rankea alto aunque esté profundo en la página.

### 4. Quick win: fetch tools disponibles cuando se busca

**Antes:** `classify_intent() → ["search"]` → solo `web_search` disponible.
**Después:** `["search"]` auto-incluye `["search", "fetch"]` → Puppeteer/mcp-fetch disponibles como fallback.

## Restricciones Arquitectónicas

- **Modelo**: qwen3.5:9b con nomic-embed-text — embeddings son baratos (local, ~500ms para 30 chunks)
- **Context window**: 32K tokens. Tool output max ~12000 chars (~3000 tokens) para dejar espacio al sistema
- **Compaction**: outputs >20000 chars se compactan via `compact_tool_output()`. Apuntar a <15000 chars
- **No LLM en pipeline de extraction**: todo el pipeline pre-LLM es determinístico (trafilatura + embeddings). Solo el LLM principal sintetiza la respuesta final
- **Timeout total**: max 20 segundos para el tool completo (search + fetch + extract + rank)
- **Dependency**: `trafilatura` es GPL-3.0+ — compatible con el proyecto
- **httpx**: ya es dependency del proyecto
- **Embeddings**: `OllamaClient.embed()` ya existe, usa `nomic-embed-text` (768 dims)
- **nomic-embed-text prefixes**: usar `search_query:` para la query y `search_document:` para chunks (mejora accuracy)

## Observabilidad (Langfuse)

El tool genera una jerarquía de spans para trazabilidad completa:

```
tool:web_research (executor, automático)
└── web_research:pipeline (handler)
    ├── web_research:search    → queries usadas, URLs encontradas, dedup stats
    ├── web_research:fetch     → status por URL (ok/error/timeout/empty), chars extraídos
    ├── web_research:rank      → chunks con similarity scores, embedding model, threshold
    └── web_research:retry     → (condicional) razón del retry, nuevo query, resultados
```

**Métricas clave para evaluar en Langfuse dashboards**:
- `success_rate` de fetch (% de páginas que retornan contenido útil)
- `top_similarity` (calidad del ranking — si es consistentemente baja, el embedding model o chunking necesita ajuste)
- `retry_triggered` rate (si es >50%, la primera query no es lo suficientemente buena)
- `latency_*_ms` por fase (detectar cuellos de botella: search vs fetch vs embed)
- `is_multiquery` flag para filtrar traces multi-query vs single-query
- `pages_failed` con `error` detail para detectar dominios que bloquean fetching

## Latencia Estimada

| Paso | Tiempo | Parallelizable |
|------|--------|:-:|
| 2× search DuckDuckGo | ~3s | Sí |
| 8× httpx fetch | ~5s | Sí |
| 8× trafilatura extract | ~0.5s | Sí (to_thread) |
| Split en ~30 chunks | ~10ms | — |
| Embed 30 chunks + 1 query | ~1s | Batch |
| Cosine similarity ranking | ~1ms | — |
| **Total pre-LLM** | **~5-8s** | |
| LLM síntesis (1 iteration) | ~10s | — |
| **Total end-to-end** | **~15-18s** | |

vs. **actual**: ~30 segundos (3 tool iterations × 10s cada una). **2x más rápido con mejores resultados.**
