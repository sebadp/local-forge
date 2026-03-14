from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class AutomationRule:
    id: int
    name: str
    description: str | None
    condition_type: str  # 'query', 'metric', 'schedule'
    condition_config: dict  # parsed JSON
    action_type: str  # 'notify_user', 'notify_admin', 'run_task', 'log'
    action_config: dict  # parsed JSON
    enabled: bool
    cooldown_minutes: int
    last_triggered_at: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: tuple) -> AutomationRule:
        return cls(
            id=row[0],
            name=row[1],
            description=row[2],
            condition_type=row[3],
            condition_config=json.loads(row[4]),
            action_type=row[5],
            action_config=json.loads(row[6]),
            enabled=bool(row[7]),
            cooldown_minutes=row[8],
            last_triggered_at=row[9],
            created_at=row[10],
        )


@dataclass
class AutomationLogEntry:
    id: int
    rule_id: int
    rule_name: str
    triggered_at: str
    condition_value: str | None
    action_result: str  # 'success', 'failed', 'skipped'
    details: str | None


@dataclass
class ActionContext:
    """Runtime context passed to action executors."""

    platform_client: object | None = None
    admin_phone: str = ""
    user_phone: str = ""
    repository: object | None = None
    ollama_client: object | None = None
    embed_model: str = ""
    vec_available: bool = False
    extra: dict = field(default_factory=dict)
