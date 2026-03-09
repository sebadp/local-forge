# Guía: Capturar un Baseline de Performance

> **¿Para qué sirve esto?**
> Antes de aplicar cualquier optimización (Plan 36 o futuros cambios) conviene tener un
> snapshot de cómo está el sistema *ahora*. Sin ese baseline es imposible saber si los cambios
> mejoraron algo o empeoraron algo que antes funcionaba bien.

---

## Conceptos clave

| Término | Qué mide |
|---------|----------|
| **Traza** | Una interacción completa: desde que llega el mensaje hasta que el usuario recibe la respuesta |
| **Span** | Una sub-operación dentro de la traza: `phase_ab`, `classify_intent`, `tool_loop`, etc. |
| **Score** | Señal numérica (0.0–1.0) adjunta a una traza: guardrail, feedback del usuario, goal completion |
| **p50 / p95 / p99** | Percentiles de latencia. p50 = tiempo mediano; p95 = el 95% de las trazas es más rápido que este valor |
| **Context fill rate** | Qué porcentaje del límite de 32K tokens se usó por mensaje |
| **Goal completion** | Score LLM-as-judge que indica si el agente completó el objetivo (solo sesiones agénticas) |

---

## Cuántos mensajes necesitás mínimo

Los distintos tipos de métricas requieren volúmenes diferentes para ser estadísticamente útiles:

| Métrica | Mínimo recomendado | Por qué |
|---------|--------------------|---------|
| **Latencias p50** (e2e, phase_ab, classify) | 30 trazas | Con menos de 30, el p50 fluctúa demasiado entre ejecuciones |
| **Latencias p95 / p99** | 100 trazas | Necesitás ver los outliers reales, no ruido estadístico |
| **Guardrail pass rate** (por check) | 50 trazas | Cada check puede tener señal diferente; mezclar tipos de mensaje ayuda |
| **Search mode distribution** (semantic/fallback) | 50 trazas | La distribución se estabiliza después de los primeros ~20 mensajes |
| **Tool efficiency** (avg calls, error rates) | 20 trazas con tools | Solo trazas con tool calling cuentan; mensajes de chat puro no aportan |
| **Token consumption** (avg input/output) | 20 generation spans | Varía mucho por tipo de mensaje; necesitás mezcla de simple+tools |
| **Context quality** (fill rate, classify_upgrade) | 30 trazas | Un solo usuario puede no activar classify_upgrade; diversificá |
| **Goal completion** (LLM-as-judge) | 10 sesiones agénticas | Costoso de generar; usa `/agent <objetivo>` para crear sesiones de prueba |

**Resumen práctico:** para un baseline completo y confiable, apuntá a **~100 mensajes reales variados** (texto, audio, preguntas con tools, preguntas simples). En producción, 3-5 días de uso normal suelen ser suficientes.

---

## Paso a paso

### 1. Verificar que el tracing esté activo

En `.env`, asegurate de tener:

```env
TRACING_ENABLED=true
TRACING_SAMPLE_RATE=1.0
GUARDRAILS_ENABLED=true
EVAL_AUTO_CURATE=true
```

Con `TRACING_SAMPLE_RATE=1.0` se trazan **todas** las interacciones. En producción con mucho volumen podés bajarlo a 0.5, pero para un baseline querés el 100%.

### 2. Enviar mensajes variados

Para que el baseline cubra todos los tipos de operaciones, envía al menos:

```
- 20 mensajes de texto simples (preguntas, conversación)
- 10 mensajes que activen tools (clima, calculadora, notas, proyectos)
- 5 mensajes de audio (para medir latencia de Whisper)
- 5 mensajes largos que requieran varias iteraciones del tool loop
- 3-5 sesiones agénticas con /agent para capturar goal_completion
```

No es necesario hacerlo en una sola sesión. El script usa una ventana de tiempo (`--days N`).

### 3. Capturar el snapshot

