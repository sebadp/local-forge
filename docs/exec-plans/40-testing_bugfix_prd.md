# PRD: Testing Session Bugfix Sprint

## Objetivo y Contexto

La sesion de testing del 2026-03-09 revelo 19 bugs que afectan features completas del sistema.
Los problemas se dividen en 4 categorias:

1. **Agent mode inutilizable** — web search roto (paquete deprecado), synthesizer fabrica datos,
   workers no propagan resultados entre si, resultados vacios se marcan como "done"
2. **Scheduler/cron inalcanzable** — classifier sin ejemplos, cron tools ausentes de TOOL_CATEGORIES
3. **Datetime roto** — timezone argentina invalida, convert_timezone devuelve ano 1900
4. **MCP parcialmente roto** — naming mismatch, Puppeteer codigo muerto, cold-start

El testing document completo esta en: `docs/testing/40-testing_session_2026_03_09.md`

## Alcance

### In Scope
- Migrar `duckduckgo_search` a `ddgs` (paquete renombrado) en search y news tools
- Hardening del agent planner: synthesizer honesto, workers detectan vacios, depends_on funcional
- Completar TOOL_CATEGORIES y classifier prompt con ejemplos faltantes
- Fix datetime tools (year 1900, timezone aliases)
- Habilitar Puppeteer MCP (infraestructura Docker ya lista, solo falta config)
- Fix MCP naming mismatch y Dockerfile pre-install
- Agregar agent result al historial de conversacion
- System prompt: anti-hallucination + anti-empty directives

### Out of Scope
- Cambiar proveedor de busqueda (SearXNG, Brave) — evaluar post-migration a ddgs
- Refactor completo del agent planner architecture
- Nuevos tests de integracion end-to-end del agent mode

## Casos de Uso Criticos

1. **Usuario pide "recuerdame en 5 minutos revisar los logs"** — debe clasificar como `time`,
   seleccionar `schedule_task`, ejecutar correctamente
2. **Usuario pide "crea un cron todos los dias a las 9am"** — debe clasificar como `time`,
   seleccionar `create_cron`, ejecutar correctamente
3. **`/agent busca informacion sobre X en internet`** — web_search debe retornar resultados,
   workers deben propagar datos entre dependencias, synthesizer debe reportar honestamente
   si no encontro nada
4. **Usuario pregunta "que hora es en Misiones?"** — debe resolver alias a Buenos_Aires timezone
5. **Usuario pide "convierte 11:55 de Argentina a Japon"** — debe usar fecha de hoy, no 1900

## Restricciones

- `duckduckgo-search` -> `ddgs`: el paquete es el mismo upstream, solo cambio de nombre.
  La API `DDGS().text()` y `DDGS().news()` se mantiene identica
- Todos los cambios deben pasar `make check` (lint + typecheck + tests)
- No romper backward compat del agent mode existente (reactive loop)
- MCP config changes deben ser backward-compatible (servers existentes siguen funcionando)
