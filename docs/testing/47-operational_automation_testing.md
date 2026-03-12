# Testing: Operational Automation

## Tests automatizados

Archivo: `tests/test_automation.py` — 30 tests

### Cobertura

| Área | Tests | Qué verifica |
|---|---|---|
| Cooldown | 3 | Never triggered, within cooldown, expired cooldown |
| Condition: query | 3 | SELECT valid, non-SELECT rejected, threshold not met |
| Condition: metric | 4 | embedding_desync, unconsolidated (empty), unknown metric, project_inactive |
| Condition: schedule | 2 | Matching cron, non-matching cron |
| Actions | 6 | notify_user, notify_admin no phone, run_task unknown, vacuum, log, fail-safe |
| Evaluator | 2 | Full loop (1 triggers, 1 doesn't), cooldown respected |
| Repository CRUD | 5 | seed+get, seed idempotent, toggle, toggle nonexistent, log+retrieve |
| Builtin rules | 2 | Seed all, seed idempotent |
| Automation tools | 3 | list_rules, toggle, get_log |

### Ejecutar

```bash
.venv/bin/python -m pytest tests/test_automation.py -v
```

## Testing manual

### Prerequisitos
1. `AUTOMATION_ENABLED=true` en `.env`
2. `AUTOMATION_ADMIN_PHONE=<tu_numero>` en `.env`

### Casos de prueba

1. **Verificar seed al startup**: revisar logs por `"Operational automation enabled"`
2. **Verificar reglas creadas**: enviar mensaje "show automation rules" → debería listar las 5 reglas built-in
3. **Deshabilitar regla**: enviar "disable the project_inactive rule" → tool toggle_automation_rule
4. **Verificar logs**: enviar "show automation log" → debería mostrar historial de ejecuciones
5. **Trigger manual**: crear >30 memorias antiguas, esperar ciclo de evaluación → debería triggerar consolidación

### Queries de verificación

```sql
-- Reglas activas
SELECT name, enabled, cooldown_minutes, last_triggered_at FROM automation_rules;

-- Log de ejecuciones
SELECT ar.name, al.triggered_at, al.action_result, al.condition_value
FROM automation_log al JOIN automation_rules ar ON al.rule_id = ar.id
ORDER BY al.triggered_at DESC LIMIT 20;
```
