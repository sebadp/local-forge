# Plan de Acción: Mejoras Arquitectónicas (Palantir AIP Gap Analysis)

> Resultado del análisis comparativo con [Palantir AIP Architecture](https://www.palantir.com/docs/foundry/architecture-center/aip-architecture).
> Este documento es el **índice maestro** — cada gap mayor tiene su propio exec plan.

## Origen

Se compararon las 12 capacidades del AIP Architecture Center contra WasAP. Las áreas fuertes (observability, context engineering, security, agent orchestration, eval) ya están al nivel. Este plan cubre las **mejoras inmediatas** y los **gaps estratégicos** pendientes.

---

## Parte 1: Quick Wins (branch actual o próximo sprint)

### 1.1 Langfuse v3 Upgrade

**Estado actual**: Pinned a `langfuse>=2.54.0,<3.0.0`. El SDK v3 (actual: 3.14.5) reescribió la API.

**Cambios principales en v3:**
- `langfuse.trace()` / `langfuse.span()` / `langfuse.generation()` → **ELIMINADOS**
- Nuevo API: `langfuse.start_as_current_observation(as_type="span"|"generation")`
- Context manager pattern (propagación automática via OpenTelemetry)
- `Langfuse()` constructor sigue funcionando, pero hay `get_client()` singleton
- Requiere Python ≥ 3.10 (nosotros usamos 3.11 ✅)
- Requiere Langfuse platform ≥ 3.125.0 (verificar versión self-hosted si aplica)

**Archivos a modificar:**
- `requirements.txt` / `pyproject.toml`: `langfuse>=3.14.0,<4.0.0`
- `app/tracing/recorder.py`: Migrar todos los métodos — ~7 call sites
  - `self.langfuse.trace(id=..., ...)` → `self.langfuse.start_as_current_observation(...)` o API equivalente
  - `self.langfuse.span(...)` → `start_as_current_observation(as_type="span", ...)`
  - `self.langfuse.generation(...)` → `start_as_current_observation(as_type="generation", ...)`
  - `self.langfuse.score(...)` → verificar API de scoring v3
  - `self.langfuse.create_dataset_item(...)` → verificar compatibilidad
  - `self.langfuse.flush()` → sigue existiendo ✅
- `app/tracing/recorder.py:40`: Eliminar el check `hasattr(Langfuse, "trace")` que bloqueaba v3
- Tests: actualizar mocks si hay

**Riesgo:** Medio. El recorder es best-effort, así que si algo falla la app sigue funcionando. Pero hay que verificar que el API de scoring y datasets no cambió.

**Acción:** Crear branch `feat/langfuse-v3`, migrar, testear contra Langfuse cloud/self-hosted.

---

### 1.2 Planner con `think=True`

**Estado actual**: `planner.py` usa `think=False` en las 3 funciones (`create_plan`, `replan`, `synthesize`). Esto es contradictorio — la planificación es exactamente donde el chain-of-thought más valor agrega.

**Dónde activar `think=True`:**

| Función | `think` actual | Cambio | Razón |
|---|---|---|---|
| `create_plan()` | `False` | → `True` | Descomposición de tareas requiere razonamiento profundo |
| `replan()` | `False` | → `True` | Evaluar progreso y decidir replanning necesita reflexión |
| `synthesize()` | `False` | → `True` | Síntesis de resultados se beneficia de CoT |
| `_score_goal_completion()` | `False` | mantener `False` | Clasificación binaria (yes/no) — no necesita CoT |
| Guardrails LLM checks | `False` | mantener `False` | Clasificación binaria — latencia importa |
| `/dev-review` | usa planner | → `True` (hereda) | Debugging requiere razonamiento complejo |
| Workers (`execute_worker`) | hereda default | evaluar | Workers ejecutan, no razonan — puede mantenerse sin think |

**Archivos a modificar:**
- `app/agent/planner.py`: Cambiar `think=False` → `think=True` en `create_plan()`, `replan()`, `synthesize()`
- **Nota**: qwen3 con `think=True` genera `<think>...</think>` tags que hay que parsear/strip del output JSON. Verificar que `_parse_plan_json()` los maneje.

**Consideración de latencia:** `think=True` en qwen3:8b puede duplicar los tokens de output. En contexto agéntico esto es aceptable (el usuario ya espera latencia). En flujo normal (chat) NO activar think para el planner — solo aplica al agent loop.

**Acción:** Modificar las 3 funciones del planner. Verificar que el parser JSON tolere `<think>` prefix en la respuesta.

---

### 1.3 Downgrade Warning + Retry antes de Fallback Reactivo

**Estado actual**: Si `_run_planner_session()` falla (exception) → fallback silencioso a `_run_reactive_session()`. El usuario no sabe que su sesión bajó de calidad.

**Mejora en 2 partes:**

**a) Retry con prompt de corrección** (antes de abandonar):
```python
# En _run_agent_body(), antes del fallback:
try:
    reply = await _run_planner_session(...)
except Exception as plan_err:
    logger.warning("Planner failed (%s), retrying with correction prompt", plan_err)
    try:
        # Retry con prompt explícito de "output ONLY valid JSON"
        reply = await _run_planner_session(...)  # segundo intento
    except Exception:
        # Ahora sí, fallback
        ...
```

