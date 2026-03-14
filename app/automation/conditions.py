from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.automation.models import AutomationRule
    from app.database.repository import Repository

logger = logging.getLogger(__name__)

_OPERATORS = {
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
}


def in_cooldown(rule: AutomationRule) -> bool:
    if not rule.last_triggered_at:
        return False
    try:
        last = datetime.fromisoformat(rule.last_triggered_at)
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        elapsed_minutes = (now - last).total_seconds() / 60
        return elapsed_minutes < rule.cooldown_minutes
    except (ValueError, TypeError):
        return False


async def check_condition(rule: AutomationRule, repository: Repository) -> tuple[bool, str]:
    """Evaluate a rule's condition. Returns (met, value_description)."""
    try:
        if rule.condition_type == "query":
            return await _check_query(rule, repository)
        elif rule.condition_type == "metric":
            return await _check_metric(rule, repository)
        elif rule.condition_type == "schedule":
            return _check_schedule(rule)
        else:
            logger.warning("Unknown condition type: %s", rule.condition_type)
            return False, "unknown_type"
    except Exception:
        logger.exception("Condition check failed for rule %s", rule.name)
        return False, "error"


async def _check_query(rule: AutomationRule, repository: Repository) -> tuple[bool, str]:
    cfg = rule.condition_config
    sql = cfg.get("sql", "")
    # Security: only allow safe SELECT queries
    upper_sql = sql.strip().upper()
    if not upper_sql.startswith("SELECT"):
        logger.warning("Rejected non-SELECT query in rule %s: %s", rule.name, sql[:80])
        return False, "rejected_non_select"
    _FORBIDDEN_WORDS = ["ATTACH", "PRAGMA", "LOAD_EXTENSION", "INTO"]
    for token in _FORBIDDEN_WORDS:
        if re.search(rf"\b{token}\b", upper_sql):
            logger.warning("Rejected unsafe SQL token '%s' in rule %s", token, rule.name)
            return False, f"rejected_unsafe:{token}"
    if ";" in upper_sql:
        logger.warning("Rejected unsafe SQL token ';' in rule %s", rule.name)
        return False, "rejected_unsafe:;"

    cursor = await repository.conn.execute(sql)
    row = await cursor.fetchone()
    if row is None:
        return False, "no_rows"

    value = float(row[0])
    threshold = float(cfg.get("threshold", 0))
    operator = cfg.get("operator", ">")
    op_fn = _OPERATORS.get(operator)
    if op_fn is None:
        logger.warning("Unknown operator %s in rule %s", operator, rule.name)
        return False, f"unknown_operator:{operator}"

    met = op_fn(value, threshold)
    return met, f"{value} {operator} {threshold}"


async def _check_metric(rule: AutomationRule, repository: Repository) -> tuple[bool, str]:
    cfg = rule.condition_config
    metric = cfg.get("metric", "")
    threshold = float(cfg.get("threshold", 0))
    operator = cfg.get("operator", ">")
    window_hours = int(cfg.get("window_hours", 24))
    op_fn = _OPERATORS.get(operator)
    if op_fn is None:
        return False, f"unknown_operator:{operator}"

    value = await _resolve_metric(metric, repository, window_hours)
    if value is None:
        return False, f"metric_unavailable:{metric}"

    met = op_fn(value, threshold)
    return met, f"{metric}={value} {operator} {threshold}"


async def _resolve_metric(metric: str, repository: Repository, window_hours: int) -> float | None:
    """Resolve a named metric to a numeric value."""
    if metric == "guardrail_pass_rate":
        try:
            dist = await repository.get_score_distribution()
            for item in dist:
                if item.get("name") == "guardrail_pass":
                    total = item.get("n", 0)
                    avg = item.get("avg", 1.0)
                    if total > 0:
                        return round(avg, 3)
            return 1.0  # No data = assume OK
        except Exception:
            return None

    elif metric == "embedding_desync":
        try:
            cursor = await repository.conn.execute("SELECT COUNT(*) FROM memories WHERE active = 1")
            total_memories = (await cursor.fetchone())[0]  # type: ignore[index]
            try:
                cursor = await repository.conn.execute("SELECT COUNT(*) FROM vec_memories")
                embedded = (await cursor.fetchone())[0]  # type: ignore[index]
            except Exception:
                return None  # vec not available
            return float(max(0, total_memories - embedded))
        except Exception:
            return None

    elif metric == "db_size_mb":
        try:
            cursor = await repository.conn.execute(
                "SELECT page_count * page_size / 1048576.0 FROM pragma_page_count(), pragma_page_size()"
            )
            row = await cursor.fetchone()
            return float(row[0]) if row else None  # type: ignore[index]
        except Exception:
            return None

    elif metric == "unconsolidated_memories":
        try:
            days = window_hours // 24 or 7
            cursor = await repository.conn.execute(
                "SELECT COUNT(*) FROM memories WHERE active = 1 "
                "AND created_at < datetime('now', ? || ' days')",
                (f"-{days}",),
            )
            row = await cursor.fetchone()
            return float(row[0]) if row else 0.0  # type: ignore[index]
        except Exception:
            return None

    elif metric == "project_inactive_days":
        try:
            threshold_days = window_hours // 24 or 7
            cursor = await repository.conn.execute(
                "SELECT COUNT(*) FROM projects p "
                "WHERE p.status = 'active' AND NOT EXISTS ("
                "  SELECT 1 FROM project_activity pa "
                "  WHERE pa.project_id = p.id "
                "  AND pa.created_at >= datetime('now', ? || ' days')"
                ")",
                (f"-{threshold_days}",),
            )
            row = await cursor.fetchone()
            return float(row[0]) if row else 0.0  # type: ignore[index]
        except Exception:
            return None

    else:
        logger.warning("Unknown metric: %s", metric)
        return None


def _check_schedule(rule: AutomationRule) -> tuple[bool, str]:
    """Simple cron-like schedule check. Matches hour and minute of current UTC time."""
    cfg = rule.condition_config
    cron_expr = cfg.get("cron", "")
    if not cron_expr:
        return False, "empty_cron"

    try:
        parts = cron_expr.split()
        if len(parts) < 5:
            return False, "invalid_cron"

        now = datetime.now(UTC)
        minute, hour, dom, month, dow = parts[0], parts[1], parts[2], parts[3], parts[4]

        minute_match = minute == "*" or int(minute) == now.minute
        hour_match = hour == "*" or int(hour) == now.hour
        dom_match = dom == "*" or int(dom) == now.day
        month_match = month == "*" or int(month) == now.month
        # dow: 0=Monday in Python isoweekday()-1, cron uses 0=Sunday
        dow_match = dow == "*" or int(dow) == now.isoweekday() % 7

        met = minute_match and hour_match and dom_match and month_match and dow_match
        return met, f"cron={cron_expr} now={now.hour:02d}:{now.minute:02d}"
    except (ValueError, IndexError):
        return False, f"parse_error:{cron_expr}"
