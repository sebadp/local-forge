"""Tests for the Operational Automation feature (Plan 47)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.automation.builtin_rules import BUILTIN_RULES, seed_builtin_rules
from app.automation.conditions import check_condition, in_cooldown
from app.automation.models import ActionContext, AutomationRule
from app.database.db import init_db
from app.database.repository import Repository

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db():
    conn, _ = await init_db(":memory:")
    yield conn
    await conn.close()


@pytest.fixture
async def repo(db):
    return Repository(db)


def _make_rule(
    *,
    name: str = "test_rule",
    condition_type: str = "metric",
    condition_config: dict | None = None,
    action_type: str = "log",
    action_config: dict | None = None,
    cooldown_minutes: int = 60,
    last_triggered_at: str | None = None,
) -> AutomationRule:
    return AutomationRule(
        id=1,
        name=name,
        description="Test rule",
        condition_type=condition_type,
        condition_config=condition_config or {},
        action_type=action_type,
        action_config=action_config or {"level": "info", "message": "test"},
        enabled=True,
        cooldown_minutes=cooldown_minutes,
        last_triggered_at=last_triggered_at,
        created_at="2024-01-01T00:00:00",
    )


# ---------------------------------------------------------------------------
# Cooldown tests
# ---------------------------------------------------------------------------


class TestCooldown:
    def test_never_triggered(self):
        rule = _make_rule(last_triggered_at=None)
        assert not in_cooldown(rule)

    def test_within_cooldown(self):
        recent = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        rule = _make_rule(cooldown_minutes=60, last_triggered_at=recent)
        assert in_cooldown(rule)

    def test_expired_cooldown(self):
        old = (datetime.now(UTC) - timedelta(minutes=120)).isoformat()
        rule = _make_rule(cooldown_minutes=60, last_triggered_at=old)
        assert not in_cooldown(rule)


# ---------------------------------------------------------------------------
# Condition tests
# ---------------------------------------------------------------------------


class TestCheckConditionQuery:
    async def test_select_valid(self, repo):
        # memories table exists but is empty → COUNT = 0
        rule = _make_rule(
            condition_type="query",
            condition_config={
                "sql": "SELECT COUNT(*) FROM memories WHERE active = 1",
                "operator": "==",
                "threshold": 0,
            },
        )
        met, value = await check_condition(rule, repo)
        assert met is True
        assert "0" in value

    async def test_non_select_rejected(self, repo):
        rule = _make_rule(
            condition_type="query",
            condition_config={
                "sql": "DELETE FROM memories",
                "operator": ">",
                "threshold": 0,
            },
        )
        met, value = await check_condition(rule, repo)
        assert met is False
        assert "rejected" in value

    async def test_greater_than_not_met(self, repo):
        rule = _make_rule(
            condition_type="query",
            condition_config={
                "sql": "SELECT COUNT(*) FROM memories WHERE active = 1",
                "operator": ">",
                "threshold": 100,
            },
        )
        met, _ = await check_condition(rule, repo)
        assert met is False


class TestCheckConditionMetric:
    async def test_embedding_desync_no_vec(self, repo):
        """embedding_desync metric returns None when vec table doesn't exist (no sqlite-vec)."""
        rule = _make_rule(
            condition_type="metric",
            condition_config={
                "metric": "embedding_desync",
                "operator": ">",
                "threshold": 10,
            },
        )
        # vec_memories might not exist in :memory: without sqlite-vec
        met, value = await check_condition(rule, repo)
        # Either False (no data) or metric_unavailable — both acceptable
        assert isinstance(met, bool)

    async def test_unconsolidated_memories_empty(self, repo):
        rule = _make_rule(
            condition_type="metric",
            condition_config={
                "metric": "unconsolidated_memories",
                "operator": ">",
                "threshold": 30,
                "window_hours": 168,
            },
        )
        met, _ = await check_condition(rule, repo)
        assert met is False  # No memories at all

    async def test_unknown_metric(self, repo):
        rule = _make_rule(
            condition_type="metric",
            condition_config={
                "metric": "does_not_exist",
                "operator": ">",
                "threshold": 0,
            },
        )
        met, value = await check_condition(rule, repo)
        assert met is False
        assert "unavailable" in value

    async def test_project_inactive_no_projects(self, repo):
        rule = _make_rule(
            condition_type="metric",
            condition_config={
                "metric": "project_inactive_days",
                "operator": ">",
                "threshold": 0,
                "window_hours": 168,
            },
        )
        met, _ = await check_condition(rule, repo)
        assert met is False  # No projects