**b) Notificación al usuario** (cuando hay downgrade):
```python
await wa_client.send_message(
    session.phone_number,
    "⚠️ No pude crear un plan estructurado. Continuando en modo reactivo "
    "(menos eficiente pero funcional)."
)
```

**c) Score de tracing** para trackear la frecuencia:
```python
trace = get_current_trace()
if trace:
    await trace.add_score(name="planner_downgrade", value=1.0, source="system",
                          comment="Fallback to reactive after planner failure")
```

**Archivos a modificar:**
- `app/agent/loop.py`: `_run_agent_body()` — agregar retry + notificación + score

---

### 1.4 Percentiles: Env Var para Desactivar

**Estado actual**: Percentiles (p50/p95/p99) se calculan en Python cargando todos los valores a memoria. Funciona bien con pocas trazas pero no escala.

**Decisión**: Mantener por ahora (preferimos tener las métricas). Agregar:

1. **Setting**: `metrics_percentiles_enabled: bool = True` en `app/config.py`
2. **Guard** en repository methods: si disabled, retornar `{}` sin hacer la query
3. **Nota en código**:
   ```python
   # NOTE: Percentile calculation loads all values into Python memory.
   # This does not scale beyond ~100K spans. When we hit that scale,
   # migrate to pre-aggregated materialized views or approximate percentiles.
   # Disable via METRICS_PERCENTILES_ENABLED=false if memory becomes an issue.
   ```

**Archivos a modificar:**
- `app/config.py`: Nueva setting
- `app/database/repository.py`: Guard en `get_latency_percentiles()` y `_compute_percentiles()`
- `app/skills/tools/eval_tools.py`: Respetar la setting en `get_latency_stats`

---

## Parte 2: Gaps Estratégicos (Exec Plans separados)

Cada gap tiene su propio PRD/PRP siguiendo las convenciones del proyecto.

### 2.1 ✅ Ontology Data Model — Plan 42

**Exec Plan:** [`42-ontology_data_model_prd.md`](42-ontology_data_model_prd.md)

**Estado:** Completado. Entity registry con SQLite, BFS traversal, context enrichment en Phase B, tool `search_knowledge_graph`.

---

### 2.2 🔴 Data Provenance & Lineage — Plan 44

**Problema**: No sabemos de dónde vino cada dato. ¿Esta memoria fue extraída de qué conversación? ¿Quién la modificó — el usuario, el consolidator, el LLM? ¿Esta nota fue creada manualmente o por un tool call?

**Depende de:** Plan 42 (Ontology) ✅ — el lineage se modela como relaciones en el entity graph.

**PRD:** [`44-data_provenance_prd.md`](44-data_provenance_prd.md)

---

### 2.3 🟡 Token Accuracy — Plan 45

**Problema**: `chars/4` tiene ±20% de margen. Para qwen3 con su tokenizer BPE específico esto puede sobre/sub-estimar significativamente.

**PRD:** [`45-token_accuracy_prd.md`](45-token_accuracy_prd.md)

---

### 2.4 🟡 Deployment Maturity — Plan 46

**Problema**: Docker + docker-compose sin health checks, secrets management, ni release channels.

**PRD:** [`46-deployment_maturity_prd.md`](46-deployment_maturity_prd.md)

---

### 2.5 🟡 Operational Automation — Plan 47

**Problema**: Solo tenemos cron jobs + webhooks. Palantir tiene automaciones event-driven basadas en datos.

**PRD:** [`47-operational_automation_prd.md`](47-operational_automation_prd.md)

---

## Orden de Ejecución Recomendado

```
Sprint actual (Quick Wins):                        ✅ Completados
  1.2 Planner think=True          ← impacto en calidad agéntica
  1.3 Downgrade warning + retry   ← resiliencia del planner
  1.4 Percentiles env var         ← housekeeping

Próximo sprint:                                     ✅ Completado
  1.1 Langfuse v3 upgrade (Plan 43)

Backlog estratégico:
  Plan 42: Ontology Data Model    ✅ Completado
  Plan 44: Data Provenance        ← Depende de Plan 42 (Ontology) ✅
  Plan 45: Token Accuracy         ← Independiente, baja urgencia
  Plan 46: Deployment Maturity    ← Independiente, media urgencia
  Plan 47: Operational Automation ← Independiente, baja urgencia
```

---

## Métricas de Éxito

| Mejora | Métrica | Target |
|---|---|---|
| Planner think=True | `goal_completion` score promedio | +10% vs baseline |
| Downgrade warning | `planner_downgrade` score frequency | <5% de sesiones agénticas |
| Langfuse v3 | Traces visibles en Langfuse dashboard | 100% (sin pérdida vs v2) |
| Ontology | Búsqueda cross-entity exitosa | ≥80% de queries multi-entidad resueltos |