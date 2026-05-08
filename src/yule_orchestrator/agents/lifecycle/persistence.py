"""Engineering lifecycle persistence — single writer for session.extra.

Stabilisation Phase 1 surfaced repeated session.extra writes
scattered across the router (thread_id, forum link, work_report)
and the agents layer (research_pack, coding_proposal). Each writer
re-implemented the merge + replace + update_session sequence and
each one had its own way of handling failures, so a missing
extra key in one path didn't show up in another's diagnostic.

This module consolidates the lifecycle-level writers behind one
helper surface so:

  - merge semantics are identical across callers (live extra dict
    mutation + frozen dataclass replace + update_session round-trip),
  - persistence failures land on ``session.extra['persistence_error']``
    with a structured ``{step, reason, keys}`` payload — never silently
    swallowed,
  - the helper ensures everything written is JSON-serialisable so
    ``json_valid`` stays true on the SQLite payload.

It does NOT replace :func:`agents.research.persistence.persist_research_artifacts`
yet — that helper still owns the pack-shape conversions. Future work:
have ``persist_research_artifacts`` delegate to
:func:`persist_research_pack_state` for the status keys, leaving the
pack serialisation in place.
"""

from __future__ import annotations

import datetime as _dt
import json as _json
from dataclasses import asdict as _dc_asdict, is_dataclass as _is_dataclass
from typing import Any, Mapping, Optional, Sequence


__all__ = (
    "PersistenceResult",
    "merge_session_extra",
    "persist_thread_link",
    "persist_research_forum_link",
    "persist_research_pack_state",
    "persist_work_report_state",
    "to_json_safe",
)


def _json_serialisable(value: Any) -> bool:
    """Quick "is this JSON-safe?" test used to short-circuit the
    coercion. Avoids the cost of ``json.dumps`` on already-clean
    primitives."""

    return isinstance(value, (str, int, float, bool, type(None)))


def to_json_safe(value: Any) -> Any:
    """Recursively coerce *value* into JSON-serialisable types.

    - dataclasses → ``asdict``
    - tuple/set → list
    - datetime/date → ``isoformat``
    - Mapping → dict (recursively coerced)
    - everything else with a non-trivial repr falls through to
      ``str()`` so the payload never raises during ``json.dumps``.
    """

    if value is None or _json_serialisable(value):
        return value
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_json_safe(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): to_json_safe(item) for key, item in value.items()}
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    if _is_dataclass(value):
        return to_json_safe(_dc_asdict(value))
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return to_json_safe(value.to_dict())
        except Exception:  # noqa: BLE001
            return str(value)
    return str(value)


class PersistenceResult:
    """Outcome of one persistence call.

    Plain object (not a dataclass) so callers can attach an updated
    session reference without dataclass replace gymnastics.
    """

    __slots__ = ("session", "ok", "step", "reason", "keys")

    def __init__(
        self,
        *,
        session: Any,
        ok: bool,
        step: Optional[str] = None,
        reason: Optional[str] = None,
        keys: Sequence[str] = (),
    ) -> None:
        self.session = session
        self.ok = ok
        self.step = step
        self.reason = reason
        self.keys = tuple(str(k) for k in keys)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"PersistenceResult(ok={self.ok}, step={self.step!r}, "
            f"reason={self.reason!r}, keys={self.keys!r})"
        )


def _record_persistence_error(
    session: Any, *, step: str, reason: str, keys: Sequence[str]
) -> None:
    """Stamp a persistence failure onto session.extra['persistence_error'].

    Best-effort. Never raises — callers must still get a return value.
    """

    if session is None:
        return
    try:
        live = getattr(session, "extra", None)
        if isinstance(live, dict):
            live["persistence_error"] = {
                "step": step,
                "reason": reason,
                "keys": sorted(str(k) for k in keys),
            }
    except Exception:  # noqa: BLE001
        return


