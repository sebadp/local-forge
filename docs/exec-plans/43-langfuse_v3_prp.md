# PRP: Langfuse v3 SDK Upgrade

> **PRD**: [`43-langfuse_v3_prd.md`](43-langfuse_v3_prd.md)
> **Branch**: `feat/langfuse-v3`
> **Estimado**: ~3-4h

---

## Fases

- [ ] **Fase 1**: Investigación y mapeo de API (30min)
- [ ] **Fase 2**: Bump de versión + migrar recorder.py (~2h)
- [ ] **Fase 3**: Tests + verificación end-to-end (~1h)
- [ ] **Fase 4**: Docs + merge (~30min)

---

## Fase 1: Investigación de API v3

### Verificar la low-level API de v3

Antes de codear, instalar `langfuse==3.14.5` en un venv limpio y verificar:

```python
from langfuse import Langfuse
lf = Langfuse(public_key="...", secret_key="...", host="...")

# ¿Existen estos métodos?
print(dir(lf))
# Esperamos: create_trace, create_span, create_generation, update_trace,
#             update_span, update_generation, score, create_dataset_item, flush
```

**Checkpoint**: documentar qué métodos existen realmente en v3.14.5 antes de continuar.

- [ ] Verificar `create_trace` con `id`, `name`, `user_id`, `session_id`, `input`, `metadata`
- [ ] Verificar `create_span` con `id`, `trace_id`, `parent_observation_id`, `name`
- [ ] Verificar `create_generation` con `id`, `trace_id`, `parent_observation_id`, `name`
- [ ] Verificar `update_span` con `id`, `output`, `input`, `level`, `metadata`
- [ ] Verificar `update_generation` con `id`, `output`, `input`, `level`, `metadata`, `model`, `usage`
- [ ] Verificar `score` — debe ser igual a v2
- [ ] Verificar `create_dataset_item` — debe ser igual a v2
- [ ] Verificar `flush()` y si existe `flush_async()`
- [ ] Verificar si `trace(id=..., tags=[...])` sigue funcionando para update (usada en `update_trace_tags`)

---

## Fase 2: Implementación

### 2.1 `requirements.txt`

```diff
-langfuse>=2.54.0,<3.0.0
+langfuse>=3.14.0,<4.0.0
```

- [ ] Actualizar `requirements.txt`
- [ ] Verificar que no hay dependencias transitivas que pineen v2

### 2.2 `app/tracing/recorder.py` — Migrar call sites

**Mapa de migración (7 call sites):**

#### `create()` classmethod — eliminar guard obsoleto

```python
# ANTES (v2)
if not hasattr(Langfuse, "trace"):
    logger.warning("Langfuse SDK is incompatible (v3+ detected)...")
else:
    langfuse = Langfuse(...)

# DESPUÉS (v3)
# El guard ya no aplica — simplemente inicializar
langfuse = Langfuse(
    public_key=settings.langfuse_public_key,
    secret_key=settings.langfuse_secret_key,
    host=settings.langfuse_host,
)
logger.info("Langfuse v3 tracing enabled")
```

- [ ] Eliminar el `if not hasattr(Langfuse, "trace"):` block
- [ ] Mantener el `try/except` externo (best-effort init)

#### `start_trace()` — `trace()` → `create_trace()`

```python
# ANTES
self.langfuse.trace(
    id=trace_id,
    name="interaction",
    user_id=phone_number,
    session_id=phone_number,
    input=input_text,
    metadata={"message_type": message_type, "platform": platform},
)

# DESPUÉS
self.langfuse.create_trace(
    id=trace_id,
    name="interaction",
    user_id=phone_number,
    session_id=phone_number,
    input=input_text,
    metadata={"message_type": message_type, "platform": platform},
)
```

- [ ] Migrar `start_trace`

#### `finish_trace()` — `trace()` → `update_trace()`

```python
# ANTES
self.langfuse.trace(id=trace_id, output=output_text, tags=[status])
self.langfuse.flush()

# DESPUÉS
self.langfuse.update_trace(id=trace_id, output=output_text, tags=[status])
# flush_async si disponible, sino flush() sync
if hasattr(self.langfuse, "flush_async"):
    await self.langfuse.flush_async()
else:
    self.langfuse.flush()
```

- [ ] Migrar `finish_trace`
- [ ] Usar `flush_async()` si disponible (evita bloquear event loop)

#### `start_span()` — `span()` / `generation()` → `create_span()` / `create_generation()`

```python
# ANTES
if kind == "generation":
    self.langfuse.generation(
        id=span_id, trace_id=trace_id, parent_observation_id=parent_id, name=name,
    )
else:
    self.langfuse.span(
        id=span_id, trace_id=trace_id, parent_observation_id=parent_id, name=name,
    )

# DESPUÉS
if kind == "generation":
    self.langfuse.create_generation(
        id=span_id, trace_id=trace_id, parent_observation_id=parent_id, name=name,
    )
else:
    self.langfuse.create_span(
        id=span_id, trace_id=trace_id, parent_observation_id=parent_id, name=name,
    )
```

- [ ] Migrar `start_span`

#### `finish_span()` — `span()` / `generation()` → `update_span()` / `update_generation()`

```python
# ANTES
if usage or model:
    self.langfuse.generation(id=span_id, output=..., input=..., level=..., metadata=..., model=..., usage=...)
else:
    self.langfuse.span(id=span_id, output=..., input=..., level=..., metadata=...)

# DESPUÉS
if usage or model:
    self.langfuse.update_generation(id=span_id, output=..., input=..., level=..., metadata=..., model=..., usage=...)
else:
    self.langfuse.update_span(id=span_id, output=..., input=..., level=..., metadata=...)
```

