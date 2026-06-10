from __future__ import annotations

from datetime import date, datetime, timedelta

from yule_planning.models import PlanningCheckpoint, PlanningScheduledBriefing
from yule_planning.snapshots import DailyPlanSnapshot, load_daily_plan_snapshot
from yule_storage import load_json_cache, save_json_cache

CHECKPOINT_SNAPSHOT_NAMESPACE = "planning-checkpoint-snapshots"
CHECKPOINT_SNAPSHOT_PROVIDER = "discord-bot"
CHECKPOINT_SNAPSHOT_TTL_SECONDS = 2 * 60 * 60


def load_plan_today_snapshot(plan_date: date) -> DailyPlanSnapshot | None:
    return load_daily_plan_snapshot(plan_date, allow_stale=True)


def build_daily_checkpoints_for_date(plan_date: date) -> list[PlanningCheckpoint]:
    snapshot = load_daily_plan_snapshot(plan_date, allow_stale=True)
    if snapshot is None:
        return []
    return list(snapshot.envelope.daily_plan.checkpoints)


def build_daily_briefings_for_date(plan_date: date) -> list[PlanningScheduledBriefing]:
    snapshot = load_daily_plan_snapshot(plan_date, allow_stale=True)
    if snapshot is None:
        return []
    return list(snapshot.envelope.daily_plan.briefings)


def build_due_checkpoints(
    window_start: datetime,
    *,
    window_minutes: int,
) -> list[PlanningCheckpoint]:
    if window_minutes <= 0:
        return []

    window_end = window_start + timedelta(minutes=window_minutes)
    checkpoints: dict[str, PlanningCheckpoint] = {}
    plan_date = window_start.date()

    while plan_date <= window_end.date():
        snapshot = load_daily_plan_snapshot(plan_date, allow_stale=True)
        if snapshot is None:
            plan_date += timedelta(days=1)
            continue

        for checkpoint in snapshot.envelope.daily_plan.checkpoints:
            remind_at = datetime.fromisoformat(checkpoint.remind_at)
            if window_start <= remind_at <= window_end:
                checkpoints[checkpoint.checkpoint_id] = checkpoint
        plan_date += timedelta(days=1)

    return sorted(checkpoints.values(), key=lambda checkpoint: checkpoint.remind_at)


def build_due_briefings(
    window_start: datetime,
    *,
    window_minutes: int,
) -> list[PlanningScheduledBriefing]:
    if window_minutes <= 0:
        return []

    window_end = window_start + timedelta(minutes=window_minutes)
    briefings: dict[str, PlanningScheduledBriefing] = {}
    plan_date = window_start.date()

    while plan_date <= window_end.date():
        snapshot = load_daily_plan_snapshot(plan_date, allow_stale=True)
        if snapshot is None:
            plan_date += timedelta(days=1)
            continue

        for briefing in snapshot.envelope.daily_plan.briefings:
            send_at = datetime.fromisoformat(briefing.send_at)
            if window_start <= send_at <= window_end:
                briefings[briefing.briefing_id] = briefing
        plan_date += timedelta(days=1)

    return sorted(briefings.values(), key=lambda briefing: briefing.send_at)


def prefetch_checkpoint_snapshots(
    reference_time: datetime,
    *,
    prefetch_minutes: int,
) -> dict[str, int]:
    if prefetch_minutes <= 0:
        return {"saved_dates": 0, "checkpoint_count": 0}

    covered_dates = _iter_date_range(
        reference_time.date(),
        (reference_time + timedelta(minutes=prefetch_minutes)).date(),
    )
    saved_dates = 0
    checkpoint_count = 0

    for plan_date in covered_dates:
        checkpoints = build_daily_checkpoints_for_date(plan_date)
        save_json_cache(
            namespace=CHECKPOINT_SNAPSHOT_NAMESPACE,
            cache_key=_checkpoint_snapshot_cache_key(plan_date),
            provider=CHECKPOINT_SNAPSHOT_PROVIDER,
            range_start=plan_date.isoformat(),
            range_end=plan_date.isoformat(),
            scope_hash=plan_date.isoformat(),
            ttl_seconds=CHECKPOINT_SNAPSHOT_TTL_SECONDS,
            payload={
                "plan_date": plan_date.isoformat(),
                "generated_at": reference_time.isoformat(),
                "checkpoints": [checkpoint.to_dict() for checkpoint in checkpoints],
            },
            metadata={
                "checkpoint_count": len(checkpoints),
                "prefetch_minutes": prefetch_minutes,
            },
        )
        saved_dates += 1
        checkpoint_count += len(checkpoints)

    return {"saved_dates": saved_dates, "checkpoint_count": checkpoint_count}


def load_prefetched_due_checkpoints(
    window_start: datetime,
    window_end: datetime,
) -> tuple[list[PlanningCheckpoint], bool]:
    due_checkpoints: dict[str, PlanningCheckpoint] = {}
    all_dates_available = True

    for plan_date in _iter_date_range(window_start.date(), window_end.date()):
        entry = load_json_cache(
            namespace=CHECKPOINT_SNAPSHOT_NAMESPACE,
            cache_key=_checkpoint_snapshot_cache_key(plan_date),
            allow_stale=True,
            touch=False,
        )
        if entry is None:
            all_dates_available = False
            continue

        payload_checkpoints = entry.payload.get("checkpoints", [])
        if not isinstance(payload_checkpoints, list):
            all_dates_available = False
            continue

        for raw_checkpoint in payload_checkpoints:
            if not isinstance(raw_checkpoint, dict):
                all_dates_available = False
                continue
            checkpoint = PlanningCheckpoint.from_dict(raw_checkpoint)
            remind_at = datetime.fromisoformat(checkpoint.remind_at)
            if window_start <= remind_at <= window_end:
                due_checkpoints[checkpoint.checkpoint_id] = checkpoint

    return (
        sorted(due_checkpoints.values(), key=lambda checkpoint: checkpoint.remind_at),
        all_dates_available,
    )


def _iter_date_range(start_date: date, end_date: date) -> list[date]:
    dates: list[date] = []
    current = start_date
    while current <= end_date:
        dates.append(current)
        current += timedelta(days=1)
    return dates


def _checkpoint_snapshot_cache_key(plan_date: date) -> str:
    return plan_date.isoformat()
