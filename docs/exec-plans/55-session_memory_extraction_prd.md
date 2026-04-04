# PRD: Session Memory — LLM-Powered Fact Extraction Continua (Plan 55)

## Objetivo y Contexto

### Problema

`fact_extractor.py` usa regex para extraer facts del usuario (nombre, GitHub username, etc.). Es rápido pero limitado:
- Solo captura patrones predefinidos hardcodeados
- No detecta preferencias implícitas ("siempre me gusta que respondas con ejemplos")
- No detecta facts técnicos ("estoy usando React con TypeScript en este proyecto")
- No detecta contexto temporal ("esta semana tengo exámenes")

Los daily logs (`memory/daily_log.py`) registran actividad pero son append-only — no extraen signal de la conversación.

### Inspiración: Claude Code `SessionMemory`

Claude Code tiene `SessionMemory/sessionMemory.ts` que:
1. Corre periódicamente en background (cada N tool calls o N tokens generados)
2. Usa un subagente forked que lee los mensajes recientes
3. Extrae facts y los persiste en un archivo `.md` de sesión
4. Se acumula durante la conversación y luego `autoDream` lo consolida

### Solución

Agregar una fase de extracción LLM-powered que corra **después de cada N mensajes del usuario** (no en cada mensaje — sería demasiado costoso con Ollama local). La extracción:

1. Lee los últimos K mensajes desde la última extracción
2. Hace un single LLM call con un prompt de extracción
3. Persiste los facts nuevos como memorias en la DB + daily log
4. No interrumpe el flow principal (corre como background task)

### Diferencia con Plan 53 (Auto-Dream)

| | Plan 53: Auto-Dream | Plan 55: Session Memory |
|---|---|---|
| **Frecuencia** | Cada 24h | Cada N mensajes (~10) |
| **Scope** | Todas las memorias + daily logs | Solo mensajes recientes |
| **Acción** | Consolidar, podar, reorganizar | Extraer facts nuevos |
| **Trigger** | Scheduler cron | Inline post-message (background) |

Son complementarios: Plan 55 extrae signal fresco, Plan 53 lo consolida periódicamente.

## Alcance

### In Scope
- Prompt de extracción de facts para Ollama (qwen3.5:9b, `think=False`)
- Función `extract_session_facts()` que procesa los últimos N mensajes
- Integración como background task post-message en el webhook router
- Gate: solo correr cada `SESSION_EXTRACT_INTERVAL` mensajes (default 10)
- Persistencia de facts extraídos como memorias + append a daily log
- Counter de mensajes desde última extracción (en memoria, per-phone)

### Out of Scope
- Reemplazar `fact_extractor.py` regex (sigue siendo útil para Phase C, es gratis)
- Extracción de facts de mensajes de audio/imagen (solo texto)
- Aprobación del usuario para guardar facts (best-effort, silent)
- Facts cross-conversación (eso es Plan 53)

## Casos de Uso Críticos

1. **Preferencia implícita**: Después de 10 mensajes, el usuario siempre responde en español y pide respuestas concisas. La extracción detecta: "El usuario prefiere respuestas concisas en español".
2. **Contexto técnico**: El usuario menciona "estoy en mi proyecto de React" → fact: "El usuario tiene un proyecto de React activo".
3. **Evento temporal**: "La semana que viene tengo un viaje a Buenos Aires" → fact con fecha absoluta: "El usuario viaja a Buenos Aires la semana del 2026-04-06".
4. **Corrección**: El usuario dice "no, mi nombre es Sebastián, no Daniel" → fact que corrige uno anterior.

## Restricciones

- Single LLM call por extracción (no multi-turn, es background y no debe saturar Ollama)
- `think=False` obligatorio (output JSON, no razonamiento)
- Best-effort: si la extracción falla, no se pierde nada (los mensajes siguen ahí)
- No bloquear el response al usuario — debe correr después de enviar la respuesta
- Respetar el rate limiter de Ollama (si hay otra request en flight, skip esta extracción)