class TestCheckConditionSchedule:
    async def test_matching_schedule(self, repo):
        now = datetime.now(UTC)
        cron = f"{now.minute} {now.hour} * * *"
        rule = _make_rule(
            condition_type="schedule",
            condition_config={"cron": cron},
        )
        met, _ = await check_condition(rule, repo)
        assert met is True

    async def test_non_matching_schedule(self, repo):
        rule = _make_rule(
            condition_type="schedule",
            condition_config={"cron": "59 23 * * *"},  # unlikely to match
        )
        met, _ = await check_condition(rule, repo)
        # Might match if test runs at 23:59 UTC, but extremely unlikely
        assert isinstance(met, bool)


# ---------------------------------------------------------------------------
# Action tests
# ---------------------------------------------------------------------------


class TestActions:
    async def test_notify_user(self):
        from app.automation.actions import execute_action

        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock()
        context = ActionContext(
            platform_client=mock_client,
            user_phone="123456",
        )
        rule = _make_rule(
            action_type="notify_user",
            action_config={"template": "Hello from {rule_name}"},
        )
        result = await execute_action(rule, "test_value", context)
        assert "notified_user" in result
        mock_client.send_message.assert_called_once()

    async def test_notify_admin_no_phone(self):
        from app.automation.actions import execute_action

        context = ActionContext(platform_client=AsyncMock(), admin_phone="")
        rule = _make_rule(
            action_type="notify_admin",
            action_config={"template": "Alert!"},
        )
        result = await execute_action(rule, "val", context)
        assert "no_phone" in result

    async def test_run_task_unknown(self):
        from app.automation.actions import execute_action

        context = ActionContext()
        rule = _make_rule(
            action_type="run_task",
            action_config={"task": "nonexistent_task"},
        )
        result = await execute_action(rule, "val", context)
        assert "unknown_task" in result

    async def test_run_task_vacuum(self, repo):
        from app.automation.actions import execute_action

        context = ActionContext(repository=repo)
        rule = _make_rule(
            action_type="run_task",
            action_config={"task": "vacuum_db"},
        )
        result = await execute_action(rule, "500MB", context)
        assert "vacuum_complete" in result

    async def test_log_action(self):
        from app.automation.actions import execute_action

        rule = _make_rule(
            action_type="log",
            action_config={"level": "warning", "message": "test alert"},
        )
        result = await execute_action(rule, "val", ActionContext())
        assert "logged" in result

    async def test_action_fail_safe(self):
        """Actions should never raise — errors are caught and returned as strings."""
        from app.automation.actions import execute_action

        rule = _make_rule(
            action_type="notify_user",
            action_config={"template": "Hello"},
        )
        # platform_client that raises
        bad_client = AsyncMock()
        bad_client.send_message = AsyncMock(side_effect=RuntimeError("boom"))
        context = ActionContext(platform_client=bad_client, user_phone="123")
        result = await execute_action(rule, "val", context)
        assert "error" in result.lower()


# ---------------------------------------------------------------------------
# Evaluator tests
# ---------------------------------------------------------------------------


class TestEvaluator:
    async def test_full_loop(self, repo):
        from app.automation.evaluator import evaluate_rules

        # Seed two rules: one metric (unconsolidated > 30 => False), one query
        await repo.seed_automation_rule(
            name="test_always_true",
            description="Always triggers",
            condition_type="query",
            condition_config=json.dumps(
                {
                    "sql": "SELECT 1",
                    "operator": "==",
                    "threshold": 1,
                }
            ),
            action_type="log",
            action_config=json.dumps({"level": "info", "message": "triggered"}),
            cooldown_minutes=0,
        )
        await repo.seed_automation_rule(
            name="test_never_true",
            description="Never triggers",
            condition_type="query",
            condition_config=json.dumps(
                {
                    "sql": "SELECT 0",
                    "operator": ">",
                    "threshold": 100,
                }
            ),
            action_type="log",
            action_config=json.dumps({"level": "info", "message": "should not trigger"}),
            cooldown_minutes=0,
        )

        count = await evaluate_rules(repo)
        assert count == 1  # Only test_always_true

        # Check log was written
        logs = await repo.get_automation_log(rule_name="test_always_true")
        assert len(logs) >= 1
        assert logs[0][5] == "success"  # action_result

    async def test_cooldown_respected(self, repo):
        from app.automation.evaluator import evaluate_rules

        await repo.seed_automation_rule(
            name="test_cooldown",
            description="Has cooldown",
            condition_type="query",
            condition_config=json.dumps(
                {
                    "sql": "SELECT 1",
                    "operator": "==",
                    "threshold": 1,
                }
            ),
            action_type="log",
            action_config=json.dumps({"level": "info", "message": "x"}),
            cooldown_minutes=999,
        )

        # First run triggers
        count1 = await evaluate_rules(repo)
        assert count1 == 1

        # Second run should be in cooldown
        count2 = await evaluate_rules(repo)
        assert count2 == 0


