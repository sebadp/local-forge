# Testing Manual: Subagent Fork & Planning Mode

> **Feature documentada**: [`docs/features/57-subagent_planning.md`](../features/57-subagent_planning.md)
> **Requisitos previos**: Container corriendo, Ollama disponible.

---

## Verificar que la feature está activa

Plan Mode y Subagent Fork están siempre activos en agent mode (no tienen feature flag).

```bash
docker compose logs -f localforge | grep -i "plan_mode\|subagent"
```

---

## Casos de prueba: Plan Mode (HITL)

| Mensaje / Acción | Resultado esperado |
|---|---|
| `/agent creame una API con tests y pusheala a GitHub` | Bot envía plan con N pasos, pregunta "¿Aprobás el plan?" |
| Responder `sí` / `ok` / `dale` al plan | Plan se ejecuta normalmente |
| Responder `cancelar` / `no` | Sesión se aborta, mensaje de confirmación |
| Responder `cambiá el paso 3 por usar PostgreSQL en vez de SQLite` | `replan_with_feedback()` genera plan modificado, se ejecuta |
| No responder en 5 minutos | Timeout: sesión se cancela automáticamente |

### Verificar en logs

```bash
# Plan mode
docker compose logs -f localforge 2>&1 | grep -i "plan_mode\|approval\|replan"

# HITL interaction
docker compose logs -f localforge 2>&1 | grep -i "hitl\|user_approval"
```

---

## Casos de prueba: Subagent Fork

| Mensaje / Acción | Resultado esperado |
|---|---|
| `/agent` con tarea compleja (descripción >150 chars o ≥3 action words) | Task se eleva a subagent. Logs muestran `should_use_subagent: True`, `run_subagent` |
| `/agent` con tarea simple (1 action word, corta) | Task se ejecuta normalmente sin subagent |

### Verificar elevación

```bash
# Subagent decisions
docker compose logs -f localforge 2>&1 | grep -i "should_use_subagent\|run_subagent"

# Subagent timeout
docker compose logs -f localforge 2>&1 | grep -i "subagent.*timeout"
```

---

## Casos de prueba: Workers Paralelos

| Mensaje / Acción | Resultado esperado |
|---|---|
| `/agent` con plan que tiene tasks independientes (sin deps entre sí) | Tasks corren en paralelo via `asyncio.gather`. Logs muestran ejecución simultánea |
| `/agent` con plan secuencial (cada task depende del anterior) | Tasks corren secuencialmente |

---

## Edge cases y validaciones

| Escenario | Resultado esperado |
|---|---|
| Subagent excede timeout (120s default) | `asyncio.wait_for` cancela, error reportado al plan |
| Replan con feedback ambiguo | LLM hace su mejor intento de modificar el plan |
| Plan con 0 tasks | No se ejecuta nada, sesión termina |
| Subagent falla a mitad de ejecución | Error capturado, task marcada como failed, plan continúa con otros tasks |
| Subagent intenta tool bloqueado por policy | PolicyEngine rechaza igual que en tool loop normal |

---

## Tests automatizados

```bash
.venv/bin/python -m pytest tests/test_subagent.py -v
# 10 tests: should_use_subagent heuristic, run_subagent, timeout, security
```

---

## Troubleshooting

| Problema | Causa probable | Solución |
|---|---|---|
| Plan mode nunca aparece | No se está usando `/agent` | Plan mode solo se activa en agent sessions |
| Subagent siempre timeout | Ollama lento | Aumentar timeout o usar modelo más rápido |
| Plan no se modifica correctamente | `replan_with_feedback` retorna plan sin cambios | Dar feedback más específico |
| Workers no corren en paralelo | Todas las tasks tienen dependencias | Normal — parallel solo aplica a tasks sin deps |

---

## Variables relevantes para testing

| Variable | Valor de test | Efecto |
|---|---|---|
| Plan mode timeout | 5 min (hardcoded) | Tiempo de espera para aprobación del usuario |
| Subagent timeout | 120s (default en SubagentConfig) | Timeout de ejecución del subagent |
| `should_use_subagent` threshold | ≥3 action words o >150 chars | Heurística para elevar a subagent |
