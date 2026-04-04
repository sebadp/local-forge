# Feature: Subagent Forking & Planning Mode

> **Version**: v1.0
> **Fecha de implementacion**: 2026-04-02
> **Fase**: Fase 6
> **Estado**: ✅ Implementada

---

## Que hace?

Tres mejoras al agent mode: (A) el usuario puede revisar y modificar el plan antes de que se ejecute, (B) tasks complejas se elevan a "subagentes" con su propio tool loop, y (C) tasks sin dependencias se ejecutan en paralelo (implementado en Plan 56).

---

## Arquitectura

```
/agent "build an API with tests"
        │
        ▼
  Planner creates plan (3-4 tasks)
        │
        ▼
  PLAN MODE: Send plan to user via HITL
        │
        ├── "sí" / "ok" → Execute
        ├── "cambiá X por Y" → replan_with_feedback() → Execute
        └── "cancelar" → Abort
        │
        ▼
  Execute tasks (parallel if deps allow)
        │
        ├── Simple task → execute_tool_loop (1 turn)
        └── Complex task → run_subagent (multi-turn mini loop)
```

---

## Componentes

### A. Plan Mode (`app/agent/loop.py`)
- After plan creation, sends plan to user via HITL with 5-min timeout
- Approve: "sí", "ok", "dale", "yes", "go" → execute
- Cancel: "cancelar", "no" → abort session
- Modify: anything else → `replan_with_feedback()` → execute updated plan

### B. Subagent Fork (`app/agent/subagent.py`)
- `SubagentConfig`: objective, tool_names, max_iterations, timeout
- `should_use_subagent()`: heuristic based on action word count (>=3) or description length (>150 chars)
- `run_subagent()`: creates independent message history, runs `execute_tool_loop`, returns result
- Timeout protection via `asyncio.wait_for()`
- Integrated into `workers.py` — automatically elevates complex tasks

### C. Replan with Feedback (`app/agent/planner.py`)
- `replan_with_feedback()`: LLM call to revise plan based on user's modification request
- Receives current plan + user feedback → generates updated plan

---

## Archivos clave

| Archivo | Rol |
|---|---|
| `app/agent/subagent.py` | SubagentConfig, should_use_subagent, run_subagent |
| `app/agent/loop.py` | Plan mode HITL integration |
| `app/agent/planner.py` | replan_with_feedback() |
| `app/agent/workers.py` | Subagent elevation in execute_worker |
| `tests/test_subagent.py` | 10 tests |

---

## Decisiones de diseno

| Decision | Alternativa | Motivo |
|---|---|---|
| Plan review siempre (no configurable) | Setting on/off | El feedback del usuario es valioso, 5 min timeout protege contra olvidos |
| Subagent reusa execute_tool_loop | Loop custom | Reutiliza toda la infra existente (tools, security, tracing) |
| Heuristica simple (action words) | LLM-based classification | Zero-cost, deterministic, funciona bien para el 90% de los casos |
| Max 1 replan por sesion (no loop) | Multiple rounds | Evita loops infinitos de modificacion |

---

## Gotchas

- **Subagent hereda security policy**: pasa por policy_engine igual que tools normales
- **Timeout subagent**: default 120s. Si Ollama es lento, puede necesitar ajuste
- **Plan mode timeout**: 5 min. Si el usuario no responde, sesion se cancela
- **Ollama serialization**: subagentes comparten Ollama instance — LLM calls son secuenciales naturalmente
