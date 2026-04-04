# PRP: Subagent Forking & Planning Mode (Plan 57)

## Archivos a Modificar

### Feature A: Workers Paralelos
- `app/agent/loop.py`: Refactor `_run_planner_session()` para parallel dispatch
- `app/agent/models.py`: Agregar helpers `pending_tasks()`, `deps_met(task)` a `AgentPlan`

### Feature B: Subagent Fork
- `app/agent/subagent.py`: **Nuevo** — SubagentSession model + `run_subagent()`
- `app/agent/workers.py`: Opción de elevar un worker a subagent
- `app/agent/loop.py`: Integrar subagent dispatch

### Feature C: Plan Mode
- `app/agent/loop.py`: Plan review step con HITL
- `app/agent/planner.py`: `replan_with_feedback()` function

### Shared
- `app/agent/models.py`: Nuevos campos y helpers
- `tests/test_parallel_workers.py`: **Nuevo**
- `tests/test_subagent.py`: **Nuevo**
- `tests/test_plan_mode.py`: **Nuevo**

## Fases de Implementación

### Phase 1: Workers Paralelos (quick win)

- [x] Agregar a `AgentPlan` en `app/agent/models.py`:
  ```python
  def pending_tasks(self) -> list[TaskStep]:
      """Return tasks with status 'pending'."""
      return [t for t in self.tasks if t.status == "pending"]
  
  def ready_tasks(self) -> list[TaskStep]:
      """Return pending tasks whose dependencies are all done/failed."""
      done_ids = {t.id for t in self.tasks if t.status in ("done", "failed")}
      return [
          t for t in self.pending_tasks()
          if all(dep in done_ids for dep in t.depends_on)
      ]
  ```
- [x] Refactor `_run_planner_session()` en `app/agent/loop.py`:
  ```python
  # Replace sequential: task = plan.next_task() / while task is not None
  # With parallel batching:
  while not plan.all_done():
      ready = plan.ready_tasks()
      if not ready:
          break  # Deadlock protection
      
      if len(ready) == 1:
          # Single task — sequential (existing behavior)
          await _execute_single_task(ready[0], ...)
      else:
          # Multiple ready — parallel
          await wa_client.send_message(phone, f"⚡ Ejecutando {len(ready)} tareas en paralelo...")
          results = await asyncio.gather(*[
              _execute_single_task(t, ...) for t in ready
          ], return_exceptions=True)
          for task, result in zip(ready, results):
              if isinstance(result, Exception):
                  task.status = "failed"
                  task.result = f"Error: {result}"
              # else: already updated inside _execute_single_task
  ```
- [x] Extraer `_execute_single_task()` como helper (refactor del código inline actual)
- [x] **IMPORTANTE**: Semáforo para Ollama — las requests LLM siguen siendo secuenciales:
  ```python
  _ollama_semaphore = asyncio.Semaphore(1)  # 1 concurrent LLM call
  ```
  Cada worker acquires el semáforo antes de llamar a Ollama. Las tool calls (file I/O, shell) son paralelas, pero LLM calls se serializan.
- [x] Tests: plan con 3 tasks paralelas + 1 dependiente, verify correct execution order

### Phase 2: Plan Mode Interactivo

- [x] En `_run_planner_session()`, después de crear el plan:
  ```python
  # Phase 1.5 — REVIEW: Let user review plan before execution
  if settings.agent_plan_review:  # New setting, default True
      plan_text = plan.to_markdown()
      approval = await request_user_approval(
          session.phone_number,
          f"📋 *Plan de ejecución:*\n{plan_text}\n\n¿Procedo? (sí / modificar / cancelar)",
          wa_client,
      )
      
      lower = approval.lower().strip()
      if lower in ("cancelar", "no", "cancel"):
          session.status = AgentStatus.COMPLETED
          return "❌ Sesión cancelada por el usuario."
      elif lower not in ("sí", "si", "dale", "ok", "yes", "go", "procede"):
          # Treat as modification request
          plan = await replan_with_feedback(
              session.objective, plan, approval, ollama_client
          )
          session.plan = plan
          session.task_plan = plan.to_markdown()
          # Show updated plan (no second approval to avoid loop)
          await wa_client.send_message(
              session.phone_number,
              f"📋 *Plan actualizado:*\n{plan.to_markdown()}\n\n▶️ Ejecutando...",
          )
  ```
