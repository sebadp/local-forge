# PRD: MicroCompact & Deferred Tool Loading — Context Window Optimization (Plan 54)

## Objetivo y Contexto

### Problema

LocalForge corre con Ollama y modelos de 8K-32K tokens de contexto. A diferencia de Claude Code (200K tokens), cada byte de contexto es valioso. Hay dos fuentes de desperdicio:

**1. Tool results acumulados**: En el tool calling loop (max 5 iteraciones), los resultados de tools anteriores permanecen completos en el contexto. Un `search_source_code` puede retornar 2000 tokens que ya no son relevantes en la iteración 4. `_clear_old_tool_results()` existe pero solo opera en el agent loop, no en el flow normal.

**2. Tool definitions en el system prompt**: Cuando `select_tools()` retorna 8-12 tools, cada definición ocupa ~100-200 tokens. En total, ~1500 tokens de schema que el LLM necesita parsear pero que en el 90% de los casos solo usará 1-2 tools. Claude Code resuelve esto con `ToolSearchTool` — carga un subset mínimo y el LLM pide más si necesita.

### Inspiración: Claude Code

- **MicroCompact** (`microCompact.ts`): Reemplaza resultados de tools viejos con `[Old tool result content cleared]`. Solo compacta tools específicos (FileRead, Bash, Grep, Glob, etc.). Time-based: los más viejos se limpian primero.
- **ToolSearch** (`ToolSearchTool.ts`): Las tools se cargan "deferred" — solo el nombre aparece en el contexto. Cuando el LLM necesita una tool, llama `ToolSearch` para obtener el schema completo.

### Solución

Dos mejoras independientes que se complementan:

**A. MicroCompact selectivo**: Antes de cada LLM call (tanto en flow normal como en tool loop), limpiar resultados de tools de rondas anteriores que superen un threshold de antigüedad o tamaño. Reemplazarlos con un summary ultra-breve.

**B. Lazy tool loading**: Cargar solo las 3-4 tools del skill clasificado + un meta-tool `discover_tools`. Si el LLM necesita algo fuera del skill activo, llama `discover_tools` para obtener el catálogo y re-seleccionar.

## Alcance

### In Scope

#### Feature A: MicroCompact
- Función `microcompact_messages()` que opera sobre `list[ChatMessage]`
- Configurable: `MICROCOMPACT_MAX_AGE_ROUNDS = 2` (resultados de ≥2 rondas atrás se compactan)
- Solo compactar tools verbosos: `web_search`, `web_research`, `search_source_code`, `read_source_file`, `get_recent_messages`, `search_notes`, `run_command`
- El replacement es determinístico: `[Tool result from {tool_name} cleared — returned {N} chars]`
- Integrar en `execute_tool_loop()` y en `_build_context()` del webhook router

#### Feature B: Lazy Tool Loading
- Nuevo tool handler `discover_tools` en `app/skills/tools/meta_tools.py`
- `select_tools()` retorna solo top-K tools (K=4) + `discover_tools` siempre presente
- `discover_tools(query: str)` → retorna lista de tools con descriptions que matchean la query
- El LLM puede pedir `discover_tools("weather")` y obtener los schemas de `get_weather`, `get_forecast`

### Out of Scope
- Cambiar el formato de tool results existente
- Compactar resultados de tools de la ronda actual
- Full deferred loading con schemas dinámicos (solo simplificamos la selección)
- Cache de tool schemas (innecesario con Ollama local)

## Casos de Uso Críticos

1. **Tool loop largo**: El agente llama 4 tools en secuencia. En la iteración 5, el contexto tiene ~6000 tokens de results anteriores. MicroCompact reduce a ~1000.
2. **Tool equivocado**: El LLM recibe solo tools de `calculator` pero el usuario pidió el clima. El LLM llama `discover_tools("clima")` y obtiene `get_weather`.
3. **Contexto limitado**: Con qwen3.5:9b (32K), una conversación larga + tool results llena el contexto. MicroCompact libera espacio para los mensajes más recientes.

## Restricciones

- MicroCompact debe ser **determinístico** — sin LLM calls. Solo string replacement.
- `discover_tools` es un tool handler normal, registrado en SkillRegistry
- No modificar la interfaz de `OllamaClient.chat()` — operar sobre los mensajes antes de enviarlos
- Best-effort: si microcompact falla, enviar el contexto sin compactar
