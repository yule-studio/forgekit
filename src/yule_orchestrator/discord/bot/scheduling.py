"""Scheduling helpers (P0-Q step 5).

Pure scheduling utilities extracted from ``bot/_legacy.py``:

- Daily-preparation tick scheduling (calendar / github / planning snapshot offsets).
- Scheduled-briefing window resolution + notification dedup cache.
- Checkpoint scan rounding + window resolution + notification dedup cache.

Kept stateless (storage helpers reach into the SQLite cache namespaces) so
this module can be imported by both ``_legacy.py`` and any follow-up
responsibility-aligned splits without re-introducing circular wiring.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta

from ...integrations.calendar.models import build_fallback_item_uid
from ...observability import RuntimeStepMetric, save_runtime_metric_run
from ...planning.day_profile import DayProfile, DayProfileBriefingSlot
from ...planning.models import PlanningCheckpoint, PlanningScheduledBriefing
from ...storage import load_json_cache, save_json_cache
from ..config import DiscordBotConfig
from ..planning_runtime import (
    build_due_briefings,
    build_due_checkpoints,
    load_prefetched_due_checkpoints,
)

CHECKPOINT_NOTIFICATION_NAMESPACE = "discord-checkpoint-notifications"
BRIEFING_NOTIFICATION_NAMESPACE = "discord-scheduled-briefings"
CHECKPOINT_NOTIFICATION_TTL_SECONDS = 2 * 24 * 60 * 60
BRIEFING_NOTIFICATION_TTL_SECONDS = 2 * 24 * 60 * 60
DAILY_PREPARATION_CALENDAR_OFFSET_MINUTES = 10
DAILY_PREPARATION_GITHUB_OFFSET_MINUTES = 5
DAILY_PREPARATION_SNAPSHOT_OFFSET_MINUTES = 2


def _next_daily_run(target_time: time | None) -> datetime:
    if target_time is None:
        raise ValueError("daily briefing time is required for scheduling.")
    now = datetime.now().astimezone()
    next_run = now.replace(hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0)
    if next_run <= now:
        next_run = next_run + timedelta(days=1)
    return next_run


def _collect_due_daily_preparation_steps(
    *,
    last_scan: datetime,
    scan_time: datetime,
    day_profile: DayProfile,
    completed_steps: set[tuple[str, str]],
) -> list[tuple[str, date, datetime]]:
    if scan_time <= last_scan:
        return []

    due_steps: list[tuple[str, date, datetime]] = []
    current_date = last_scan.date()
    end_date = scan_time.date()
    while current_date <= end_date:
        for step_name, scheduled_at in _daily_preparation_schedule_for(current_date, day_profile):
            step_key = (current_date.isoformat(), step_name)
            if step_key in completed_steps:
                continue
            if last_scan < scheduled_at <= scan_time:
                due_steps.append((step_name, current_date, scheduled_at))
        current_date = current_date + timedelta(days=1)

    due_steps.sort(key=lambda item: item[2])
    return due_steps


def _daily_preparation_schedule_for(plan_date: date, day_profile: DayProfile) -> list[tuple[str, datetime]]:
    morning_slot = next(slot for slot in day_profile.briefing_schedule(plan_date) if slot.briefing_type == "morning")
    briefing_at = morning_slot.send_at
    return [
        ("calendar_sync", briefing_at - timedelta(minutes=DAILY_PREPARATION_CALENDAR_OFFSET_MINUTES)),
        ("github_sync", briefing_at - timedelta(minutes=DAILY_PREPARATION_GITHUB_OFFSET_MINUTES)),
        ("planning_snapshot", briefing_at - timedelta(minutes=DAILY_PREPARATION_SNAPSHOT_OFFSET_MINUTES)),
    ]


def _cleanup_completed_preparation_steps(
    completed_steps: set[tuple[str, str]],
    *,
    today: date,
) -> None:
    stale_keys = [item for item in completed_steps if item[0] < today.isoformat()]
    for item in stale_keys:
        completed_steps.discard(item)


def _next_daily_preparation_runs(*, now: datetime, day_profile: DayProfile) -> tuple[datetime, datetime, datetime]:
    next_briefing = _next_scheduled_briefing_run(now=now, day_profile=day_profile, briefing_type="morning")

    return (
        next_briefing - timedelta(minutes=DAILY_PREPARATION_CALENDAR_OFFSET_MINUTES),
        next_briefing - timedelta(minutes=DAILY_PREPARATION_GITHUB_OFFSET_MINUTES),
        next_briefing - timedelta(minutes=DAILY_PREPARATION_SNAPSHOT_OFFSET_MINUTES),
    )


def _next_checkpoint_scan(after: datetime | None = None) -> datetime:
    current = after or datetime.now().astimezone()
    rounded = current.replace(second=0, microsecond=0)
    if rounded <= current:
        rounded = rounded + timedelta(minutes=1)
    return rounded


def _checkpoint_channel_error_label(config: DiscordBotConfig) -> str:
    if config.checkpoint_channel_id is not None:
        return "DISCORD_CHECKPOINT_CHANNEL_ID"
    return "DISCORD_DAILY_CHANNEL_ID"


def _next_scheduled_briefing_run(
    *,
    now: datetime,
    day_profile: DayProfile,
    briefing_type: str | None,
) -> datetime:
    upcoming: list[datetime] = []
    for offset in range(0, 3):
        plan_date = now.date() + timedelta(days=offset)
        for slot in day_profile.briefing_schedule(plan_date):
            if briefing_type is not None and slot.briefing_type != briefing_type:
                continue
            if slot.send_at > now:
                upcoming.append(slot.send_at)
    if not upcoming:
        raise ValueError("no upcoming briefing schedule could be computed")
    return min(upcoming)


def _resolve_due_briefings(
    window_start: datetime,
    window_end: datetime,
) -> list[PlanningScheduledBriefing]:
    window_minutes = max(1, math.ceil((window_end - window_start).total_seconds() / 60))
    return build_due_briefings(window_start, window_minutes=window_minutes)


def _collect_due_briefing_slots(
    *,
    last_scan: datetime,
    scan_time: datetime,
    day_profile: DayProfile,
) -> list[tuple[DayProfileBriefingSlot, date]]:
    if scan_time <= last_scan:
        return []

    slots: list[tuple[DayProfileBriefingSlot, date]] = []
    plan_date = last_scan.date()
    end_date = scan_time.date()
    while plan_date <= end_date:
        for slot in day_profile.briefing_schedule(plan_date):
            if last_scan < slot.send_at <= scan_time:
                slots.append((slot, plan_date))
        plan_date += timedelta(days=1)
    slots.sort(key=lambda item: item[0].send_at)
    return slots


def _synthesize_scheduled_briefing(
    slot: DayProfileBriefingSlot,
    plan_date: date,
) -> PlanningScheduledBriefing:
    return PlanningScheduledBriefing(
        briefing_id=build_fallback_item_uid(
            "planning-scheduled-briefing", plan_date.isoformat(), slot.briefing_type
        ),
        briefing_type=slot.briefing_type,
        title=slot.title,
        send_at=slot.send_at.isoformat(),
        content="",
        source="rules",
    )


def _has_briefing_been_sent_async(channel_id: int | None, briefing_id: str) -> bool:
    if channel_id is None:
        return False
    entry = load_json_cache(
        namespace=BRIEFING_NOTIFICATION_NAMESPACE,
        cache_key=_briefing_notification_cache_key(channel_id, briefing_id),
        allow_stale=False,
        touch=False,
    )
    return entry is not None


def _briefing_notification_cache_key(channel_id: int, briefing_id: str) -> str:
    return f"{channel_id}:{briefing_id}"


def _filter_unsent_briefings(
    channel_id: int | None,
    briefings: list[PlanningScheduledBriefing],
) -> list[PlanningScheduledBriefing]:
    if channel_id is None:
        return briefings
    unsent: list[PlanningScheduledBriefing] = []
    for briefing in briefings:
        entry = load_json_cache(
            namespace=BRIEFING_NOTIFICATION_NAMESPACE,
            cache_key=_briefing_notification_cache_key(channel_id, briefing.briefing_id),
            allow_stale=False,
            touch=False,
        )
        if entry is None:
            unsent.append(briefing)
    return unsent


def _mark_briefings_sent(
    channel_id: int | None,
    briefings: list[PlanningScheduledBriefing],
) -> None:
    if channel_id is None:
        return
    for briefing in briefings:
        save_json_cache(
            namespace=BRIEFING_NOTIFICATION_NAMESPACE,
            cache_key=_briefing_notification_cache_key(channel_id, briefing.briefing_id),
            provider="discord-bot",
            range_start=briefing.send_at,
            range_end=briefing.send_at,
            scope_hash=str(channel_id),
            ttl_seconds=BRIEFING_NOTIFICATION_TTL_SECONDS,
            payload={
                "channel_id": channel_id,
                "briefing_id": briefing.briefing_id,
                "briefing_type": briefing.briefing_type,
                "send_at": briefing.send_at,
            },
            metadata={
                "channel_id": channel_id,
                "briefing_type": briefing.briefing_type,
            },
        )


def _save_discord_send_metric(
    *,
    workflow: str,
    started_at: datetime,
    duration_seconds: float,
    ok: bool,
    channel_id: int | None,
    message_count: int,
    snapshot_state: str,
    error: str | None = None,
) -> None:
    ended_at = datetime.now().astimezone()
    step = RuntimeStepMetric(
        name="discord_send",
        duration_seconds=duration_seconds,
        ok=ok,
        started_at=started_at.isoformat(),
        ended_at=ended_at.isoformat(),
        metadata={
            "channel_id": channel_id,
            "message_count": message_count,
            "snapshot_state": snapshot_state,
        },
        error=error,
    )
    save_runtime_metric_run(
        workflow=workflow,
        started_at=started_at,
        ended_at=ended_at,
        steps=[step],
        metadata={
            "channel_id": channel_id,
            "snapshot_state": snapshot_state,
        },
    )


def _checkpoint_window_minutes(window_start: datetime, window_end: datetime) -> int:
    total_seconds = max(0.0, (window_end - window_start).total_seconds())
    return max(1, math.ceil(total_seconds / 60.0))


def _resolve_due_checkpoints(window_start: datetime, window_end: datetime) -> list[PlanningCheckpoint]:
    prefetched_checkpoints, cache_complete = load_prefetched_due_checkpoints(window_start, window_end)
    if cache_complete:
        return prefetched_checkpoints

    return build_due_checkpoints(
        window_start,
        window_minutes=_checkpoint_window_minutes(window_start, window_end),
    )


def _filter_unsent_checkpoints(
    channel_id: int,
    checkpoints: list[PlanningCheckpoint],
) -> list[PlanningCheckpoint]:
    return [
        checkpoint
        for checkpoint in checkpoints
        if not _has_checkpoint_been_sent(channel_id, checkpoint.checkpoint_id)
    ]


def _mark_checkpoints_sent(channel_id: int, checkpoints: list[PlanningCheckpoint]) -> None:
    for checkpoint in checkpoints:
        save_json_cache(
            namespace=CHECKPOINT_NOTIFICATION_NAMESPACE,
            cache_key=_checkpoint_cache_key(channel_id, checkpoint.checkpoint_id),
            provider="discord-bot",
            range_start=None,
            range_end=None,
            scope_hash=str(channel_id),
            ttl_seconds=CHECKPOINT_NOTIFICATION_TTL_SECONDS,
            payload={
                "channel_id": channel_id,
                "checkpoint_id": checkpoint.checkpoint_id,
                "remind_at": checkpoint.remind_at,
            },
            metadata={"kind": checkpoint.kind},
        )


def _has_checkpoint_been_sent(channel_id: int, checkpoint_id: str) -> bool:
    entry = load_json_cache(
        namespace=CHECKPOINT_NOTIFICATION_NAMESPACE,
        cache_key=_checkpoint_cache_key(channel_id, checkpoint_id),
        touch=False,
    )
    return entry is not None


def _checkpoint_cache_key(channel_id: int, checkpoint_id: str) -> str:
    return f"{channel_id}:{checkpoint_id}"
