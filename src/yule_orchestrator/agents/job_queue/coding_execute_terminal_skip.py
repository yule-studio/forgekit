"""P1-Z B — terminal session resurrection 차단 helpers.

배경
====
``coding_execute_dispatcher.iter_ready_coding_jobs`` 는 옛 wiring 에서
``session.state`` 검사가 없어서 operator 가 폐기 (REJECTED) 한 session 도
``coding_job=ready`` 가 extra 에 남아있으면 stale marker self-heal 로
다시 새 coding_execute row 를 만들어 살아났다.  canonical sessions
``166c416a1ed0`` / ``c7bc03b8d41a`` 가 runtime restart 후 살아나던
직접 원인.

본 모듈
========
* :func:`is_terminal_session(session) -> bool` — WorkflowState 가
  ``completed`` / ``rejected`` 이면 True.
* :data:`SESSION_EXTRA_TERMINAL_SKIP_KEY` — operator surface 에 stamp 할
  audit key.
* :func:`stamp_terminal_session_skip(...)` — dispatch pre-pass 가 호출:
  terminal session 인데 ready coding_job / dispatch marker 가 남아있으면
  ``coding_execute_terminal_skip`` audit 만 stamp 하고 re-enqueue 차단.

dispatcher 자체 LOC 가 1000 임계 안에 머물도록 별도 모듈로 분리.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Optional


logger = logging.getLogger(__name__)


TERMINAL_SESSION_STATES: frozenset = frozenset({"completed", "rejected"})
SESSION_EXTRA_TERMINAL_SKIP_KEY: str = "coding_execute_terminal_skip"


def is_terminal_session(session: Any) -> bool:
    """session.state ∈ {completed, rejected} 면 True.

    WorkflowState enum / 문자열 둘 다 흡수.  state 가 None / 누락이면
    False (보수적으로 살아있다고 본다).
    """

    raw = getattr(session, "state", None)
    if raw is None:
        return False
    text = getattr(raw, "value", None)
    if text is None:
        text = str(raw)
    return str(text).strip().lower() in TERMINAL_SESSION_STATES


def stamp_terminal_session_skip(
    *,
    session_loader: Optional[Callable[[], Iterable[Any]]] = None,
    update_session_fn: Optional[Callable[..., Any]] = None,
    coding_job_reader: Optional[Callable[[Any], Optional[Mapping[str, Any]]]] = None,
    dispatch_marker_key: str = "coding_execute_dispatch",
    ready_statuses: Optional[frozenset] = None,
    now: Optional[datetime] = None,
) -> int:
    """Terminal session 에 audit 만 stamp 하고 re-enqueue 차단.

    *session_loader* / *update_session_fn* / *coding_job_reader* 는 caller
    (dispatcher) 가 inject — 본 helper 는 storage I/O 직접 안 함.
    *ready_statuses* 가 None 이면 ``frozenset({"ready"})`` 로 폴백.

    반환: audit 가 stamp 된 session 수 (테스트가 0/1/N 검증).
    """

    if session_loader is None:
        from .coding_execute_dispatcher import _default_session_loader

        session_loader = _default_session_loader
    if update_session_fn is None:
        from .coding_execute_dispatcher import _default_update_session

        update_session_fn = _default_update_session
    if coding_job_reader is None:
        from .coding_execute_dispatcher import _read_coding_job

        coding_job_reader = _read_coding_job
    ready_set = ready_statuses or frozenset({"ready"})

    try:
        sessions = list(session_loader() or ())
    except Exception:  # noqa: BLE001
        logger.warning(
            "stamp_terminal_session_skip: session loader raised",
            exc_info=True,
        )
        return 0

    when = (now or datetime.now(tz=timezone.utc)).replace(microsecond=0).isoformat()
    stamped = 0
    for session in sessions:
        if not is_terminal_session(session):
            continue
        coding_job = coding_job_reader(session)
        has_ready_job = (
            isinstance(coding_job, Mapping)
            and str(coding_job.get("status") or "").strip().lower() in ready_set
        )
        extra_raw = getattr(session, "extra", None) or {}
        has_marker = (
            isinstance(extra_raw, Mapping)
            and isinstance(extra_raw.get(dispatch_marker_key), Mapping)
        )
        if not (has_ready_job or has_marker):
            continue

        extra = dict(extra_raw)
        raw_state = getattr(session, "state", "")
        state_text = getattr(raw_state, "value", None) or str(raw_state or "")
        state_text = str(state_text).lower()
        existing_skip = extra.get(SESSION_EXTRA_TERMINAL_SKIP_KEY)
        # idempotent — 같은 terminal state 면 audit 재기록 안 함
        if (
            isinstance(existing_skip, Mapping)
            and str(existing_skip.get("session_state") or "").lower() == state_text
        ):
            continue
        extra[SESSION_EXTRA_TERMINAL_SKIP_KEY] = {
            "session_state": state_text,
            "reason": "terminal_session_skip",
            "had_ready_coding_job": bool(has_ready_job),
            "had_dispatch_marker": bool(has_marker),
            "at": when,
        }

        try:
            from dataclasses import replace as _replace

            updated = _replace(session, extra=extra)
        except Exception:  # noqa: BLE001
            continue
        try:
            update_session_fn(
                updated,
                now=(now or datetime.now(tz=timezone.utc)),
            )
            stamped += 1
            logger.warning(
                "coding_execute dispatcher: terminal session skip — "
                "session=%s state=%s had_ready_job=%s had_marker=%s — "
                "re-enqueue 차단",
                getattr(session, "session_id", "?"),
                state_text,
                has_ready_job,
                has_marker,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "coding_execute dispatcher: persisting terminal_skip audit raised",
                exc_info=True,
            )
    return stamped


__all__ = (
    "SESSION_EXTRA_TERMINAL_SKIP_KEY",
    "TERMINAL_SESSION_STATES",
    "is_terminal_session",
    "stamp_terminal_session_skip",
)
