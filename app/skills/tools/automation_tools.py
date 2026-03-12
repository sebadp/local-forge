from __future__ import annotations

from typing import TYPE_CHECKING

from app.automation.models import AutomationRule

if TYPE_CHECKING:
    from app.database.repository import Repository
    from app.skills.registry import SkillRegistry


def register(registry: SkillRegistry, repository: Repository) -> None:
    _repo = repository

    async def list_automation_rules(**_: object) -> str:
        rows = await _repo.get_all_automation_rules()
        if not rows:
            return "No automation rules configured."
        lines = []
        for row in rows:
            rule = AutomationRule.from_row(row)
            status = "enabled" if rule.enabled else "disabled"
            last = rule.last_triggered_at or "never"
            lines.append(
                f"- **{rule.name}** [{status}]: {rule.description or 'No description'} "
                f"(cooldown: {rule.cooldown_minutes}min, last: {last})"
            )
        return "\n".join(lines)

    async def toggle_rule(name: str = "", enabled: bool = True, **_: object) -> str:
        updated = await _repo.toggle_automation_rule(name, enabled)
        if not updated:
            return f"Rule '{name}' not found."
        status = "enabled" if enabled else "disabled"
        return f"Rule '{name}' is now {status}."

    async def get_automation_log(rule_name: str = "", limit: int = 20, **_: object) -> str:
        rows = await _repo.get_automation_log(
            rule_name=rule_name if rule_name else None,
            limit=int(limit),
        )
        if not rows:
            return "No automation log entries found."
        lines = []
        for row in rows:
            # id, rule_id, rule_name, triggered_at, condition_value, action_result, details
            lines.append(
                f"- [{row[3]}] **{row[2]}**: {row[5]} — {row[4] or ''} "
                f"{'(' + row[6] + ')' if row[6] else ''}"
            )
        return "\n".join(lines)

    registry.register_tool(
        name="list_automation_rules",
        description="List all automation rules with their status, cooldown, and last trigger time",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=list_automation_rules,
    )

    registry.register_tool(
        name="toggle_automation_rule",
        description="Enable or disable an automation rule by name",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the automation rule"},
                "enabled": {"type": "boolean", "description": "True to enable, False to disable"},
            },
            "required": ["name", "enabled"],
        },
        handler=toggle_rule,
    )

    registry.register_tool(
        name="get_automation_log",
        description="View the execution log of automation rules, optionally filtered by rule name",
        parameters={
            "type": "object",
            "properties": {
                "rule_name": {
                    "type": "string",
                    "description": "Filter by rule name (optional, empty = all)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max entries to return (default 20)",
                },
            },
            "required": [],
        },
        handler=get_automation_log,
    )
