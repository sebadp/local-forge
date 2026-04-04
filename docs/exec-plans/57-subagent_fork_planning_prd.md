# PRD: Subagent Forking & Planning Mode (Plan 57)

## Objetivo y Contexto

### Problema

El agent mode actual tiene dos limitaciones arquitectónicas:

**1. Workers ejecutan secuencialmente**. El planner genera tasks con `depends_on` que permitirían paralelismo, pero `_run_planner_session()` itera `plan.next_task()` uno a uno. Para un proyecto con 4 tasks independientes, esto 4x el tiempo de ejecución.

**2. No hay "fork" real**. Claude Code puede spawnar subagentes que:
- Heredan el contexto conversacional del padre
- Corren en background con su propia conversation loop
- Reportan resultados via notificaciones
- Pueden tener tools distintas al padre

En LocalForge, el planner delega a workers, pero estos son **single-turn**: ejecutan el tool loop y terminan. No pueden hacer work complejo multi-step de forma autónoma. Si un worker necesita "leer 5 archivos, analizar, y escribir un reporte", eso son 5+ tool calls dentro del mismo worker turn — factible pero frágil.

**3. No hay plan mode**. Claude Code tiene `EnterPlanMode` donde el agente piensa y planifica sin ejecutar tools de escritura. En LocalForge, el planner genera un plan pero el usuario no puede revisarlo ni ajustarlo antes de la ejecución (el plan se ejecuta inmediatamente).

### Solución

#### A. Workers Paralelos con `asyncio.gather`
El cambio más simple y de mayor impacto: cuando el planner genera tasks sin dependencias entre sí, ejecutarlas en paralelo.

#### B. Subagent Fork
Para tasks complejas, permitir que un worker se convierta en un **mini-agente** con su propio loop, context, y tool budget. Cada subagente:
- Recibe un objective específico y un subset de tools
- Tiene su propio tool calling loop (max N iterations)
- Persiste su sesión en JSONL (reutilizar `persistence.py`)
- Reporta resultado al orchestrator cuando termina

#### C. Plan Mode Interactivo
Antes de ejecutar, el agente envía el plan al usuario por WhatsApp y espera confirmación. El usuario puede:
- Aprobar: "dale" / "ok" → ejecutar
- Modificar: "cambiá el paso 3 por X" → replanner
- Rechazar: "no" / "cancelar" → abortar

## Alcance

### In Scope

#### Feature A: Workers Paralelos
- Modificar `_run_planner_session()` para detectar tasks sin dependencias pendientes
- Ejecutar tasks paralelas con `asyncio.gather`
- Merge de resultados al finalizar cada batch paralelo
- Status updates: "⚡ Ejecutando 3 tareas en paralelo..."

#### Feature B: Subagent Fork
- `SubagentSession` model: objective, tools, max_iterations, parent_session_id
- `run_subagent()` function: mini agent loop que reusa `execute_tool_loop()`
- El orchestrator puede decidir convertir un worker en subagent si la task es compleja (>3 steps en la descripción)
- Persistencia en `data/agent_sessions/` (reusar estructura existente)
- Timeout per-subagent (default 120s)

#### Feature C: Plan Mode
- Después de Phase 1 (plan creado), enviar plan formateado al usuario via WA
- Esperar reply del usuario (reusar mecanismo de HITL)
- Parse de la respuesta: aprobar / modificar / rechazar
- Si modifica: llamar `replan()` con el feedback del usuario

### Out of Scope
- Subagentes que corren en procesos separados (todo es asyncio in-process)
- Subagentes con modelos diferentes (todos usan el mismo Ollama model)
- Plan mode para el flow normal (solo para agent mode)
- Herencia de contexto conversacional completo en subagentes (solo reciben el objective + resultados previos)

## Casos de Uso

### Workers Paralelos
El planner genera:
```json
{"tasks": [
  {"id": 1, "description": "Read project structure", "depends_on": []},
  {"id": 2, "description": "Search for API endpoints", "depends_on": []},
  {"id": 3, "description": "Analyze test coverage", "depends_on": []},
  {"id": 4, "description": "Write summary report", "depends_on": [1, 2, 3]}
]}
```
Tasks 1, 2, 3 corren en paralelo. Task 4 espera a que las 3 terminen.

### Plan Mode
```
LocalForge: 📋 Plan creado:
1. [ ] Leer estructura del proyecto
2. [ ] Buscar endpoints de API
3. [ ] Analizar cobertura de tests
4. [ ] Escribir reporte

¿Procedo? (sí/modificar/cancelar)

Usuario: sí, pero no analices tests, enfocate en la arquitectura

LocalForge: 📋 Plan actualizado:
1. [ ] Leer estructura del proyecto
2. [ ] Buscar endpoints de API
3. [ ] Analizar patrones arquitectónicos
4. [ ] Escribir reporte

¿Procedo?

Usuario: dale
```

## Restricciones

- Workers paralelos NO deben escribir al mismo archivo simultáneamente
- Subagentes heredan el security policy del padre (no bypass)
- Plan mode timeout: 5 minutos. Si el usuario no responde, abortar sesión
- Max subagentes concurrentes por sesión: 3 (protección de recursos Ollama)
- Subagentes comparten el Ollama instance — las requests son secuenciales a nivel de LLM
