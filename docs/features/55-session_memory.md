# Feature: Session Memory — LLM-Powered Fact Extraction

> **Version**: v1.0
> **Fecha de implementacion**: 2026-04-02
> **Fase**: Fase 6
> **Estado**: ✅ Implementada

---

## Que hace?

Extrae automaticamente datos del usuario (preferencias, contexto tecnico, eventos temporales) de la conversacion cada N mensajes, usando una llamada LLM en background. Complementa la extraccion regex de `fact_extractor.py` con comprension semantica.

---

## Arquitectura

```
Cada N mensajes del usuario (default 10)
        │
        ▼
  should_extract(phone)  ── counter in-memory por telefono
        │ (si toca)
        ▼
  _run_session_extraction()  ← background task (no bloquea respuesta)
        │
        ├── Cargar ultimos 20 mensajes del usuario
        ├── Cargar memorias existentes como "known facts"
        ├── Single LLM call con prompt de extraccion (think=False)
        ├── Parsear JSON response → lista de facts
        ├── Persistir como memorias en DB
        ├── Append a daily log
        └── Sync MEMORY.md
```

---

## Archivos clave

| Archivo | Rol |
|---|---|
| `app/memory/session_extractor.py` | Prompt, parsing, extraccion, counter |
| `app/webhook/router.py` | Trigger como background task post-response |
| `app/config.py` | Settings: `session_extract_enabled`, `session_extract_interval` |
| `tests/test_session_extractor.py` | 17 tests |

---

## Walkthrough tecnico

1. **Counter**: `should_extract(phone)` incrementa un counter in-memory por telefono. Retorna `True` cada `session_extract_interval` mensajes → `session_extractor.py:34`
2. **Trigger**: Despues de enviar la respuesta al usuario, si el counter toca, se lanza `_run_session_extraction()` como background task → `router.py`
3. **Load**: Carga ultimos 20 mensajes y memorias existentes → `router.py:_run_session_extraction`
4. **Extract**: Single LLM call con prompt que incluye mensajes recientes + facts existentes + fecha actual → `session_extractor.py:119`
5. **Persist**: Cada fact se guarda como memoria con categoria (preference/personal/technical/temporal/correction) → `session_extractor.py:141`

---

## Diferencia con otros sistemas de memoria

| Sistema | Frecuencia | Scope | Accion |
|---|---|---|---|
| `fact_extractor.py` (regex) | Cada mensaje | Memorias existentes | Extraer patterns conocidos |
| **Session Extractor** (este) | Cada N msgs | Mensajes recientes | Extraer facts nuevos via LLM |
| Auto-Dream (Plan 53) | Cada 24h | Todas las memorias | Consolidar, podar, reorganizar |

---

## Decisiones de diseno

| Decision | Alternativa descartada | Motivo |
|---|---|---|
| Background task post-response | Inline antes del response | No debe agregar latencia al usuario |
| Counter in-memory (no persistido) | Counter en DB | Simplicidad; si el server reinicia, worst case es una extraccion de mas |
| `think=False` | `think=True` | Output es JSON, no requiere razonamiento visible |
| Categorias fijas (5+general) | Categorias libres | Consistencia con el sistema de memorias existente |

---

## Variables de configuracion relevantes

| Variable (`config.py`) | Default | Efecto |
|---|---|---|
| `session_extract_enabled` | `true` | Activa/desactiva la extraccion |
| `session_extract_interval` | `10` | Cada cuantos mensajes del usuario correr extraccion |