def merge_session_extra(
    session: Any,
    updates: Mapping[str, Any],
    *,
    now: Optional[_dt.datetime] = None,
) -> PersistenceResult:
    """Merge *updates* into ``session.extra`` and persist via
    ``update_session``.

    All values are coerced via :func:`to_json_safe` before the merge
    so SQLite's ``json_valid`` check stays clean. The live extra dict
    is mutated in-place (test stubs read it back without capturing
    the returned session) and the frozen dataclass path runs
    ``dataclasses.replace`` + ``update_session`` for production
    workflow rows.

    Returns a :class:`PersistenceResult` — ``ok=True`` when the
    SQLite write succeeded, ``ok=False`` with a structured reason
    otherwise. Callers can surface the reason in a Discord reply or
    inspect ``session.extra['persistence_error']`` for the same
    payload.
    """

    if session is None:
        return PersistenceResult(session=None, ok=False, step="merge", reason="session is None")
    if not updates:
        return PersistenceResult(session=session, ok=True)

    safe_updates = {str(key): to_json_safe(value) for key, value in updates.items()}
    keys_for_error = tuple(safe_updates.keys())

    try:
        from dataclasses import replace as _dc_replace

        from ..workflow_state import update_session
    except Exception as exc:  # noqa: BLE001
        _record_persistence_error(
            session,
            step="import update_session",
            reason=str(exc),
            keys=keys_for_error,
        )
        return PersistenceResult(
            session=session,
            ok=False,
            step="import",
            reason=str(exc),
            keys=keys_for_error,
        )

    # Mirror to live extra dict so test stubs see the new keys
    # without having to capture the returned session.
    live = getattr(session, "extra", None)
    if isinstance(live, dict):
        for key, value in safe_updates.items():
            live[key] = value

    existing = dict(getattr(session, "extra", {}) or {})
    merged = {**existing, **safe_updates}
    try:
        updated = _dc_replace(session, extra=merged)
    except TypeError:
        # Plain stub — in-place mutation above already covers it.
        return PersistenceResult(session=session, ok=True, keys=keys_for_error)
    try:
        update_session(updated, now=now or _dt.datetime.now().astimezone())
    except Exception as exc:  # noqa: BLE001
        _record_persistence_error(
            updated,
            step="update_session",
            reason=str(exc),
            keys=keys_for_error,
        )
        return PersistenceResult(
            session=updated,
            ok=False,
            step="update_session",
            reason=str(exc),
            keys=keys_for_error,
        )
    # Successful write — clear any stale persistence_error stamp.
    try:
        live2 = getattr(updated, "extra", None)
        if isinstance(live2, dict):
            live2.pop("persistence_error", None)
    except Exception:  # noqa: BLE001
        pass
    return PersistenceResult(session=updated, ok=True, keys=keys_for_error)


def persist_thread_link(
    session: Any,
    thread_id: Optional[int],
    *,
    now: Optional[_dt.datetime] = None,
) -> PersistenceResult:
    """Stamp the Discord work-thread id back on ``session.thread_id``.

    No-op when *thread_id* is ``None`` or already matches the current
    session.thread_id. Failures land in
    ``session.extra['persistence_error']`` and the result reports
    the error so the caller can surface it.
    """

    if session is None or thread_id is None:
        return PersistenceResult(session=session, ok=True)
    try:
        existing = getattr(session, "thread_id", None)
    except Exception:  # noqa: BLE001
        existing = None
    if existing == thread_id:
        return PersistenceResult(session=session, ok=True)

    try:
        from dataclasses import replace as _dc_replace

        from ..workflow_state import update_session
    except Exception as exc:  # noqa: BLE001
        _record_persistence_error(
            session,
            step="import update_session (thread_id)",
            reason=str(exc),
            keys=("thread_id",),
        )
        return PersistenceResult(
            session=session, ok=False, step="import", reason=str(exc),
            keys=("thread_id",),
        )

    # In-place fast path.
    try:
        if hasattr(session, "thread_id") and not isinstance(session, type):
            try:
                session.thread_id = thread_id  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass

    try:
        updated = _dc_replace(session, thread_id=thread_id)
    except TypeError:
        return PersistenceResult(session=session, ok=True, keys=("thread_id",))
    try:
        update_session(updated, now=now or _dt.datetime.now().astimezone())
    except Exception as exc:  # noqa: BLE001
        _record_persistence_error(
            updated,
            step="update_session (thread_id)",
            reason=str(exc),
            keys=("thread_id",),
        )
        return PersistenceResult(
            session=updated, ok=False, step="update_session", reason=str(exc),
            keys=("thread_id",),
        )
    return PersistenceResult(session=updated, ok=True, keys=("thread_id",))