- [x] Agregar `replan_with_feedback()` a `app/agent/planner.py`:
  ```python
  async def replan_with_feedback(
      objective: str, 
      current_plan: AgentPlan, 
      user_feedback: str,
      ollama_client: OllamaClient,
  ) -> AgentPlan:
      """Regenerate plan incorporating user's modification request."""
  ```
  Prompt: "Current plan: {plan}. User feedback: {feedback}. Generate updated plan."
- [x] Setting `agent_plan_review: bool = True` en `app/config.py`
- [x] Tests: approval flow, modification flow, cancellation flow

### Phase 3: Subagent Fork

- [x] Crear `app/agent/subagent.py`:
  ```python
  @dataclass
  class SubagentConfig:
      objective: str
      tools: list[str]  # Tool names to include
      max_iterations: int = 5
      timeout_seconds: float = 120.0
      parent_session_id: str | None = None
  
  async def run_subagent(
      config: SubagentConfig,
      ollama_client: OllamaClient,
      skill_registry: SkillRegistry,
      mcp_manager: McpManager | None,
      hitl_callback,
      ollama_semaphore: asyncio.Semaphore,
  ) -> str:
      """Run a mini agent loop with focused tools and objective.
      
      Returns the final text result from the subagent.
      """
      messages = [ChatMessage(
          role="system",
          content=_SUBAGENT_SYSTEM_PROMPT.format(objective=config.objective),
      )]
      messages.append(ChatMessage(role="user", content=config.objective))
      
      # Filter tools to only those in config.tools
      filtered_tools = [
          t for t in skill_registry.get_ollama_tool_schemas()
          if t["function"]["name"] in config.tools
      ]
      
      for iteration in range(config.max_iterations):
          async with ollama_semaphore:
              reply, tool_calls = await ollama_client.chat(
                  messages=messages, tools=filtered_tools, think=False
              )
          
          if not tool_calls:
              return reply  # Done
          
          # Execute tool calls (these run without semaphore — I/O is parallel)
          for tc in tool_calls:
              result = await skill_registry.execute_tool(tc.name, tc.arguments)
              messages.append(ChatMessage(role="tool", content=result, name=tc.name))
      
      return reply  # Max iterations reached
  ```
- [x] En `workers.py`, agregar heurística para elevar a subagent:
  ```python
  # If task description is complex (contains multiple action verbs or >100 chars)
  # and worker_type is "general" or "coder", use subagent instead of single-turn
  def should_use_subagent(task: TaskStep) -> bool:
      if task.worker_type not in ("general", "coder"):
          return False
      action_words = ["create", "build", "implement", "write", "modify", "add", "fix", "test"]
      count = sum(1 for w in action_words if w in task.description.lower())
      return count >= 3 or len(task.description) > 150
  ```
- [x] Max concurrent subagents: `_MAX_CONCURRENT_SUBAGENTS = 3`
- [x] Subagent timeout con `asyncio.wait_for()`
- [x] Persistencia: append subagent messages to parent session JSONL
- [x] Tests: subagent completes task, timeout handling, tool filtering

### Phase 4: Documentación & QA

- [x] `make test` pasa
- [x] `make lint` pasa
- [x] E2E test manual: agent session con 3 parallel tasks + plan review
- [x] Crear `docs/features/57-subagent_planning.md`
- [x] Actualizar `AGENTS.md`: subagent architecture, plan mode
- [x] Actualizar `CLAUDE.md`: parallel workers pattern, ollama semaphore

## Dependencias entre Phases

```
Phase 1 (Workers Paralelos) ← independiente, puede hacerse primero
Phase 2 (Plan Mode)         ← independiente, puede hacerse primero
Phase 3 (Subagent Fork)     ← requiere Phase 1 (semáforo) pero no Phase 2
Phase 4 (Docs)              ← requiere 1+2+3
```

## Nota sobre Ollama y Paralelismo

Ollama procesa requests de forma secuencial por modelo (a menos que se configure `OLLAMA_NUM_PARALLEL`). Esto significa que aunque tengamos 3 workers "paralelos", las llamadas LLM se serializan naturalmente. El paralelismo real ocurre en:
- Tool execution (file I/O, shell commands, HTTP requests)
- Processing results y building messages
- Status updates al usuario

El semáforo de Ollama (`asyncio.Semaphore(1)`) formaliza esta restricción y evita errores de concurrencia. Si el usuario configura `OLLAMA_NUM_PARALLEL > 1`, se puede aumentar el semáforo.
