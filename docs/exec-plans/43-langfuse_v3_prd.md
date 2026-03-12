# PRD: Langfuse v3 SDK Upgrade

> **Plan**: 43
> **Tipo**: Migration
> **Prioridad**: Alta — desbloquea sessions, OTEL nativo, datasets API actualizada
> **Estado**: 📋 Planificado

---

## El Problema

`recorder.py` está pinned a `langfuse>=2.54.0,<3.0.0`. El SDK actual en PyPI es **v3.14.5** (lanzado hace meses). La v3 es una reescritura completa que migró a OpenTelemetry como transporte nativo.

**Síntomas del statu quo:**
- Usamos un SDK desactualizado con posibles bugs de seguridad
- No podemos usar features v3: OTEL-native, `get_client()` singleton, `flush_async()`
- El guard `hasattr(Langfuse, "trace")` en `recorder.py:40` bloquea activamente v3
- Nuevas features de Langfuse platform (v3.125+) no son compatibles con el SDK v2

---

## Cambios Principales entre v2 y v3

### API eliminada en v3

| v2 (actual) | v3 (nueva) | Notas |
|---|---|---|
| `langfuse.trace(id=..., ...)` | `langfuse.trace(id=..., ...)` | **Sigue existiendo** pero con nueva semántica |
| `langfuse.span(id=..., trace_id=..., ...)` | Context manager `start_as_current_span(...)` | Flujo OTEL |
| `langfuse.generation(id=..., trace_id=..., ...)` | Context manager `start_as_current_generation(...)` | Flujo OTEL |
| `langfuse.score(trace_id=..., ...)` | `langfuse.score(trace_id=..., ...)` | **Sigue igual** |
| `langfuse.create_dataset_item(...)` | `langfuse.create_dataset_item(...)` | **Sigue igual** |
| `langfuse.flush()` | `langfuse.flush()` o `await langfuse.flush_async()` | Hay versión async |
| `hasattr(Langfuse, "trace")` → True | `hasattr(Langfuse, "trace")` → True | El método existe, es la API lo que cambió |

### Descubrimiento clave

Tras revisar la [documentación v3](https://langfuse.com/docs/sdk/python/sdk-v3), el método `langfuse.trace()` **sí existe en v3**, pero ahora actúa como un **context manager** en lugar de retornar un objeto mutable. El flujo v2 de llamar métodos sobre el objeto retornado por `trace()` ya no funciona igual.

Sin embargo, **para nuestro patrón de uso específico** (llamadas independientes con IDs explícitos para actualizar traces/spans), v3 expone un patrón de "low-level API" que sí soporta IDs explícitos:

```python
# v3 low-level API (compatible con nuestro patrón ID-first)
langfuse.create_trace(id=trace_id, name="interaction", ...)
langfuse.create_span(id=span_id, trace_id=trace_id, ...)
langfuse.create_generation(id=span_id, trace_id=trace_id, ...)
langfuse.update_trace(id=trace_id, output=...)
langfuse.update_span(id=span_id, ...)
langfuse.update_generation(id=span_id, ...)
```

Esto significa que la migración es un **rename/refactor** en `recorder.py`, no una reescritura arquitectónica.

---

## Alcance

### In Scope

- Bump `langfuse>=3.14.0,<4.0.0` en `requirements.txt`
- Migrar los ~7 call sites en `app/tracing/recorder.py`
- Eliminar el guard `hasattr(Langfuse, "trace")` y reemplazarlo con detección de v3
- Verificar que SQLite tracing sigue funcionando (independiente de Langfuse)
- Testear contra Langfuse cloud o self-hosted v3.125+

### Out of Scope

- Migrar al flujo OTEL context-manager (requeriría refactor de TraceContext — v2 siguiente)
- Modificar `app/tracing/context.py` (sigue siendo compatible)
- Cambiar cómo SQLite almacena spans/traces (schema no cambia)

---

## Reglas y Excepciones

1. **Best-effort siempre**: si la llamada v3 falla, capturar y loggear — nunca propagar
2. **SQLite es el source of truth**: Langfuse es best-effort. Si migración rompe algo, SQLite sigue
3. **Backward compatible con self-hosted**: verificar que `langfuse_host` sigue funcionando con instancias self-hosted en v3.125+
4. **`flush_async()`**: si v3 lo tiene, usarlo en `finish_trace` para evitar bloquear el event loop con `flush()` sync

---

## Métricas de Éxito

| Métrica | Target |
|---|---|
| Traces visibles en Langfuse | 100% (sin pérdida vs v2) |
| Scores visibles | 100% |
| Spans jerárquicos | Correctos (parent_id preservado) |
| Latencia de finish_trace | Sin degradación (flush_async si disponible) |
| Tests unitarios | Verde |
