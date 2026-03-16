# PRD: Planner-Orchestrator para el Agent Loop

## Objetivo y Contexto

El agent loop actual (`app/agent/loop.py`) usa un single-loop reactivo: un solo LLM con un system prompt fijo hace todo (planifica, ejecuta tools, evalúa y decide cuándo terminar). Esto presenta dos limitaciones:

1. **No entiende el contexto antes de actuar**: Para tareas como dev-review, el primer paso debería ser *leer la conversación* del usuario para entender qué pasó, pero el agente actual salta directo a ejecutar tools sin una fase de comprensión.
2. **No hay separación planner/worker**: El mismo LLM call hace planning y execution, mezclando responsabilidades y desperdiciando context window.

### Por qué Orchestrator-Workers (Anthropic)

- El patrón Orchestrator-Workers de Anthropic descompone tareas dinámicamente, delega a workers, y sintetiza resultados
- Workers reciben: tarea original, subtarea específica, formato de output esperado, tools disponibles, y boundaries claros
- El plan se guarda en *memoria externa* (no solo en context window) porque el contexto puede truncarse
- ToolOrchestra (NVIDIA) demostró que un orquestador basado en Qwen3-8B supera a competidores más grandes cuando tiene task decomposition estructurado + tool descriptions claras

### Motivación inmediata

- Habilitar `/dev-review`: un comando que analiza las últimas interacciones de un usuario para encontrar bugs, alucinaciones, y áreas de mejora
- Sentar las bases para cualquier tarea agéntica compleja que requiera múltiples fases

## Alcance (In Scope & Out of Scope)

### In Scope
- Planner agent que entiende contexto, crea plan estructurado, y delega a workers
- Workers especializados por tipo (reader, analyzer, coder, reporter, general)
- 3 fases: UNDERSTAND → EXECUTE → SYNTHESIZE con replanning
- Debug tools para introspección (review_interactions, get_tool_output_full, etc.)
- Comando `/dev-review` como primer caso de uso
- Repository methods para acceder a trazas y transcripts por teléfono

### Out of Scope
- Multi-agent paralelo (por ahora workers son secuenciales)
- Cambios al tool calling loop principal (`execute_tool_loop` para el flujo normal de chat)
- Nuevos modelos LLM (todo usa qwen3.5:9b)
- UI/UX changes en WhatsApp (el output sigue siendo texto)

## Casos de Uso Críticos

1. **Dev-review**: `/dev-review +5491234567` — lee conversación, identifica anomalías en trazas, diagnostica bugs, genera reporte
2. **Coding tasks**: `/agent "Agrega validación al endpoint X"` — planner crea pasos (leer código → entender → editar → testear), workers ejecutan
3. **Research tasks**: `/agent "Investiga por qué el bot alucinó ayer"` — planner lee logs, workers analizan trazas

## Restricciones Arquitectónicas / Requerimientos Técnicos

- **Modelo**: qwen3.5:9b — context window limitado, requiere prompts concisos y outputs JSON simples
- **Backward compatibility**: Todas las sesiones agénticas usan el planner. Si el JSON parse del plan falla → fallback a plan lineal (1 task type=general)
- **Seguridad**: Workers heredan el PolicyEngine + AuditTrail existente via hitl_callback
- **Persistencia**: Plan snapshots incluidos en el JSONL de persistence.py
- **Max replans**: 3 (hard cap, igual que Magentic-One)
- **Think mode**: `think: False` para planner calls (structured output, no thinking blocks)
