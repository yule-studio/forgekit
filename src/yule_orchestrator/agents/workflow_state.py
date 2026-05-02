"""SQLite-backed state for the engineering-agent Discord workflow.

Each task started by ``/engineer-intake`` (or the ``yule engineer`` CLI) is
tracked as a :class:`WorkflowSession`. State transitions are guarded by the
orchestrator (``workflow.py``), but the persistence shape is here so tests
and operators can introspect a session without going through Discord.

Stored via the existing ``save_json_cache`` / ``load_json_cache`` layer to
match the checkpoint state pattern.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

from ..storage import list_json_cache_entries, load_json_cache, save_json_cache


WORKFLOW_NAMESPACE = "engineering-agent-workflow"
WORKFLOW_TTL_SECONDS = 30 * 24 * 60 * 60


class WorkflowState(str, Enum):
    INTAKE = "intake"
    APPROVED = "approved"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REJECTED = "rejected"


@dataclass(frozen=True)
class WorkflowSession:
    session_id: str
    prompt: str
    task_type: str
    state: WorkflowState
    created_at: datetime
    updated_at: datetime
    role_sequence: Sequence[str] = ()
    executor_role: Optional[str] = None
    executor_runner: Optional[str] = None
    references_user: Sequence[str] = ()
    references_suggested: Sequence[str] = ()
    references_used: Sequence[Mapping[str, Any]] = ()
    progress_notes: Sequence[str] = ()
    summary: Optional[str] = None
    rejection_reason: Optional[str] = None
    channel_id: Optional[int] = None
    user_id: Optional[int] = None
    thread_id: Optional[int] = None
    write_requested: bool = False
    write_blocked_reason: Optional[str] = None
    review_cycle: int = 0
    review_feedbacks: Sequence[Mapping[str, Any]] = ()
    extra: Mapping[str, Any] = field(default_factory=dict)


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


def save_session(session: WorkflowSession) -> None:
    payload = _to_payload(session)
    save_json_cache(
        namespace=WORKFLOW_NAMESPACE,
        cache_key=session.session_id,
        provider="engineering-agent-workflow",
        range_start=session.created_at.isoformat(),
        range_end=session.updated_at.isoformat(),
        scope_hash=session.task_type,
        ttl_seconds=WORKFLOW_TTL_SECONDS,
        payload=payload,
    )


def load_session(session_id: str) -> Optional[WorkflowSession]:
    entry = load_json_cache(
        namespace=WORKFLOW_NAMESPACE,
        cache_key=session_id,
        allow_stale=True,
        touch=False,
    )
    if entry is None:
        return None
    return _from_payload(entry.payload)


def list_sessions(*, limit: int = 100) -> tuple[WorkflowSession, ...]:
    """Return recent workflow sessions, newest first.

    The local cache is the only workflow index in the MVP. Listing recent
    entries lets Discord routing recover an already-open thread when the user
    explicitly asks to continue instead of registering another task.
    """

    sessions: list[WorkflowSession] = []
    for entry in list_json_cache_entries(
        namespace=WORKFLOW_NAMESPACE,
        provider="engineering-agent-workflow",
        include_expired=True,
        limit=limit,
    ):
        try:
            sessions.append(_from_payload(entry.payload))
        except Exception:  # noqa: BLE001 - ignore corrupt cache rows
            continue
    # Some legacy rows persisted ``updated_at`` as naive ISO strings
    # (older writes) while newer rows use ``datetime.now().astimezone()``
    # which is timezone-aware. Sorting the mixed list directly raises
    # ``TypeError: can't compare offset-naive and offset-aware datetimes``.
    # Normalise the sort key only — the dataclass instances themselves
    # are left untouched so callers see the persisted shape.
    sessions.sort(key=lambda item: _sortable_dt(item.updated_at), reverse=True)
    return tuple(sessions)


def find_latest_open_session(
    *,
    channel_id: Optional[int] = None,
    user_id: Optional[int] = None,
    thread_id: Optional[int] = None,
    exclude_session_id: Optional[str] = None,
    limit: int = 100,
) -> Optional[WorkflowSession]:
    """Find the newest non-closed session matching the given Discord scope."""

    for session in list_sessions(limit=limit):
        if exclude_session_id and session.session_id == exclude_session_id:
            continue
        if session.state in {WorkflowState.COMPLETED, WorkflowState.REJECTED}:
            continue
        if thread_id is not None and session.thread_id != thread_id:
            continue
        if channel_id is not None and session.channel_id != channel_id:
            continue
        if user_id is not None and session.user_id != user_id:
            continue
        return session
    return None


def update_session(session: WorkflowSession, *, now: datetime) -> WorkflowSession:
    """Bump updated_at and persist; returns the updated copy."""

    updated = replace(session, updated_at=now)
    save_session(updated)
    return updated


def _to_payload(session: WorkflowSession) -> Mapping[str, Any]:
    return {
        "session_id": session.session_id,
        "prompt": session.prompt,
        "task_type": session.task_type,
        "state": session.state.value,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
        "role_sequence": list(session.role_sequence),
        "executor_role": session.executor_role,
        "executor_runner": session.executor_runner,
        "references_user": list(session.references_user),
        "references_suggested": list(session.references_suggested),
        "references_used": [dict(item) for item in session.references_used],
        "progress_notes": list(session.progress_notes),
        "summary": session.summary,
        "rejection_reason": session.rejection_reason,
        "channel_id": session.channel_id,
        "user_id": session.user_id,
        "thread_id": session.thread_id,
        "write_requested": session.write_requested,
        "write_blocked_reason": session.write_blocked_reason,
        "review_cycle": session.review_cycle,
        "review_feedbacks": [dict(item) for item in session.review_feedbacks],
        "extra": dict(session.extra),
    }


def _from_payload(payload: Mapping[str, Any]) -> WorkflowSession:
    return WorkflowSession(
        session_id=str(payload["session_id"]),
        prompt=str(payload.get("prompt", "")),
        task_type=str(payload.get("task_type", "unknown")),
        state=WorkflowState(str(payload.get("state", WorkflowState.INTAKE.value))),
        created_at=datetime.fromisoformat(str(payload["created_at"])),
        updated_at=datetime.fromisoformat(str(payload["updated_at"])),
        role_sequence=tuple(str(item) for item in payload.get("role_sequence", [])),
        executor_role=_optional_str(payload.get("executor_role")),
        executor_runner=_optional_str(payload.get("executor_runner")),
        references_user=tuple(str(item) for item in payload.get("references_user", [])),
        references_suggested=tuple(str(item) for item in payload.get("references_suggested", [])),
        references_used=tuple(
            dict(item) if isinstance(item, dict) else {"title": str(item)}
            for item in payload.get("references_used", [])
        ),
        progress_notes=tuple(str(item) for item in payload.get("progress_notes", [])),
        summary=_optional_str(payload.get("summary")),
        rejection_reason=_optional_str(payload.get("rejection_reason")),
        channel_id=_optional_int(payload.get("channel_id")),
        user_id=_optional_int(payload.get("user_id")),
        thread_id=_optional_int(payload.get("thread_id")),
        write_requested=bool(payload.get("write_requested", False)),
        write_blocked_reason=_optional_str(payload.get("write_blocked_reason")),
        review_cycle=int(payload.get("review_cycle") or 0),
        review_feedbacks=tuple(
            dict(item) if isinstance(item, dict) else {}
            for item in payload.get("review_feedbacks") or ()
        ),
        extra=dict(payload.get("extra") or {}),
    )


def _sortable_dt(value: datetime) -> datetime:
    """Return *value* coerced to a timezone-aware datetime for ordering.

    Naive datetimes (legacy cache rows) are treated as UTC — the absolute
    instant may shift by the operator's local offset, but ordering is
    consistent within naive and aware slices, which is enough for "most
    recently updated session first". The dataclass itself is never
    mutated; only the sort key sees the coerced value.
    """

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
