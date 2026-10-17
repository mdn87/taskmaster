"""UTC RFC 3339 timestamps with second precision and a literal Z."""

from __future__ import annotations

import re
from datetime import datetime, timezone

_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")


class TimeFormatError(ValueError):
    pass


def parse(value: object) -> datetime:
    if not isinstance(value, str) or not _PATTERN.match(value):
        raise TimeFormatError("expected YYYY-MM-DDTHH:MM:SSZ")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise TimeFormatError("invalid calendar time") from None


def is_valid(value: object) -> bool:
    try:
        parse(value)
    except TimeFormatError:
        return False
    return True


def fmt(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise TimeFormatError("naive datetime")
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now() -> str:
    return fmt(datetime.now(timezone.utc).replace(microsecond=0))


def seconds_between(earlier: str, later: str) -> int:
    return int((parse(later) - parse(earlier)).total_seconds())