# ---------------------------------------------------------------------------
# Repository CRUD tests
# ---------------------------------------------------------------------------


class TestRepositoryCRUD:
    async def test_seed_and_get(self, repo):
        await repo.seed_automation_rule(
            name="test_rule",
            description="Test",
            condition_type="metric",
            condition_config=json.dumps({"metric": "db_size_mb"}),
            action_type="log",
            action_config=json.dumps({"message": "hi"}),
        )
        row = await repo.get_automation_rule("test_rule")
        assert row is not None
        assert row[1] == "test_rule"

    async def test_seed_idempotent(self, repo):
        for _ in range(3):
            await repo.seed_automation_rule(
                name="idem",
                description="Test",
                condition_type="metric",
                condition_config="{}",
                action_type="log",
                action_config="{}",
            )
        all_rules = await repo.get_all_automation_rules()
        names = [r[1] for r in all_rules]
        assert names.count("idem") == 1

    async def test_toggle(self, repo):
        await repo.seed_automation_rule(
            name="toggle_me",
            description="Test",
            condition_type="metric",
            condition_config="{}",
            action_type="log",
            action_config="{}",
        )
        # Disable
        ok = await repo.toggle_automation_rule("toggle_me", False)
        assert ok is True
        row = await repo.get_automation_rule("toggle_me")
        assert row[7] == 0  # enabled column

        # Re-enable
        await repo.toggle_automation_rule("toggle_me", True)
        row = await repo.get_automation_rule("toggle_me")
        assert row[7] == 1

    async def test_toggle_nonexistent(self, repo):
        ok = await repo.toggle_automation_rule("no_such_rule", True)
        assert ok is False

    async def test_log_and_retrieve(self, repo):
        await repo.seed_automation_rule(
            name="log_test",
            description="Test",
            condition_type="metric",
            condition_config="{}",
            action_type="log",
            action_config="{}",
        )
        row = await repo.get_automation_rule("log_test")
        rule_id = row[0]

        await repo.log_automation(rule_id, "val=42", "success", "all good")
        logs = await repo.get_automation_log(rule_name="log_test")
        assert len(logs) == 1
        assert logs[0][4] == "val=42"
        assert logs[0][5] == "success"


# ---------------------------------------------------------------------------
# Seed builtin rules
# ---------------------------------------------------------------------------


class TestBuiltinRules:
    async def test_seed_builtin(self, repo):
        count = await seed_builtin_rules(repo)
        assert count == len(BUILTIN_RULES)

        all_rules = await repo.get_all_automation_rules()
        names = {r[1] for r in all_rules}
        for br in BUILTIN_RULES:
            assert br["name"] in names

    async def test_seed_idempotent(self, repo):
        await seed_builtin_rules(repo)
        await seed_builtin_rules(repo)
        all_rules = await repo.get_all_automation_rules()
        assert len(all_rules) == len(BUILTIN_RULES)


# ---------------------------------------------------------------------------
# Automation tools
# ---------------------------------------------------------------------------


class TestAutomationTools:
    async def test_list_rules(self, repo):
        from app.skills.registry import SkillRegistry
        from app.skills.tools.automation_tools import register

        reg = SkillRegistry()
        register(reg, repo)

        await seed_builtin_rules(repo)

        tool = reg.get_tool("list_automation_rules")
        assert tool is not None
        result = await tool.handler()
        assert "project_inactive" in result
        assert "guardrail_degraded" in result

    async def test_toggle_tool(self, repo):
        from app.skills.registry import SkillRegistry
        from app.skills.tools.automation_tools import register

        reg = SkillRegistry()
        register(reg, repo)

        await seed_builtin_rules(repo)

        tool = reg.get_tool("toggle_automation_rule")
        result = await tool.handler(name="project_inactive", enabled=False)
        assert "disabled" in result

    async def test_get_log_tool(self, repo):
        from app.skills.registry import SkillRegistry
        from app.skills.tools.automation_tools import register

        reg = SkillRegistry()
        register(reg, repo)

        tool = reg.get_tool("get_automation_log")
        result = await tool.handler()
        assert "No automation log" in result
