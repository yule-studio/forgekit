from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from yule_orchestrator.storage import load_json_cache, save_json_cache
from .models import DailyPlanEnvelope

DAILY_PLAN_SNAPSHOT_NAMESPACE = "planning-daily-plan-snapshots"
DAILY_PLAN_SNAPSHOT_PROVIDER = "planning-agent"
DEFAULT_DAILY_PLAN_SNAPSHOT_SECONDS = 6 * 60 * 60


@dataclass(frozen=True)
class DailyPlanSnapshot:
    plan_date: date
    generated_at: datetime
    envelope: DailyPlanEnvelope
    is_stale: bool
    cache_key: str
    metadata: dict

    def to_dict(self) -> dict:
        return {
            "plan_date": self.plan_date.isoformat(),
            "generated_at": self.generated_at.isoformat(),
            "is_stale": self.is_stale,
            "cache_key": self.cache_key,
            "metadata": self.metadata,
            "envelope": self.envelope.to_dict(),
        }


def save_daily_plan_snapshot(
    envelope: DailyPlanEnvelope,
    *,
    generated_at: Optional[datetime] = None,
    ttl_seconds: Optional[int] = None,
) -> DailyPlanSnapshot:
    plan_date = envelope.daily_plan.plan_date
    generated_at = generated_at or datetime.now().astimezone()
    ttl_seconds = _snapshot_ttl_seconds() if ttl_seconds is None else ttl_seconds
    cache_key = daily_plan_snapshot_cache_key(plan_date)
    metadata = {
        "plan_date": plan_date.isoformat(),
        "generated_at": generated_at.isoformat(),
        "fixed_event_count": envelope.daily_plan.summary.fixed_event_count,
        "todo_count": envelope.daily_plan.summary.todo_count,
        "github_issue_count": envelope.daily_plan.summary.github_issue_count,
        "recommended_task_count": envelope.daily_plan.summary.recommended_task_count,
    }
    payload = {
        "plan_date": plan_date.isoformat(),
        "generated_at": generated_at.isoformat(),
        "envelope": envelope.to_dict(),
    }
    save_json_cache(
        namespace=DAILY_PLAN_SNAPSHOT_NAMESPACE,
        cache_key=cache_key,
        provider=DAILY_PLAN_SNAPSHOT_PROVIDER,
        range_start=plan_date.isoformat(),
        range_end=plan_date.isoformat(),
        scope_hash=plan_date.isoformat(),
        ttl_seconds=ttl_seconds,
        payload=payload,
        metadata=metadata,
    )
    return DailyPlanSnapshot(
        plan_date=plan_date,
        generated_at=generated_at,
        envelope=envelope,
        is_stale=False,
        cache_key=cache_key,
        metadata=metadata,
    )


def load_daily_plan_snapshot(
    plan_date: date,
    *,
    allow_stale: bool = True,
    ttl_seconds: Optional[int] = None,
) -> Optional[DailyPlanSnapshot]:
    entry = load_json_cache(
        namespace=DAILY_PLAN_SNAPSHOT_NAMESPACE,
        cache_key=daily_plan_snapshot_cache_key(plan_date),
        ttl_seconds=_snapshot_ttl_seconds() if ttl_seconds is None else ttl_seconds,
        allow_stale=allow_stale,
        touch=False,
    )
    if entry is None:
        return None

    payload = entry.payload
    generated_at_text = str(payload.get("generated_at") or entry.metadata.get("generated_at") or "")
    envelope_payload = payload.get("envelope")
    if not isinstance(envelope_payload, dict):
        return None

    try:
        generated_at = datetime.fromisoformat(generated_at_text)
        envelope = DailyPlanEnvelope.from_dict(envelope_payload)
    except Exception:
        return None

    return DailyPlanSnapshot(
        plan_date=plan_date,
        generated_at=generated_at,
        envelope=envelope,
        is_stale=entry.is_stale,
        cache_key=entry.cache_key,
        metadata=entry.metadata,
    )


def daily_plan_snapshot_cache_key(plan_date: date) -> str:
    return plan_date.isoformat()


def _snapshot_ttl_seconds() -> int:
    raw_value = os.getenv("PLANNING_DAILY_SNAPSHOT_SECONDS", "").strip()
    if not raw_value:
        return DEFAULT_DAILY_PLAN_SNAPSHOT_SECONDS

    try:
        ttl_seconds = int(raw_value)
    except ValueError as exc:
        raise ValueError(
            f"PLANNING_DAILY_SNAPSHOT_SECONDS must be an integer, got: {raw_value!r}"
        ) from exc

    if ttl_seconds <= 0:
        raise ValueError("PLANNING_DAILY_SNAPSHOT_SECONDS must be greater than 0.")

    return ttl_seconds
