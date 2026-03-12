from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.database.repository import Repository

logger = logging.getLogger(__name__)

BUILTIN_RULES = [
    {
        "name": "project_inactive",
        "description": "Alert when active projects have no activity for 7+ days",
        "condition_type": "metric",
        "condition_config": json.dumps(
            {
                "metric": "project_inactive_days",
                "operator": ">",
                "threshold": 0,
                "window_hours": 168,  # 7 days
            }
        ),
        "action_type": "notify_user",
        "action_config": json.dumps(
            {
                "template": "You have inactive projects with no activity in the last 7 days. "
                "Check your project list to see if you want to resume or archive them.",
            }
        ),
        "cooldown_minutes": 1440,  # 24h
    },
    {
        "name": "guardrail_degraded",
        "description": "Alert admin when guardrail pass rate drops below 70%",
        "condition_type": "metric",
        "condition_config": json.dumps(
            {
                "metric": "guardrail_pass_rate",
                "operator": "<",
                "threshold": 0.7,
                "window_hours": 24,
            }
        ),
        "action_type": "notify_admin",
        "action_config": json.dumps(
            {
                "template": "Guardrail pass rate is degraded: {value}. Check recent failures.",
            }
        ),
        "cooldown_minutes": 240,  # 4h
    },
    {
        "name": "embeddings_desync",
        "description": "Auto re-index when >10 memories lack embeddings",
        "condition_type": "metric",
        "condition_config": json.dumps(
            {
                "metric": "embedding_desync",
                "operator": ">",
                "threshold": 10,
                "window_hours": 24,
            }
        ),
        "action_type": "run_task",
        "action_config": json.dumps(
            {
                "task": "backfill_embeddings",
            }
        ),
        "cooldown_minutes": 360,  # 6h
    },
    {
        "name": "db_large",
        "description": "Auto VACUUM when database exceeds 500 MB",
        "condition_type": "metric",
        "condition_config": json.dumps(
            {
                "metric": "db_size_mb",
                "operator": ">",
                "threshold": 500,
                "window_hours": 24,
            }
        ),
        "action_type": "run_task",
        "action_config": json.dumps(
            {
                "task": "vacuum_db",
            }
        ),
        "cooldown_minutes": 1440,  # 24h
    },
    {
        "name": "consolidation_pending",
        "description": "Auto-consolidate when >30 old memories exist",
        "condition_type": "metric",
        "condition_config": json.dumps(
            {
                "metric": "unconsolidated_memories",
                "operator": ">",
                "threshold": 30,
                "window_hours": 168,  # 7 days
            }
        ),
        "action_type": "run_task",
        "action_config": json.dumps(
            {
                "task": "consolidate_memories",
            }
        ),
        "cooldown_minutes": 720,  # 12h
    },
]


async def seed_builtin_rules(repository: Repository) -> int:
    """Seed built-in automation rules. Returns count of newly inserted rules."""
    inserted = 0
    for rule in BUILTIN_RULES:
        try:
            await repository.seed_automation_rule(
                name=str(rule["name"]),
                description=str(rule["description"]),
                condition_type=str(rule["condition_type"]),
                condition_config=str(rule["condition_config"]),
                action_type=str(rule["action_type"]),
                action_config=str(rule["action_config"]),
                cooldown_minutes=int(str(rule["cooldown_minutes"])),
            )
            inserted += 1
        except Exception:
            logger.exception("Failed to seed rule: %s", rule["name"])
    return inserted