```bash
# Baseline de los últimos 7 días (recomendado para producción)
python scripts/baseline.py --db data/localforge.db --days 7

# Ventana más amplia si tenés poco volumen
python scripts/baseline.py --db data/localforge.db --days 30

# Guardar en un path específico (para nombrar claramente el snapshot)
python scripts/baseline.py --db data/localforge.db --days 7 \
    --output reports/baseline_antes_plan36.json
```

El script imprime el reporte en consola **y** guarda un JSON en `reports/`. El JSON es lo que usarás para comparar después.

### 4. Interpretar el reporte

```
TRACE VOLUME
─────────────────────────────────────────────────────
  Total messages      : 143
    text              : 98
    audio             : 28
    image             : 17
  Completed           : 139
  Failed              : 4
  With tool calls     : 61
```

- `Failed` > 5%: revisar logs. Puede ser un error de configuración o timeout de Ollama.
- `With tool calls` muy bajo (< 20%): el router de intención no está activando tools. Revisá `classify_intent`.

```
END-TO-END LATENCY
─────────────────────────────────────────────────────
  end_to_end: p50=2100ms  p95=5800ms  p99=9200ms  max=14000ms  (n=139)
```

- **p50 < 2000ms**: excelente para mensajes simples con qwen3:8b en local.
- **p50 2000–4000ms**: normal con tools activos.
- **p95 > 8000ms**: revisar si hay outliers en `tool_loop` o `phase_ab`.
- **p99 > 15000ms**: posible timeout o bloqueo en una operación async.

```
PHASE BREAKDOWN
─────────────────────────────────────────────────────
  phase_ab   (total A+B): p50=380ms   p95=920ms
    phase_a  (embed query): p50=85ms  p95=210ms
    phase_b  (DB searches): p50=290ms p95=700ms
  classify_intent        : p50=210ms  p95=480ms
  tool_loop              : p50=1800ms p95=4100ms
```

- `phase_a` (embed) > 200ms p50: el modelo de embeddings está lento. Revisá `nomic-embed-text`.
- `phase_b` (DB) > 500ms p50: revisar índices SQLite o número de memorias almacenadas.
- `classify_intent` > 400ms p50: qwen3 tarda en clasificar. Normal sin GPU; con GPU debería ser < 100ms.

```
TOOL EFFICIENCY
─────────────────────────────────────────────────────
  Avg tool calls/trace  : 2.31
  Max tool calls/trace  : 9
  Avg iterations/trace  : 1.84
  Error rates by tool:
    web_search                  : 8.1%
    calculate                   : 0.6%
```

- Avg > 4 calls/trace: el LLM puede estar loopeando innecesariamente. Revisá el system prompt.
- Error rate por tool > 10%: ese tool tiene un problema sistemático. Revisá su handler.

```
AGENT EFFICACY
─────────────────────────────────────────────────────
  Planner sessions        : 12
  Replanning rate         : 25.0%
  Goal completion (LLM)   : 83.3%  (n=12)
```

- Replanning rate > 40%: el planner genera planes que se invalidan frecuentemente. Revisá el prompt del planner.
- Goal completion < 70%: el agente no completa los objetivos con consistencia.
  ⚠️ Este score es auto-juicio (qwen3 evaluándose a sí mismo); tiende a estar inflado ~10-15%.

### 5. Dashboard visual (opcional)

```bash
python scripts/dashboard.py --db data/localforge.db --days 7 \
    --output reports/dashboard_baseline.html
```

Abre el HTML en cualquier browser. Incluye gráficos Chart.js de failure trend, tablas de latencia,
guardrail pass rates, y las nuevas secciones de tool efficiency y context quality.

Si tenés Langfuse corriendo:

```bash
LANGFUSE_HOST=http://localhost:3000 python scripts/dashboard.py --db data/localforge.db
```

Los trace IDs en la tabla de "Recent Failures" se convierten en links clickeables directamente a Langfuse.

---