def persist_research_forum_link(
    session: Any,
    *,
    thread_id: Optional[int] = None,
    url: Optional[str] = None,
    open_call_posted: Optional[bool] = None,
    open_call_error: Optional[str] = None,
    forum_comment_mode: Optional[str] = None,
    now: Optional[_dt.datetime] = None,
) -> PersistenceResult:
    """Persist the research forum thread id + open-call signal pair."""

    updates: dict[str, Any] = {}
    if thread_id is not None:
        updates["research_forum_thread_id"] = int(thread_id)
    if url is not None:
        updates["research_forum_thread_url"] = str(url)
    if forum_comment_mode is not None:
        updates["forum_comment_mode"] = str(forum_comment_mode)
    if open_call_posted is not None or open_call_error is not None:
        updates["research_open_call_posted"] = (
            bool(open_call_posted) if open_call_posted is not None else None
        )
        updates["research_open_call_error"] = (
            str(open_call_error) if open_call_error else None
        )
        # Legacy keys mirrored for back-compat with existing diagnostic.
        updates["forum_kickoff_posted"] = updates["research_open_call_posted"]
        updates["forum_kickoff_error"] = updates["research_open_call_error"]
    if not updates:
        return PersistenceResult(session=session, ok=True)
    return merge_session_extra(session, updates, now=now)


def persist_research_pack_state(
    session: Any,
    *,
    pack: Any = None,
    status: Optional[str] = None,
    source_count: Optional[int] = None,
    stop_reason: Optional[str] = None,
    error: Optional[Mapping[str, Any]] = None,
    now: Optional[_dt.datetime] = None,
) -> PersistenceResult:
    """Persist the research_pack snapshot and its status sidecar.

    Used as a thin layer over :func:`merge_session_extra` so callers
    don't have to remember the ``research_pack`` / ``research_status`` /
    ``research_source_count`` / ``research_stop_reason`` /
    ``research_pack_error`` key naming convention.
    """

    updates: dict[str, Any] = {}
    if pack is not None:
        updates["research_pack"] = to_json_safe(pack)
    if status is not None:
        updates["research_status"] = str(status)
    if source_count is not None:
        updates["research_source_count"] = int(source_count)
    if stop_reason is not None:
        updates["research_stop_reason"] = str(stop_reason)
    if error is not None:
        updates["research_pack_error"] = to_json_safe(error)
    if not updates:
        return PersistenceResult(session=session, ok=True)
    return merge_session_extra(session, updates, now=now)


def persist_work_report_state(
    session: Any,
    *,
    report: Any = None,
    status: Optional[str] = None,
    error: Optional[Mapping[str, Any]] = None,
    now: Optional[_dt.datetime] = None,
) -> PersistenceResult:
    """Persist the work_report dict + its status sidecar."""

    updates: dict[str, Any] = {}
    if report is not None:
        updates["work_report"] = to_json_safe(report)
    if status is not None:
        updates["work_report_status"] = str(status)
    if error is not None:
        updates["work_report_error"] = to_json_safe(error)
    if not updates:
        return PersistenceResult(session=session, ok=True)
    return merge_session_extra(session, updates, now=now)
