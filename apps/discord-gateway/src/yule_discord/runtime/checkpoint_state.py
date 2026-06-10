from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Sequence

from yule_planning.models import PlanningCheckpoint
from yule_storage import (
    TaskCompletionEvent,
    load_json_cache,
    record_task_completion_event,
    save_json_cache,
)

CHECKPOINT_RESPONSE_NAMESPACE = "discord-checkpoint-responses"
CHECKPOINT_RESPONSE_TTL_SECONDS = 7 * 24 * 60 * 60

CHECKPOINT_PENDING_NAMESPACE = "discord-checkpoint-pending"
CHECKPOINT_PENDING_TTL_SECONDS = 30 * 60

CHECKPOINT_RESPONSE_STATUS_DONE = "done"
CHECKPOINT_RESPONSE_STATUS_SKIPPED = "skipped"


@dataclass(frozen=True)
class CheckpointPendingResponse:
    user_id: int
    plan_date: date
    channel_id: int
    checkpoint_ids: tuple[str, ...]
    sent_at: datetime
    checkpoint_metadata: dict[str, dict] = field(default_factory=dict)


def mark_checkpoint_responded(
    *,
    plan_date: date,
    checkpoint_id: str,
    status: str,
    user_id: int,
    responded_at: datetime,
    source_event_uid: str | None = None,
    source_event_title: str | None = None,
    block_title: str | None = None,
    checkpoint_kind: str | None = None,
    block_minutes: int | None = None,
) -> None:
    save_json_cache(
        namespace=CHECKPOINT_RESPONSE_NAMESPACE,
        cache_key=_response_cache_key(plan_date, checkpoint_id),
        provider="discord-checkpoint-state",
        range_start=responded_at.isoformat(),
        range_end=responded_at.isoformat(),
        scope_hash=plan_date.isoformat(),
        ttl_seconds=CHECKPOINT_RESPONSE_TTL_SECONDS,
        payload={
            "plan_date": plan_date.isoformat(),
            "checkpoint_id": checkpoint_id,
            "status": status,
            "responded_at": responded_at.isoformat(),
            "user_id": user_id,
            "source_event_uid": source_event_uid,
            "source_event_title": source_event_title,
            "block_title": block_title,
            "checkpoint_kind": checkpoint_kind,
            "block_minutes": block_minutes,
        },
    )

    record_task_completion_event(
        TaskCompletionEvent(
            plan_date=plan_date,
            checkpoint_id=checkpoint_id,
            status=status,
            user_id=user_id,
            responded_at=responded_at,
            source_event_uid=source_event_uid,
            source_event_title=source_event_title,
            block_title=block_title,
            checkpoint_kind=checkpoint_kind,
            block_minutes=block_minutes,
        )
    )


def has_checkpoint_been_responded(*, plan_date: date, checkpoint_id: str) -> bool:
    entry = load_json_cache(
        namespace=CHECKPOINT_RESPONSE_NAMESPACE,
        cache_key=_response_cache_key(plan_date, checkpoint_id),
        touch=False,
    )
    return entry is not None


def filter_unresponded_checkpoints(
    plan_date: date,
    checkpoints: Sequence[PlanningCheckpoint],
) -> list[PlanningCheckpoint]:
    return [
        checkpoint
        for checkpoint in checkpoints
        if not has_checkpoint_been_responded(
            plan_date=plan_date,
            checkpoint_id=checkpoint.checkpoint_id,
        )
    ]


def save_checkpoint_pending_response(
    *,
    user_id: int,
    plan_date: date,
    channel_id: int,
    checkpoints: Sequence[PlanningCheckpoint],
    sent_at: datetime,
) -> None:
    checkpoint_ids = [cp.checkpoint_id for cp in checkpoints]
    metadata = {
        cp.checkpoint_id: {
            "source_event_uid": cp.source_event_uid,
            "source_event_title": cp.source_event_title,
            "block_title": cp.block_title,
            "block_start": cp.block_start,
            "block_end": cp.block_end,
            "checkpoint_kind": cp.kind,
        }
        for cp in checkpoints
    }
    save_json_cache(
        namespace=CHECKPOINT_PENDING_NAMESPACE,
        cache_key=_pending_cache_key(user_id),
        provider="discord-checkpoint-state",
        range_start=sent_at.isoformat(),
        range_end=sent_at.isoformat(),
        scope_hash=str(user_id),
        ttl_seconds=CHECKPOINT_PENDING_TTL_SECONDS,
        payload={
            "user_id": user_id,
            "plan_date": plan_date.isoformat(),
            "channel_id": channel_id,
            "checkpoint_ids": checkpoint_ids,
            "checkpoint_metadata": metadata,
            "sent_at": sent_at.isoformat(),
        },
    )


def load_checkpoint_pending_response(*, user_id: int) -> CheckpointPendingResponse | None:
    entry = load_json_cache(
        namespace=CHECKPOINT_PENDING_NAMESPACE,
        cache_key=_pending_cache_key(user_id),
        allow_stale=False,
        touch=False,
    )
    if entry is None:
        return None
    payload = entry.payload
    raw_ids = payload.get("checkpoint_ids") or []
    raw_metadata = payload.get("checkpoint_metadata") or {}
    try:
        normalized_metadata = {
            str(key): dict(value) if isinstance(value, dict) else {}
            for key, value in raw_metadata.items()
        }
        return CheckpointPendingResponse(
            user_id=int(payload["user_id"]),
            plan_date=date.fromisoformat(str(payload["plan_date"])),
            channel_id=int(payload.get("channel_id") or 0),
            checkpoint_ids=tuple(str(item) for item in raw_ids),
            sent_at=datetime.fromisoformat(str(payload["sent_at"])),
            checkpoint_metadata=normalized_metadata,
        )
    except (KeyError, TypeError, ValueError):
        return None


def clear_checkpoint_pending_response(*, user_id: int) -> None:
    save_json_cache(
        namespace=CHECKPOINT_PENDING_NAMESPACE,
        cache_key=_pending_cache_key(user_id),
        provider="discord-checkpoint-state",
        range_start=None,
        range_end=None,
        scope_hash=str(user_id),
        ttl_seconds=1,
        payload={"user_id": user_id, "cleared": True},
    )


def _response_cache_key(plan_date: date, checkpoint_id: str) -> str:
    return f"{plan_date.isoformat()}:{checkpoint_id}"


def _pending_cache_key(user_id: int) -> str:
    return str(user_id)
