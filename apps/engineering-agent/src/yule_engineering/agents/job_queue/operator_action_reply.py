"""Operator action thread reply handler — P0-S.

`#승인-대기` 채널의 operator action 카드 (INFO/ACCESS/SECRET/DECISION)
에 대한 thread reply 를 처리한다. 기존 :mod:`approval_reply` 가
APPROVE/REJECT/HOLD/UNCLEAR 어휘만 다루는 것과 분리해 둔다 — 새
유형은 ``key=value`` 라인 기반이라 어휘 파서를 공유할 수 없다.

흐름
====
1. ``route_approval_channel_message`` 가 reply 메시지를 본다.
2. 이 모듈의 :func:`find_pending_operator_action_for_reply` 가 thread
   reply 위치를 보고 SAVED operator action 카드 1 건을 찾는다.
3. :func:`handle_operator_action_reply` 가 텍스트를 파싱하고 session
   extra 를 갱신한 결과를 :class:`OperatorActionReplyOutcome` 으로 반환.
4. 위 outcome 은 router 가 친절한 ack 메시지를 게시할 때 사용한다.

세션 갱신은 ``WorkflowSession.extra`` 의:

- ``operator_state`` (:class:`OperatorSessionState`)
- ``operator_pending_requests``
- ``operator_answered_requests``

세 키만 건드린다. WorkflowState (intake/approved/in_progress/...) 는
손대지 않는다 — sub-state 라서 macro state 와 직교한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from ..operator_action import (
    OperatorActionReply,
    OperatorActionRequest,
    OperatorActionType,
    OperatorSessionState,
    operator_action_request_from_approval_payload,
    parse_operator_action_reply,
    stamp_answered_request,
)
from .approval_worker import (
    JOB_TYPE_APPROVAL_POST,
    OPERATOR_ACTION_KINDS,
)
from .state_machine import JobState
from .store import Job, JobQueue


_REPLYABLE_STATES: tuple[JobState, ...] = (JobState.SAVED,)


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def find_pending_operator_action_for_reply(
    *,
    queue: JobQueue,
    session_id: str,
    source_message_id: Optional[int] = None,
    source_thread_id: Optional[int] = None,
) -> Optional[Job]:
    """SAVED 상태의 operator-action ``approval_post`` job 중 reply 가 가장
    가능성 높은 1 건을 반환.

    우선순위:
      1. ``source_message_id`` 정확 매칭
      2. ``source_thread_id`` 매칭 (가장 최근)
      3. 세션의 가장 최근 operator-action SAVED 카드

    어떤 카드도 없으면 ``None`` — caller 는 일반 approval 핸들러로
    fallback 한다.
    """

    if not session_id:
        return None
    candidates: list[Job] = []
    for job in queue.list_for_session(session_id, states=_REPLYABLE_STATES):
        if job.job_type != JOB_TYPE_APPROVAL_POST:
            continue
        kind = (job.payload or {}).get("approval_kind")
        if kind not in OPERATOR_ACTION_KINDS:
            continue
        candidates.append(job)
    if not candidates:
        return None

    if source_message_id is not None:
        for job in candidates:
            existing = (job.payload or {}).get("source_message_id")
            if existing is not None and int(existing) == int(source_message_id):
                return job

    if source_thread_id is not None:
        thread_matches = [
            job
            for job in candidates
            if (job.payload or {}).get("source_thread_id") is not None
            and int((job.payload or {}).get("source_thread_id"))
            == int(source_thread_id)
        ]
        if thread_matches:
            return max(thread_matches, key=lambda j: j.created_at)

    return max(candidates, key=lambda j: j.created_at)


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OperatorActionReplyOutcome:
    """:func:`handle_operator_action_reply` 결과.

    - ``handled`` False: 매칭 카드 없음 — caller 가 fallback.
    - ``handled`` True 인데 ``reply.is_complete`` False: 응답 부족 / 거부.
    - ``handled`` True 이고 ``reply.is_complete`` True: 세션 상태 복귀.
    """

    handled: bool
    request_type: Optional[OperatorActionType] = None
    approval_job_id: Optional[str] = None
    request: Optional[OperatorActionRequest] = None
    reply: Optional[OperatorActionReply] = None
    new_state: Optional[OperatorSessionState] = None
    audit: Mapping[str, Any] = field(default_factory=dict)
    skipped_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Session adapter — caller injects load/update so 모듈은 storage agnostic
# ---------------------------------------------------------------------------


from typing import Callable

LoadSessionFn = Callable[[str], Optional[Any]]
UpdateSessionFn = Callable[[Any, Mapping[str, Any]], Any]


def _default_load_session(session_id: str) -> Optional[Any]:
    try:
        from ..workflow_state import load_session as _load
    except Exception:  # noqa: BLE001 - partial install
        return None
    try:
        return _load(session_id)
    except Exception:  # noqa: BLE001
        return None


def _default_update_session(session: Any, extra: Mapping[str, Any]) -> Any:
    try:
        from dataclasses import replace as _replace
        from ..workflow_state import update_session as _update
    except Exception:  # noqa: BLE001
        return session
    try:
        updated = _replace(session, extra=dict(extra))
        return _update(updated, now=datetime.now(tz=timezone.utc))
    except Exception:  # noqa: BLE001
        return session


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def handle_operator_action_reply(
    *,
    queue: JobQueue,
    text: str,
    session_id: str,
    answered_by: str,
    answered_at: Optional[str] = None,
    source_message_id: Optional[int] = None,
    source_thread_id: Optional[int] = None,
    load_session_fn: Optional[LoadSessionFn] = None,
    update_session_fn: Optional[UpdateSessionFn] = None,
) -> OperatorActionReplyOutcome:
    """thread reply *text* 를 operator-action 카드에 매핑해 처리.

    side-effect 는 두 가지뿐:
      - SAVED operator-action job 1 건을 찾아 그 request 를 복원.
      - 매칭되면 :func:`stamp_answered_request` 결과를 세션 extra 로 저장.

    queue 의 ``approval_post`` row 자체는 SAVED 그대로 유지한다 — 같은
    카드에 2 차 응답이 들어올 수 있고, gateway 가 재요청 카드를 다시
    enqueue 할 수도 있다. dedup 은 enqueue 측의 ``find_active`` 가 이미
    담당.
    """

    job = find_pending_operator_action_for_reply(
        queue=queue,
        session_id=session_id,
        source_message_id=source_message_id,
        source_thread_id=source_thread_id,
    )
    if job is None:
        return OperatorActionReplyOutcome(
            handled=False, skipped_reason="no_matching_operator_action"
        )

    request = operator_action_request_from_approval_payload(job.payload or {})
    if request is None:
        return OperatorActionReplyOutcome(
            handled=False,
            approval_job_id=job.job_id,
            skipped_reason="missing_operator_action_payload",
        )

    reply = parse_operator_action_reply(
        request_type=request.request_type, text=text
    )

    answered_at_iso = (answered_at or "").strip() or _utc_now_iso()
    load_fn = load_session_fn or _default_load_session
    update_fn = update_session_fn or _default_update_session

    session = load_fn(session_id)
    new_extra: Mapping[str, Any] = {}
    new_state: Optional[OperatorSessionState] = None
    if session is not None:
        existing_extra = getattr(session, "extra", None) or {}
        new_extra = stamp_answered_request(
            session_extra=existing_extra,
            reply=reply,
            answered_by=answered_by,
            answered_at=answered_at_iso,
        )
        try:
            update_fn(session, new_extra)
        except Exception:  # noqa: BLE001 — audit is best-effort
            pass
        raw_state = (
            new_extra.get("operator_state")
            if isinstance(new_extra, Mapping)
            else None
        )
        if isinstance(raw_state, str):
            try:
                new_state = OperatorSessionState(raw_state)
            except ValueError:
                new_state = None

    return OperatorActionReplyOutcome(
        handled=True,
        request_type=request.request_type,
        approval_job_id=job.job_id,
        request=request,
        reply=reply,
        new_state=new_state,
        audit={
            "answered_by": answered_by,
            "answered_at": answered_at_iso,
            "source_message_id": source_message_id,
            "source_thread_id": source_thread_id,
            "request_type": request.request_type.value,
            "is_complete": reply.is_complete,
            "rejected_reason": reply.rejected_reason,
        },
    )


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


__all__ = (
    "LoadSessionFn",
    "OperatorActionReplyOutcome",
    "UpdateSessionFn",
    "find_pending_operator_action_for_reply",
    "handle_operator_action_reply",
)
