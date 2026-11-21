"""Resolve spoken dates using the register's local India calendar."""

import calendar
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

INDIA_TIME = ZoneInfo("Asia/Kolkata")
MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if i}
WEEKDAYS = {name.lower(): i for i, name in enumerate(calendar.day_name)}


def local_today() -> date:
    return datetime.now(INDIA_TIME).date()


def spoken_date(value: str, *, today: date) -> date:
    value = re.sub(r"^(?:on|is)\s+", "", value.strip().lower())
    if value == "today":
        return today
    if value == "tomorrow":
        return today + timedelta(days=1)
    if value == "next week":
        return today + timedelta(weeks=1)
    if value == "next month":
        value = "in 1 month"
    relative = re.fullmatch(r"(?:in|after) (\d{1,3}) (days?|weeks?|months?)", value)
    if relative:
        count, unit = int(relative[1]), relative[2]
        if unit.startswith("day"):
            return today + timedelta(days=count)
        if unit.startswith("week"):
            return today + timedelta(weeks=count)
        month_index = today.month - 1 + count
        year, month = today.year + month_index // 12, month_index % 12 + 1
        return date(year, month, min(today.day, calendar.monthrange(year, month)[1]))
    weekday = re.fullmatch(r"(?:next )?(\w+)", value)
    if weekday and weekday[1] in WEEKDAYS:
        return today + timedelta(days=(WEEKDAYS[weekday[1]] - today.weekday()) % 7 or 7)
    named = re.fullmatch(r"([a-z]+) (\d{1,2})(?:\s*,?\s+(\d{4}))?", value)
    if named and named[1] in MONTHS:
        result = date(int(named[3]) if named[3] else today.year, MONTHS[named[1]], int(named[2]))
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        result = date.fromisoformat(value)
    else:
        raise ValueError("Ambiguous date")
    if result < today:
        raise ValueError("Past date")
    return result
