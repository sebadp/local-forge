# PRD: Web Search Enhancement — Smart Fetch & LLM Extract (Plan 52)

## Objetivo y Contexto

### Problema

`web_search` retorna solo títulos, URLs y snippets de DuckDuckGo (~100 chars por resultado). Para muchas queries, esto es insuficiente:

- "fixture Rosario Central con fecha y hora" → snippet dice "Rosario Central Calendario 2026 - ESPN (AR)" pero no tiene las fechas/horas reales
- "precio del dólar hoy" → snippet dice "Cotización del Dólar - Ámbito Financiero" pero no el precio actual
- "receta de pasta carbonara con ingredientes" → snippet dice "Receta fácil de pasta carbonara" pero no los ingredientes

El LLM recibe snippets insuficientes y tiene dos opciones malas:
1. Fabricar datos a partir de snippets vagos (hallucination)
2. Decir "no encontré la información" (failure)

### Solución: Approach C — LLM Extraction

Agregar un parámetro `depth` a `web_search`:
- `"quick"` (default): comportamiento actual — solo snippets, <3 segundos
- `"detailed"`: auto-fetch top páginas + **LLM extraction** (single call) → datos específicos, ~10-15 segundos

**Approach C** usa el LLM (qwen3.5:9b con `think=False`) para extraer información relevante de las páginas fetcheadas. A diferencia de `web_research` (Plan 51, Approach B que usa embeddings), este approach es:
- **Más simple**: no requiere pipeline de embeddings ni chunking semántico
- **Más flexible**: el LLM entiende contexto y puede extraer datos no-verbatim (tablas, listas, relaciones)
- **Trade-off**: usa un LLM call extra (~5s), pero es un solo call con todo el contenido

### Relación con Plan 51

| | Plan 51: `web_research` | Plan 52: `web_search` enhanced |
|---|---|---|
| **Approach** | B — Chunk + Embed + Rank | C — LLM Extraction |
| **Depth** | Profundo: 8-12 páginas, multi-query | Medio: 2-3 páginas, single query |
| **Extraction** | Determinístico (embeddings) | LLM-based (más flexible) |
| **Latencia** | ~15-20s | ~10-15s |
| **Uso ideal** | Investigación exhaustiva, comparaciones | Datos puntuales: precios, horarios, listas |
| **Complejidad** | Alta (chunk+embed pipeline) | Media (fetch + single LLM call) |

Los dos tools se complementan. El LLM elige según la tool description:
- `web_search` → "Busco datos puntuales de la web"
- `web_research` → "Necesito investigar a fondo múltiples fuentes"

**Plan 52 puede implementarse independientemente de Plan 51.** Ambos usan `trafilatura` + `httpx` para fetching, pero Plan 52 no necesita embeddings.

## Alcance

### In Scope
- Agregar parámetro `depth: "quick" | "detailed"` a `web_search`
- Auto-fetch top 3 páginas cuando `depth="detailed"`
- LLM extraction: single call con `think=False` para extraer datos relevantes
- Tool description actualizada para que el LLM sepa cuándo usar `depth="detailed"`
- Shared extraction utilities con Plan 51 (si ya existe `web_extraction.py`, reusar)

### Out of Scope
- Cambios al pipeline de embeddings (eso es Plan 51)
- Multi-query search (eso es Plan 51)
- Retry/loop mechanism (Plan 51 tiene retry; aquí el LLM puede simplemente llamar otra vez)
- Fetch de más de 3 páginas (para eso está `web_research`)
- Browser rendering / Puppeteer (sigue como MCP tool separado)

## Casos de Uso Críticos

### 1. Query con datos puntuales → extracción automática

**Antes:** "precio del dólar hoy" → snippet: "Cotización del Dólar - Ámbito Financiero" → LLM fabrica un precio.
**Después:** "precio del dólar hoy" con `depth="detailed"` → fetcha Ámbito, extrae "Dólar oficial: $1050, Blue: $1180" → LLM presenta datos reales.

### 2. Query con lista/tabla → datos estructurados

**Antes:** "fixture Rosario Central" → snippet sin fechas → "no encontré info".
**Después:** `depth="detailed"` → fetcha ESPN, LLM extrae "Fecha 8: 22/03 vs Racing 19hs, Fecha 9: 29/03 vs Boca 21hs" → datos reales.

### 3. Query simple → mantiene velocidad actual

"capital de Francia" → `depth="quick"` (default) → snippet "Paris" → respuesta inmediata, sin fetch overhead.

## Observabilidad (Langfuse)

Spans condicionales según `depth`:

```
tool:web_search (executor, automático — siempre)
│
├── [depth="quick"]  → web_search:quick (opcional, liviano)
│                      Solo query, results_count, latency_ms
│
└── [depth="detailed"] → web_search:detailed
                         ├── web_search:fetch     → status por URL (ok/error/timeout/empty)
                         └── llm:web_extract      → input pages preview, output extraction preview
```

**Métricas clave para evaluar en Langfuse dashboards**:
- `depth` distribution: qué % de llamadas usa quick vs detailed (¿el LLM elige bien?)
- `pages_successful` / `pages_attempted`: tasa de éxito de fetch
- `extraction_chars`: longitud del output del LLM extractor (si es consistentemente corto, el prompt necesita ajuste)
- `latency_total_ms`: desglosado en search + fetch + extract para detectar cuellos de botella
- Status por URL individual: detectar dominios que bloquean (403s recurrentes)
- Comparar calidad de respuesta final cuando se usa quick vs detailed (correlacionar con thumbs up/down)

## Restricciones Arquitectónicas

- **LLM call extra**: un call a qwen3.5:9b con `think=False`, input ~6000 chars (3 páginas × 2000), output ~500-1000 chars. Latencia estimada: ~5 segundos
- **Contenido de páginas**: truncar cada página a ~2000 chars post-extraction (trafilatura). Total input al LLM de extraction: ~6000-8000 chars — dentro de context window
- **No embeddings**: a diferencia de Plan 51, no usa `OllamaClient.embed()`. Solo `OllamaClient.chat()` con prompt de extraction
- **Backward compat**: `depth` default es `"quick"` → comportamiento idéntico al actual cuando no se especifica
- **`think=False` obligatorio**: el prompt de extraction es un prompt utilitario, no necesita chain-of-thought
- **Dependency**: reutiliza `trafilatura` de Plan 51 (o lo agrega si Plan 51 no está implementado aún)
