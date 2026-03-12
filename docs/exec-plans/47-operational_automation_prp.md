# PRP: Operational Automation

## Archivos a Modificar

### Nuevos
- `app/automation/__init__.py`: Package init
- `app/automation/models.py`: `AutomationRule`, `ActionContext`, `AutomationLogEntry` dataclasses
- `app/automation/evaluator.py`: `evaluate_rules()` — evalua condiciones y ejecuta acciones
- `app/automation/conditions.py`: `check_condition()` — dispatchers por `condition_type` (query, metric, schedule)
- `app/automation/actions.py`: `execute_action()` — dispatchers por `action_type` (notify_user, notify_admin, run_task, log)
- `app/automation/builtin_rules.py`: Seed de reglas built-in (proyecto inactivo, guardrails degraded, embeddings desync, DB vacuum, consolidacion pendiente)
- `tests/test_automation.py`: Unit tests (30 tests)

### Modificados
- `app/database/db.py`: `AUTOMATION_SCHEMA` (tablas `automation_rules` + `automation_log`)
- `app/database/repository.py`: Metodos CRUD para reglas + log de ejecucion
- `app/config.py`: `automation_enabled: bool = False`, `automation_interval_minutes: int = 15`, `automation_admin_phone: str = ""`
- `app/main.py`: Registrar job APScheduler para `evaluate_rules()`, seed de reglas built-in al startup
- `app/skills/router.py`: Categoria `"automation"` en `TOOL_CATEGORIES` + few-shot examples
- `app/skills/tools/__init__.py`: Registrar automation tools

### Nuevos (tools)
- `app/skills/tools/automation_tools.py`: `list_automation_rules`, `toggle_automation_rule`, `get_automation_log` — gated por `automation_enabled`

## Fases de Implementacion

### Phase 1: Schema + Models
- [x] Agregar `AUTOMATION_SCHEMA` en `db.py` (tablas `automation_rules` + `automation_log` + indice)
- [x] Agregar settings en `config.py`: `automation_enabled`, `automation_interval_minutes`, `automation_admin_phone`
- [x] Crear `app/automation/models.py` — dataclasses: `AutomationRule`, `ActionContext`, `AutomationLogEntry`

### Phase 2: Repository methods
- [x] `get_active_automation_rules()` — `WHERE enabled=1`
- [x] `get_automation_rule(name)` — single rule by name
- [x] `get_all_automation_rules()` — all rules sorted by name
- [x] `toggle_automation_rule(name, enabled)` — UPDATE
- [x] `log_automation(rule_id, condition_value, result, details)`
- [x] `update_rule_last_triggered(rule_id)`
- [x] `get_automation_log(rule_name?, limit)` — JOIN with rule names
- [x] `seed_automation_rule(...)` — `INSERT OR IGNORE` idempotente

### Phase 3: Condition evaluators
- [x] Crear `app/automation/conditions.py`
- [x] `check_condition(rule, repository) -> tuple[bool, str]` — dispatcher
- [x] Condition type `query`: SQL readonly (SELECT only), compara con threshold
- [x] Condition type `metric`: 5 métricas (`guardrail_pass_rate`, `embedding_desync`, `db_size_mb`, `unconsolidated_memories`, `project_inactive_days`)
- [x] Condition type `schedule`: match simple hora/minuto UTC contra cron expression
- [x] `in_cooldown(rule) -> bool`

### Phase 4: Action executors
- [x] Crear `app/automation/actions.py`
- [x] `execute_action(rule, condition_value, context) -> str` — dispatcher
- [x] Action type `notify_user`: `platform_client.send_message()` con template rendering
- [x] Action type `notify_admin`: igual con admin_phone
- [x] Action type `run_task`: `backfill_embeddings`, `consolidate_memories`, `vacuum_db`
- [x] Action type `log`: solo `logger.warning(message)`
- [x] Fail-safe: todas las acciones wrapeadas en try/except

### Phase 5: Evaluator loop + Built-in rules
- [x] Crear `app/automation/evaluator.py` — loop principal con cooldown + condition + action + log
- [x] Crear `app/automation/builtin_rules.py` — `seed_builtin_rules(repository)`:
  - `project_inactive`: notify_user, cooldown 24h
  - `guardrail_degraded`: notify_admin, cooldown 4h
  - `embeddings_desync`: run_task backfill, cooldown 6h
  - `db_large`: run_task vacuum, cooldown 24h
  - `consolidation_pending`: run_task consolidate, cooldown 12h

### Phase 6: Integration con main.py + APScheduler
- [x] Import + inicializar en `main.py` lifespan (gated por `automation_enabled`)
- [x] `seed_builtin_rules(repository)` idempotente al startup
- [x] Registrar job APScheduler interval `automation_interval_minutes`
- [x] Platform routing: admin_phone con prefijo `tg_` → Telegram, sino WhatsApp

### Phase 7: Tools + Router
- [x] Crear `app/skills/tools/automation_tools.py`: 3 tools
- [x] Registrar en `register_builtin_tools()` (gated por `automation_enabled`)
- [x] Agregar categoria `"automation"` en `TOOL_CATEGORIES`
- [x] Agregar few-shot examples al classifier prompt

### Phase 8: Tests
- [x] Test cooldown: never triggered, within, expired (3)
- [x] Test condition query: SELECT valid, non-SELECT rejected, threshold not met (3)
- [x] Test condition metric: embedding_desync, unconsolidated, unknown, project_inactive (4)
- [x] Test condition schedule: matching, non-matching (2)
- [x] Test actions: notify_user, notify_admin no phone, run_task unknown, vacuum, log, fail-safe (6)
- [x] Test evaluator: full loop, cooldown respected (2)
- [x] Test repository CRUD: seed+get, idempotent, toggle, toggle nonexistent, log+retrieve (5)
- [x] Test builtin rules: seed, idempotent (2)
- [x] Test automation tools: list, toggle, log (3)
- [x] 785 passed, lint OK, mypy OK

### Phase 9: Docs
- [x] Crear `docs/features/47-operational_automation.md`
- [x] Crear `docs/testing/47-operational_automation_testing.md`
- [x] Actualizar README indexes (features + testing + exec-plans)
- [x] Actualizar CLAUDE.md con patrones de automation
