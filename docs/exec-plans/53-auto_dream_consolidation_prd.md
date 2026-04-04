# PRD: Auto-Dream — Memory Consolidation Background (Plan 53)

## Objetivo y Contexto

### Problema

Las memorias de LocalForge crecen con cada sesión pero solo se consolidan si se llama manualmente o cuando se alcanza un threshold simple (`MIN_MEMORIES = 8`). El consolidator actual (`app/memory/consolidator.py`) solo hace dedup/contradicción — no sintetiza, no reorganiza, no poda el índice.

Con el tiempo, las memorias acumulan:
- Facts obsoletos que ya no reflejan la realidad
- Memorias redundantes que dicen lo mismo con distintas palabras
- Daily logs que nunca se integran en memorias permanentes
- El `MEMORY.md` crece sin control y pierde utilidad como índice rápido

### Inspiración: Claude Code `autoDream`

Claude Code ejecuta un **"dream"** en background cada 24h (mínimo 5 sesiones): un subagente lee transcripts recientes, consolida memorias, y poda el índice. 4 fases: Orient → Gather → Consolidate → Prune.

### Solución

Implementar un job de APScheduler que periódicamente lance un proceso de consolidación inteligente en 4 fases:

1. **Orient**: Leer `MEMORY.md` y memorias existentes para entender el estado actual
2. **Gather**: Leer daily logs recientes + mensajes desde la última consolidación
3. **Consolidate**: Merge, dedup, actualizar facts obsoletos, convertir dates relativos → absolutos
4. **Prune**: Mantener `MEMORY.md` como índice conciso, eliminar memorias superseded

A diferencia de Claude Code (que usa forked subagents con la API de Anthropic), nosotros lo hacemos con un prompt directo a Ollama, reutilizando la infraestructura existente.

## Alcance

### In Scope
- Job de APScheduler que corre cada N horas (configurable, default 24h)
- Gate: solo ejecutar si hay ≥ N sesiones/conversaciones desde la última consolidación
- Lock file para evitar consolidaciones concurrentes
- Prompt de 4 fases inspirado en Claude Code, adaptado a nuestra estructura de memoria
- Persistencia del timestamp de última consolidación
- Logging estructurado de qué se consolidó/podó

### Out of Scope
- Cambiar la estructura de `MEMORY.md` o el schema de memorias en SQLite
- Leer transcripts de sesiones agénticas completas (solo daily logs + mensajes recientes)
- Consolidación cross-usuario (cada usuario se consolida independientemente)
- UI para aprobar cambios de consolidación (es automático y best-effort)

## Casos de Uso Críticos

1. **Acumulación natural**: Después de 7 días de uso, el usuario tiene 40 memorias, muchas redundantes. El dream corre y las reduce a 25 memorias limpias.
2. **Facts obsoletos**: La memoria dice "el usuario trabaja en X" pero hace 3 días dijo "cambié de trabajo a Y". El dream detecta la contradicción y actualiza.
3. **Daily logs → memorias permanentes**: Los daily logs de la semana mencionan repetidamente "el usuario está preparando una presentación para el viernes". El dream lo extrae como un fact temporal con fecha absoluta.
4. **Índice bloated**: `MEMORY.md` tiene 80 entradas. El dream lo poda a las 40 más relevantes.

## Restricciones Arquitectónicas

- Usar `OllamaClient.chat()` con `think=False` (es JSON/structural output)
- Correr como background task trackeada por `_track_task()` 
- Best-effort: errores se logean, nunca bloquean el pipeline principal
- El job corre en el scheduler existente en `app/main.py`
- Lock via archivo `.consolidation_lock` en `data/` (como Claude Code hace con `consolidationLock.ts`)
- Registrar en tracing como span "dream_consolidation"