## Comparar baseline vs. post-optimización

El snapshot JSON tiene esta estructura:

```json
{
  "captured_at": "2026-03-09T12:00:00+00:00",
  "days": 7,
  "latency": {
    "end_to_end": { "p50": 2100, "p95": 5800, "p99": 9200, "n": 139 },
    "phase_ab":   { "p50": 380,  "p95": 920,  ... },
    ...
  },
  "tool_efficiency": { "avg_tool_calls": 2.31, ... },
  "context_quality": { "avg_fill_rate": 34.2, ... },
  "goal_completion": { "goal_completion_rate_pct": 83.3, "n": 12 }
}
```

Para comparar dos snapshots rápido:

```bash
# Comparación manual con jq
jq '.latency.end_to_end' reports/baseline_antes_plan36.json
jq '.latency.end_to_end' reports/baseline_despues_plan36.json

# Ver diferencia de p50
python3 -c "
import json
a = json.load(open('reports/baseline_antes_plan36.json'))
b = json.load(open('reports/baseline_despues_plan36.json'))
p50_a = a['latency']['end_to_end']['p50']
p50_b = b['latency']['end_to_end']['p50']
print(f'p50: {p50_a:.0f}ms → {p50_b:.0f}ms  ({(p50_b-p50_a)/p50_a*100:+.1f}%)')
"
```

---

## Targets de referencia (qwen3:8b sin GPU)

Estos son valores orientativos para un setup local típico (Apple Silicon M1/M2 o equivalente):

| Métrica | Aceptable | Bueno | Excelente |
|---------|-----------|-------|-----------|
| e2e p50 (mensajes simples) | < 4000ms | < 2000ms | < 1200ms |
| e2e p50 (con tools) | < 7000ms | < 4000ms | < 2500ms |
| phase_a embed p50 | < 300ms | < 150ms | < 80ms |
| phase_b DB p50 | < 600ms | < 300ms | < 100ms |
| classify_intent p50 | < 600ms | < 300ms | < 150ms |
| Context fill rate avg | < 60% | < 40% | < 25% |
| Tool error rate (por tool) | < 15% | < 5% | < 1% |
| Goal completion rate | > 60% | > 75% | > 85% |

> Con GPU (CUDA/Metal) los tiempos de LLM se reducen 3–5x. Los tiempos de DB y embed son iguales.

---

## Qué hacer si no hay datos suficientes

Si el script reporta "No data" en varias secciones:

1. **Verificar que `TRACING_ENABLED=true`** en `.env` y que la app fue reiniciada después del cambio.

2. **Verificar que la app procesa mensajes** (ver logs):
   ```bash
   docker compose logs -f localforge | grep "trace_id"
   ```

3. **Generar trazas de prueba** sin usuario real:
   ```bash
   # Enviar un mensaje de prueba directo al endpoint
   curl -X POST http://localhost:8000/webhook \
     -H "Content-Type: application/json" \
     -d '...'  # usar el formato del parser de la plataforma
   ```

4. **Ampliar la ventana** de días hasta encontrar datos:
   ```bash
   python scripts/baseline.py --days 90
   ```

5. **Consultar la DB directamente** para ver qué hay:
   ```sql
   SELECT COUNT(*), MIN(started_at), MAX(started_at) FROM traces;
   SELECT COUNT(*), name FROM trace_spans GROUP BY name ORDER BY 1 DESC LIMIT 10;
   ```

---

## Archivos generados

| Archivo | Contenido |
|---------|-----------|
| `reports/baseline_plan36_<timestamp>.json` | Snapshot completo en JSON (para comparar) |
| `reports/dashboard.html` | Dashboard visual HTML con Chart.js |

Ambos están en `.gitignore` por defecto. Si querés versionar el baseline para compararlo en CI, podés comitear el JSON manualmente:

```bash
git add reports/baseline_antes_plan36.json
git commit -m "chore: add performance baseline before Plan 36"
```