- [ ] Migrar `finish_span`

#### `add_score()` — sin cambios (API idéntica en v3)

```python
# Sin cambios — v3 mantiene la misma firma
self.langfuse.score(
    trace_id=trace_id,
    observation_id=span_id,
    name=name,
    value=value,
    comment=comment,
)
```

- [ ] Confirmar que `score()` funciona igual en v3

#### `update_trace_tags()` — verificar si `trace(id=..., tags=...)` sigue siendo update

```python
# Si en v3 trace() es create, usar update_trace() en su lugar:
self.langfuse.update_trace(id=trace_id, tags=tags)
```

- [ ] Verificar y migrar `update_trace_tags`

#### `sync_dataset_to_langfuse()` — sin cambios si `create_dataset_item` es idéntico

- [ ] Confirmar y actualizar si necesario

### 2.3 Manejo de `usage` en v3

En v2, `usage={"input": N, "output": M}` era el formato. En v3 puede ser:
```python
usage=ModelUsage(input=N, output=M)
# o simplemente dict — verificar en Fase 1
```

- [ ] Verificar formato de `usage` en `update_generation()`
- [ ] Ajustar si cambió el schema

---

## Fase 3: Tests y Verificación

### 3.1 Tests unitarios

Los tests actuales de tracing mockean `langfuse.trace()`, `langfuse.span()`, etc.
Actualizar los mocks para reflejar los nuevos métodos v3:

```bash
grep -r "langfuse\." tests/ --include="*.py" -l
```

- [ ] Identificar todos los archivos de test que mockean Langfuse
- [ ] Actualizar los mocks: `mock_langfuse.trace` → `mock_langfuse.create_trace`, etc.
- [ ] Correr `make test` — verde

### 3.2 Smoke test end-to-end

Con Langfuse self-hosted (o cloud) levantado:

1. Configurar `.env` con `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`
2. Arrancar la app: `docker compose up -d`
3. Enviar un mensaje de WhatsApp/Telegram
4. Verificar en Langfuse UI:
   - Trace visible con input/output
   - Spans jerárquicos correctos (phase_ab, classify_intent, execute_tool_loop, etc.)
   - Scores visibles (language_match, tool_coherence, etc.)
   - Session grouping por phone_number

- [ ] Smoke test con Langfuse cloud
- [ ] Verificar traces → OK
- [ ] Verificar spans → OK
- [ ] Verificar scores → OK
- [ ] Verificar dataset items → OK (si `eval_auto_curate=True`)

### 3.3 Graceful degradation

- [ ] Probar sin Langfuse configurado (sin `LANGFUSE_PUBLIC_KEY`) → SQLite sigue funcionando
- [ ] Probar con Langfuse down → best-effort, warnings en log, app sigue funcionando

---

## Fase 4: Docs y Merge

### 4.1 Actualizar `CLAUDE.md`

Modificar la línea de tracing que menciona el pin de versión:

```diff
-**Langfuse versión**: pinear `langfuse>=2.54.0,<3.0.0`
+**Langfuse versión**: `langfuse>=3.14.0,<4.0.0` (v3 low-level API)
```

Y actualizar el patrón de `TraceRecorder`:
- `create_trace` / `create_span` / `create_generation` en lugar de `trace()` / `span()` / `generation()`
- `update_trace` / `update_span` / `update_generation` para updates
- `flush_async()` en shutdown si disponible

- [ ] Actualizar nota de versión en CLAUDE.md memory + tracing patterns

### 4.2 Merge

- [ ] PR a `main` con título: `feat: migrate Langfuse SDK to v3`
- [ ] Descripción del PR: qué cambió, qué permanece igual, cómo testear

---

## Archivos a Modificar

| Archivo | Cambio |
|---|---|
| `requirements.txt` | Bump `langfuse>=3.14.0,<4.0.0` |
| `app/tracing/recorder.py` | Migrar 7 call sites (create/update en lugar de trace/span/generation) |
| `tests/test_tracing_*.py` | Actualizar mocks de Langfuse |
| `CLAUDE.md` | Actualizar nota de versión |
| `docs/exec-plans/43-langfuse_v3_prp.md` | Marcar checkboxes a medida que avanza |

---

## Riesgos

| Riesgo | Mitigación |
|---|---|
| `create_trace` no existe en v3 (se llama diferente) | Verificar en Fase 1 antes de codear |
| `usage` dict format cambió en v3 | Verificar y adaptar en Fase 2.3 |
| Self-hosted Langfuse en versión < 3.125 | Documentar requisito de versión de platform |
| flush_async() no existe en todas las builds de v3 | Guard con `hasattr` + fallback a `flush()` |

---

## Notas de Implementación

### ¿Por qué no migrar al flujo OTEL context-manager?

El flujo v3 "moderno" usa context managers:
```python
with langfuse.start_as_current_span("my-span") as span:
    span.update(input=..., output=...)
```

Esto requeriría reescribir `TraceContext` en `app/tracing/context.py` — un refactor mayor que rompe la encapsulación actual. La low-level API ID-first que usamos **sigue soportada en v3** y es el camino correcto para nuestro patrón (donde el span ID se crea antes de la ejecución y se actualiza al terminar).

### Detección de versión compatible

Si queremos soportar tanto v2 como v3 (por si acaso):
```python
_LF_V3 = not hasattr(Langfuse, "trace")  # En v2 existe, en v3 no existe como standalone
# Pero mejor no — solo soportar v3 y fallar explícitamente si alguien instala v2
```
