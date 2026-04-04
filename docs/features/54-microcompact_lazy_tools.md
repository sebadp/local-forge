# Feature: MicroCompact & Lazy Tool Loading

> **Version**: v1.0
> **Fecha de implementacion**: 2026-04-02
> **Fase**: Fase 6
> **Estado**: ✅ Implementada

---

## Que hace?

Optimiza el uso del contexto del LLM de dos formas: (A) limpia automaticamente resultados de tools viejos y verbosos para liberar espacio, y (B) permite al LLM descubrir tools disponibles via busqueda por keyword cuando las tools iniciales no son suficientes.

---

## Arquitectura

```
Feature A: MicroCompact
========================
  Tool Loop iteration N
        │
        ▼
  microcompact_messages()
        │
        ├── Escanea rounds anteriores (assistant+tool_calls → tool results)
        ├── Si round es >= max_age_rounds viejo:
        │     ├── Tool en COMPACTABLE_TOOLS?
        │     └── Resultado > 200 chars?
        │           └── Reemplaza con stub: "[Tool result cleared — N chars]"
        └── Retorna nueva lista (no muta original)

Feature B: discover_tools
=========================
  LLM necesita tool que no tiene
        │
        ▼
  discover_tools(query="weather")
        │
        ├── registry.search_tools(query) — fuzzy match en nombre + descripcion
        └── Retorna lista formateada de tools + categorias
              │
              ▼
        LLM llama request_more_tools(categories=["weather"])
```

---

## Archivos clave

| Archivo | Rol |
|---|---|
| `app/formatting/microcompact.py` | MicroCompact: clearing selectivo de tool results |
| `app/skills/tools/meta_tools.py` | `discover_tools` handler |
| `app/skills/registry.py` | `search_tools()` — keyword search sobre tools registrados |
| `app/skills/executor.py` | Integracion de microcompact en el tool loop |
| `app/skills/tools/__init__.py` | Registro de `discover_tools` al startup |
| `tests/test_microcompact.py` | 9 tests |
| `tests/test_meta_tools.py` | 8 tests |

---

## Walkthrough tecnico

### MicroCompact

1. En cada iteracion del tool loop, `microcompact_messages()` se llama antes del LLM call → `executor.py:623`
2. Identifica "rounds" escaneando assistant messages con `tool_calls` → `microcompact.py:68`
3. Para rounds >=2 iteraciones viejos, reemplaza tool results de `COMPACTABLE_TOOLS` con stubs → `microcompact.py:96`
4. Deterministic: sin LLM calls, solo string replacement

### discover_tools

1. Registrado como tool builtin al startup → `__init__.py:97`
2. `search_tools(query)` tokeniza query y matchea contra nombre+descripcion de todos los tools → `registry.py:139`
3. Retorna top matches con nombre, descripcion y categoria
4. El LLM puede entonces llamar `request_more_tools` con la categoria correcta

---

## Decisiones de diseno

| Decision | Alternativa descartada | Motivo |
|---|---|---|
| Deterministic replacement (no LLM) | LLM-based summarization | Costo cero, predecible, sin latencia |
| Solo compactar tools verbosos | Compactar todos los tool results | Tools cortos (calculate, get_weather) no justifican el clearing |
| Keyword search simple | Embedding-based search | Los nombres de tools son descriptivos; embeddings seria overengineering |
| `discover_tools` retorna descriptions, no schemas | Retornar schemas completos | El LLM debe usar `request_more_tools` para cargar schemas — separacion de concerns |

---

## Variables de configuracion relevantes

| Variable | Default | Efecto |
|---|---|---|
| `COMPACTABLE_TOOLS` (constante) | 16 tools | Set de tools cuyos resultados se compactan |
| `max_age_rounds` (param) | `2` | Rounds de antiguedad antes de compactar |
| `_MIN_CONTENT_LEN` (constante) | `200` | Resultados mas cortos no se compactan |
