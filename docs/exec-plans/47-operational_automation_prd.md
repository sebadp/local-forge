# PRD: Operational Automation

> **Origen:** Gap 2.5 del [Plan de Arquitectura](42-architecture_action_plan.md) (Palantir AIP Gap Analysis)
> **Independiente** — no depende de otros planes (aunque se beneficia de Plan 44: Data Provenance)

## Objetivo y Contexto

El sistema actual solo tiene cron jobs (APScheduler) y webhooks reactivos. Palantir AIP tiene automaciones event-driven basadas en datos. Nuestros gaps:

- **Sin alertas proactivas**: si el guardrail pass rate cae al 50%, nadie se entera hasta revisar el dashboard manualmente
- **Sin triggers data-driven**: no hay forma de decir "si un proyecto lleva 7 días sin actividad, notificar al usuario"
- **Mantenimiento manual**: consolidación de memorias, cleanup de trazas, re-indexación de embeddings — todo depende de cron jobs fijos sin feedback
- **Sin self-healing**: si embeddings quedan desincronizados o la DB necesita VACUUM, requiere intervención manual

La idea es evolucionar de "cron que corre cada N horas" a "triggers que reaccionan a condiciones de los datos".

## Alcance

### In Scope

1. **Trigger engine**: motor ligero de reglas `IF condition THEN action` evaluadas periódicamente
2. **Data-driven triggers**: condiciones basadas en queries a la DB (e.g. `project.last_activity_at < now() - 7 days`)
3. **Metric-based triggers**: condiciones basadas en métricas agregadas (e.g. `guardrail_pass_rate(24h) < 0.7`)
4. **Action types**: notificación al usuario (WhatsApp/Telegram), log entry, memory creation, cron adjustment
5. **Built-in automation rules**:
   - Proyecto inactivo → recordatorio al usuario
   - Guardrail pass rate bajo → alerta al admin
   - Embeddings desynced (count mismatch) → auto re-index
   - DB > N MB → auto VACUUM
   - Memorias > N sin consolidar → trigger consolidación
6. **Admin notifications channel**: mensajes de sistema al owner (distinguidos de conversación normal)
7. **Rule persistence**: reglas en tabla SQLite, configurables sin redeploy

### Out of Scope

- Event sourcing / streaming (overkill — polling periódico es suficiente)
- Reglas definidas por el usuario via chat (V2 — primero reglas hardcoded + config)
- Integración con sistemas externos de alerting (PagerDuty, Opsgenie)
- DAG / workflow engine (Airflow-like) — mantenemos simple: trigger → action
- Notificaciones a múltiples usuarios (single-tenant)

## Casos de Uso Críticos

1. **Proyecto olvidado**: usuario creó proyecto "Tesis" hace 10 días, sin actividad → sistema envía mensaje: "Hey, tu proyecto Tesis lleva 10 días sin movimiento. ¿Necesitás ayuda para retomarlo?"
2. **Calidad degradada**: guardrail pass rate baja de 95% a 60% en las últimas 24h → alerta al admin con top-3 checks que fallan más
3. **Embeddings desincronizados**: 50 memorias sin embedding (post-bulk-import) → auto-trigger de `backfill_embeddings()` en background
4. **DB maintenance**: SQLite database > 500MB → auto VACUUM en horario de baja actividad
5. **Consolidación pendiente**: >30 memorias activas sin consolidar en 7 días → trigger consolidación LLM

## Modelo de Datos

### automation_rules
```sql
CREATE TABLE automation_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    condition_type TEXT NOT NULL,     -- 'query', 'metric', 'schedule'
    condition_config TEXT NOT NULL,   -- JSON: {query, threshold, operator, ...}
    action_type TEXT NOT NULL,        -- 'notify_user', 'notify_admin', 'run_task', 'log'
    action_config TEXT NOT NULL,      -- JSON: {message_template, task_name, ...}
    enabled INTEGER DEFAULT 1,
    cooldown_minutes INTEGER DEFAULT 60,  -- min entre ejecuciones
    last_triggered_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
```

### automation_log
```sql
CREATE TABLE automation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER NOT NULL,
    triggered_at TEXT DEFAULT (datetime('now')),
    condition_value TEXT,             -- valor que disparó el trigger
    action_result TEXT,               -- 'success', 'failed', 'skipped'
    details TEXT,                     -- error message si failed
    FOREIGN KEY (rule_id) REFERENCES automation_rules(id)
);
```

## Diseño Técnico

### Trigger evaluator (`app/automation/evaluator.py`)

```python
async def evaluate_rules(repository, settings, platform_client):
    """Evalúa todas las reglas activas y ejecuta acciones si se cumplen."""
    rules = await repository.get_active_automation_rules()
    for rule in rules:
        if _in_cooldown(rule):
            continue
        condition_met, value = await _check_condition(rule, repository)
        if condition_met:
            await _execute_action(rule, value, platform_client)
            await repository.log_automation(rule.id, value, "success")
```

### Integration con APScheduler

```python
# En main.py, registrar job periódico:
scheduler.add_job(
    evaluate_automation_rules,
    trigger="interval",
    minutes=15,  # evaluar cada 15 min
    id="automation_evaluator",
)
```

### Condition types

| Type | Config example | Evaluación |
|---|---|---|
| `query` | `{"sql": "SELECT COUNT(*) FROM memories WHERE active=1", "operator": ">", "threshold": 30}` | Ejecuta query, compara resultado |
| `metric` | `{"metric": "guardrail_pass_rate", "window_hours": 24, "operator": "<", "threshold": 0.7}` | Llama a repository method existente |
| `schedule` | `{"cron": "0 3 * * *"}` | Evalúa si es hora de correr |

### Action types

| Type | Config example | Ejecución |
|---|---|---|
| `notify_user` | `{"template": "Tu proyecto {name} lleva {days} días sin actividad"}` | `platform_client.send_message()` |
| `notify_admin` | `{"template": "Guardrail pass rate: {value}%"}` | Mensaje al owner phone |
| `run_task` | `{"task": "backfill_embeddings"}` | Llama función registrada |
| `log` | `{"level": "warning", "message": "..."}` | Solo logging |

## Restricciones Arquitectónicas

- **Non-blocking**: evaluación de triggers en background, nunca en critical path de mensajes
- **Fail-safe**: errores en triggers → log + skip, nunca crash del sistema
- **Rate-limited**: cooldown por regla para evitar spam de notificaciones
- **No recursion**: acciones de tipo `notify_user` no deben triggerar procesamiento de mensaje entrante
- **SQLite-friendly**: queries de condición deben ser livianas (índices existentes, no full scans)
- **Testable**: conditions y actions testeables unitariamente con mocks de repository

## Métricas de Éxito

| Métrica | Target |
|---|---|
| Tiempo entre problema y detección | <15 min (vs manual: horas/días) |
| False positive rate en alerts | <10% |
| Acciones automáticas exitosas | >95% |
| Overhead en latencia de mensajes | 0ms (runs in background) |
| Reglas activas sin cooldown violation | 100% |
