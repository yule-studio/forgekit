from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Sequence


@dataclass(frozen=True)
class DayProfile:
    wake_time: time
    work_start_time: time
    lunch_start_time: time
    work_end_time: time
    commute_minutes: int
    departure_buffer_minutes: int
    home_area: str
    work_area: str
    lunch_duration_minutes: int = 60

    def recommended_departure_at(self, plan_date: date) -> datetime:
        work_start = datetime.combine(plan_date, self.work_start_time)
        return work_start - timedelta(minutes=self.commute_minutes + self.departure_buffer_minutes)

    def briefing_schedule(self, plan_date: date) -> Sequence["DayProfileBriefingSlot"]:
        from yule_orchestrator.core import local_tz

        timezone = local_tz()
        return [
            DayProfileBriefingSlot(
                briefing_type="morning",
                title="아침 브리핑",
                send_at=datetime.combine(plan_date, self.wake_time).replace(tzinfo=timezone),
            ),
            DayProfileBriefingSlot(
                briefing_type="work_start",
                title="업무 시작 브리핑",
                send_at=datetime.combine(plan_date, self.work_start_time).replace(tzinfo=timezone),
            ),
            DayProfileBriefingSlot(
                briefing_type="lunch",
                title="점심 브리핑",
                send_at=datetime.combine(plan_date, self.lunch_start_time).replace(tzinfo=timezone),
            ),
            DayProfileBriefingSlot(
                briefing_type="evening",
                title="퇴근 후 브리핑",
                send_at=datetime.combine(plan_date, self.work_end_time).replace(tzinfo=timezone),
            ),
        ]


@dataclass(frozen=True)
class DayProfileBriefingSlot:
    briefing_type: str
    title: str
    send_at: datetime


def load_day_profile() -> DayProfile:
    return DayProfile(
        wake_time=_time_env("YULE_WAKE_TIME", default=time(hour=6, minute=0)),
        work_start_time=_time_env("YULE_WORK_START_TIME", default=time(hour=9, minute=0)),
        lunch_start_time=_time_env("YULE_LUNCH_START_TIME", default=time(hour=13, minute=0)),
        work_end_time=_time_env("YULE_WORK_END_TIME", default=time(hour=18, minute=0)),
        commute_minutes=_positive_int_env("YULE_COMMUTE_MINUTES", default=45),
        departure_buffer_minutes=_positive_int_env("YULE_DEPARTURE_BUFFER_MINUTES", default=10),
        home_area=_string_env("YULE_HOME_AREA", default="신정동"),
        work_area=_string_env("YULE_WORK_AREA", default="마곡"),
        lunch_duration_minutes=_positive_int_env("YULE_LUNCH_DURATION_MINUTES", default=60),
    )


def _time_env(name: str, *, default: time) -> time:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default

    value = raw.strip()
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError(f"{name} must use HH:MM format, got: {value!r}")

    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise ValueError(f"{name} must use HH:MM format, got: {value!r}") from exc

    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError(f"{name} must use a valid 24-hour time, got: {value!r}")

    return time(hour=hour, minute=minute)


def _positive_int_env(name: str, *, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default

    value = raw.strip()
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer value, got: {value!r}") from exc

    if parsed <= 0:
        raise ValueError(f"{name} must be greater than 0, got: {value!r}")

    return parsed


def _string_env(name: str, *, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def load_work_mode_enabled() -> bool:
    raw = os.environ.get("YULE_WORK_MODE_ENABLED")
    if raw is None:
        return True
    value = raw.strip().lower()
    if not value:
        return True
    return value not in {"false", "0", "no", "off"}
