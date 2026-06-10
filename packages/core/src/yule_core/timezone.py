from __future__ import annotations

import os
from datetime import datetime, tzinfo
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

YULE_TIMEZONE_ENV = "YULE_TIMEZONE"


def local_tz() -> tzinfo:
    name = os.environ.get(YULE_TIMEZONE_ENV, "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"{YULE_TIMEZONE_ENV} must be a valid IANA zone name, got: {name!r}"
            ) from exc

    system_tz = datetime.now().astimezone().tzinfo
    if system_tz is None:
        return ZoneInfo("UTC")
    return system_tz


def local_tz_name() -> str:
    name = os.environ.get(YULE_TIMEZONE_ENV, "").strip()
    if name:
        return name

    system_name = datetime.now().astimezone().tzname()
    return system_name or "local"


def now_local() -> datetime:
    return datetime.now(tz=local_tz())


def to_local(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=local_tz())
    return value.astimezone(local_tz())
