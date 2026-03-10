from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

# Aliases for Argentine provinces that don't have their own IANA timezone
# but are commonly used by LLMs when interpreting user location.
_TZ_ALIASES: dict[str, str] = {
    "America/Argentina/Misiones": "America/Argentina/Buenos_Aires",
    "America/Argentina/Formosa": "America/Argentina/Buenos_Aires",
    "America/Argentina/Chaco": "America/Argentina/Buenos_Aires",
    "America/Argentina/Entre_Rios": "America/Argentina/Buenos_Aires",
    "America/Argentina/Corrientes": "America/Argentina/Buenos_Aires",
    "America/Argentina/Santa_Fe": "America/Argentina/Cordoba",
    "America/Argentina/Neuquen": "America/Argentina/Salta",
}


def register(registry: SkillRegistry) -> None:
    async def get_current_datetime(timezone: str = "UTC") -> str:
        logger.info(f"get_current_datetime requested for timezone: {timezone}")
        timezone = _TZ_ALIASES.get(timezone, timezone)
        try:
            tz = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, KeyError):
            logger.warning(f"Unknown timezone: {timezone}")
            return f"Unknown timezone: {timezone}"
        now = datetime.now(tz)
        result = now.strftime("%A, %Y-%m-%d %H:%M:%S %Z")
        logger.info(f"Current datetime: {result}")
        return result

    async def convert_timezone(time: str, from_timezone: str, to_timezone: str) -> str:
        logger.info(f"convert_timezone: {time} from {from_timezone} to {to_timezone}")
        from_timezone = _TZ_ALIASES.get(from_timezone, from_timezone)
        to_timezone = _TZ_ALIASES.get(to_timezone, to_timezone)
        try:
            from_tz = ZoneInfo(from_timezone)
        except (ZoneInfoNotFoundError, KeyError):
            logger.warning(f"Unknown source timezone: {from_timezone}")
            return f"Unknown timezone: {from_timezone}"
        try:
            to_tz = ZoneInfo(to_timezone)
        except (ZoneInfoNotFoundError, KeyError):
            logger.warning(f"Unknown target timezone: {to_timezone}")
            return f"Unknown timezone: {to_timezone}"

        for fmt in ("%Y-%m-%d %H:%M:%S", "%H:%M:%S", "%H:%M"):
            try:
                dt = datetime.strptime(time, fmt)
                # Fix year 1900: time-only inputs default to 1900-01-01,
                # use today's date instead for correct DST and date output.
                if fmt in ("%H:%M:%S", "%H:%M"):
                    today = datetime.now(from_tz).date()
                    dt = dt.replace(year=today.year, month=today.month, day=today.day)
                dt = dt.replace(tzinfo=from_tz)
                converted = dt.astimezone(to_tz)
                result = converted.strftime("%Y-%m-%d %H:%M:%S %Z")
                logger.info(f"Converted time: {result}")
                return result
            except ValueError:
                continue

        logger.warning(f"Could not parse time: {time}")
        return f"Could not parse time: {time}"

    registry.register_tool(
        name="get_current_datetime",
        description="Get the current date and time in a given timezone",
        parameters={
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "IANA timezone name (e.g. America/Argentina/Buenos_Aires, UTC, Europe/London)",
                },
            },
            "required": [],
        },
        handler=get_current_datetime,
        skill_name="datetime",
    )

    registry.register_tool(
        name="convert_timezone",
        description="Convert a time from one timezone to another",
        parameters={
            "type": "object",
            "properties": {
                "time": {
                    "type": "string",
                    "description": "Time to convert (e.g. '14:30' or '2024-01-15 14:30:00')",
                },
                "from_timezone": {
                    "type": "string",
                    "description": "Source IANA timezone",
                },
                "to_timezone": {
                    "type": "string",
                    "description": "Target IANA timezone",
                },
            },
            "required": ["time", "from_timezone", "to_timezone"],
        },
        handler=convert_timezone,
        skill_name="datetime",
    )
