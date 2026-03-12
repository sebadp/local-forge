# Operational Automation

> **Exec Plan**: [`47-operational_automation_prd.md`](../exec-plans/47-operational_automation_prd.md) / [`47-operational_automation_prp.md`](../exec-plans/47-operational_automation_prp.md)

## Qué hace

Motor ligero de reglas `IF condition THEN action` evaluadas periódicamente por APScheduler. Permite automatizar mantenimiento de la DB, alertas proactivas, y self-healing sin intervención manual.

## Componentes

### `app/automation/models.py`
- `AutomationRule`: dataclass con `from_row()` para deserializar rows de SQLite
- `ActionContext`: contexto runtime para acciones (platform_client, repository, ollama_client, etc.)
- `AutomationLogEntry`: dataclass para entradas del log

### `app/automation/conditions.py`
- `in_cooldown(rule)`: check si la regla está en período de cooldown
- `check_condition(rule, repository)`: dispatcher por `condition_type`
  - **query**: ejecuta SQL readonly (solo SELECT), compara resultado con threshold
  - **metric**: resuelve métricas nombradas (`guardrail_pass_rate`, `embedding_desync`, `db_size_mb`, `unconsolidated_memories`, `project_inactive_days`)
  - **schedule**: match simple de hora/minuto UTC contra expresión cron

### `app/automation/actions.py`
- `execute_action(rule, condition_value, context)`: dispatcher por `action_type`
  - **notify_user**: envía mensaje formateado al usuario vía `PlatformClient`
  - **notify_admin**: igual pero al admin phone
  - **run_task**: ejecuta tarea registrada (`backfill_embeddings`, `consolidate_memories`, `vacuum_db`)
  - **log**: solo logging, sin notificación
- Fail-safe: todas las acciones wrapeadas en try/except — errores logueados, nunca propagados

### `app/automation/evaluator.py`
- `evaluate_rules(repository, ...)`: loop principal que evalúa todas las reglas activas
  1. Fetch reglas activas
  2. Filter por cooldown
  3. Check condición
  4. Ejecutar acción si condición se cumple
  5. Log resultado + update `last_triggered_at`

### `app/automation/builtin_rules.py`
5 reglas built-in seeded idempotentemente al startup:

| Regla | Condición | Acción | Cooldown |
|---|---|---|---|
| `project_inactive` | Proyectos activos sin actividad 7+ días | Notificar usuario | 24h |
| `guardrail_degraded` | Pass rate < 70% (24h) | Notificar admin | 4h |
| `embeddings_desync` | >10 memorias sin embedding | Backfill embeddings | 6h |
| `db_large` | DB > 500 MB | VACUUM automático | 24h |
| `consolidation_pending` | >30 memorias viejas sin consolidar | Consolidar memorias | 12h |

## Integración

- **APScheduler**: job `automation_evaluator` corre cada `automation_interval_minutes` (default 15)
- **Platform routing**: admin_phone con prefijo `tg_` enruta a Telegram, sino WhatsApp
- **Tools**: 3 tools en categoría `"automation"` — `list_automation_rules`, `toggle_automation_rule`, `get_automation_log`
- **Gated**: toda la feature gated por `automation_enabled: bool = False` (opt-in)

## Settings

| Setting | Default | Descripción |
|---|---|---|
| `automation_enabled` | `False` | Habilitar/deshabilitar toda la feature |
| `automation_interval_minutes` | `15` | Intervalo entre evaluaciones de reglas |
| `automation_admin_phone` | `""` | Teléfono del admin para notificaciones |

## Decisiones de diseño

- **Polling vs event-driven**: polling cada 15min es más simple y suficiente para el volumen actual
- **Cooldown por regla**: evita spam de notificaciones — cada regla tiene su propio cooldown
- **Fail-safe everywhere**: errores en condiciones, acciones, o logging nunca crashean el sistema
- **SELECT-only queries**: validación de que condition queries no mutan datos
- **INSERT OR IGNORE**: seed de reglas idempotente — safe para correr en cada startup

## Testing

- **Tests**: `tests/test_automation.py` — 30 tests
- **Guía manual**: [`docs/testing/47-operational_automation_testing.md`](../testing/47-operational_automation_testing.md)
