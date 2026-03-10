import re

from app.skills.registry import SkillRegistry
from app.skills.tools.datetime_tools import register


def _make_registry():
    reg = SkillRegistry(skills_dir="/nonexistent")
    register(reg)
    return reg


async def test_get_current_datetime_utc():
    reg = _make_registry()
    from app.skills.models import ToolCall

    result = await reg.execute_tool(
        ToolCall(name="get_current_datetime", arguments={"timezone": "UTC"})
    )
    assert result.success
    assert "UTC" in result.content
    # Should match datetime format, optionally preceded by a weekday name
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", result.content)


async def test_get_current_datetime_default():
    reg = _make_registry()
    from app.skills.models import ToolCall

    result = await reg.execute_tool(ToolCall(name="get_current_datetime", arguments={}))
    assert result.success
    assert "UTC" in result.content


async def test_get_current_datetime_invalid_tz():
    reg = _make_registry()
    from app.skills.models import ToolCall

    result = await reg.execute_tool(
        ToolCall(name="get_current_datetime", arguments={"timezone": "Fake/Place"})
    )
    assert result.success
    assert "Unknown timezone" in result.content


async def test_convert_timezone():
    reg = _make_registry()
    from app.skills.models import ToolCall

    result = await reg.execute_tool(
        ToolCall(
            name="convert_timezone",
            arguments={"time": "14:30", "from_timezone": "UTC", "to_timezone": "America/New_York"},
        )
    )
    assert result.success
    # Should contain a valid time
    assert re.search(r"\d{2}:\d{2}:\d{2}", result.content)


async def test_convert_timezone_invalid_from():
    reg = _make_registry()
    from app.skills.models import ToolCall

    result = await reg.execute_tool(
        ToolCall(
            name="convert_timezone",
            arguments={"time": "14:30", "from_timezone": "Fake/Zone", "to_timezone": "UTC"},
        )
    )
    assert "Unknown timezone" in result.content


async def test_convert_timezone_bad_time_format():
    reg = _make_registry()
    from app.skills.models import ToolCall

    result = await reg.execute_tool(
        ToolCall(
            name="convert_timezone",
            arguments={"time": "not-a-time", "from_timezone": "UTC", "to_timezone": "UTC"},
        )
    )
    assert "Could not parse" in result.content


async def test_timezone_alias_resolved():
    """Argentine province aliases should resolve to valid IANA timezones."""
    reg = _make_registry()
    from app.skills.models import ToolCall

    result = await reg.execute_tool(
        ToolCall(name="get_current_datetime", arguments={"timezone": "America/Argentina/Misiones"})
    )
    assert result.success
    # Should NOT return "Unknown timezone"
    assert "Unknown timezone" not in result.content
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", result.content)


async def test_convert_timezone_today_date():
    """Time-only input should use today's date, not 1900."""
    reg = _make_registry()
    from datetime import datetime

    from app.skills.models import ToolCall

    result = await reg.execute_tool(
        ToolCall(
            name="convert_timezone",
            arguments={
                "time": "11:55",
                "from_timezone": "America/Argentina/Buenos_Aires",
                "to_timezone": "Asia/Tokyo",
            },
        )
    )
    assert result.success
    # Must contain today's year, not 1900
    current_year = str(datetime.now().year)
    assert current_year in result.content
    assert "1900" not in result.content


async def test_convert_timezone_full_datetime_unchanged():
    """Full datetime input should not have its date altered."""
    reg = _make_registry()
    from app.skills.models import ToolCall

    result = await reg.execute_tool(
        ToolCall(
            name="convert_timezone",
            arguments={
                "time": "2026-01-15 14:30:00",
                "from_timezone": "UTC",
                "to_timezone": "America/New_York",
            },
        )
    )
    assert result.success
    assert "2026" in result.content


async def test_convert_timezone_alias_both():
    """Aliases should work for both from and to timezones."""
    reg = _make_registry()
    from app.skills.models import ToolCall

    result = await reg.execute_tool(
        ToolCall(
            name="convert_timezone",
            arguments={
                "time": "11:55",
                "from_timezone": "America/Argentina/Misiones",
                "to_timezone": "Asia/Tokyo",
            },
        )
    )
    assert result.success
    assert "Unknown timezone" not in result.content
