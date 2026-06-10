"""P1-L-2 D — merge 성공 직후 다음 coding slice 자동 enqueue.

배경 — ``approval_required`` / ``autonomous_merge`` 모두 merge 가 끝나면
사용자에게 "다음 진행해줘" 라고 다시 부탁받지 않아도 자동으로 다음
slice 가 굴러가야 한다.

세션 단위 ``coding_backlog`` (session.extra["coding_backlog"], list of
slice spec dict) 가 있으면 첫 항목을 pop 해 다음 coding_proposal 또는
coding_execute job 으로 enqueue. backlog 가 비어있으면 session done.

본 모듈은 enqueue 자체는 caller (``coding_job_factory`` 또는 inject 된
콜백) 에게 위임 — 여기서는 "다음에 무엇을 enqueue 해야 하는가" 만 결정.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, List, Mapping, Optional

from .pr_merge_continuation import EXTRA_PR_MERGE_AUDIT


EXTRA_CODING_BACKLOG: str = "coding_backlog"
"""session.extra 키 — 다음 코딩 slice 들의 dict list."""

EXTRA_SESSION_COMPLETED_REASON: str = "session_completed_reason"
"""백로그 비어서 세션 끝낸 경우 이유 (``backlog_empty_after_merge``)."""


class NextSliceAction(str, Enum):
    DISPATCH_SLICE = "dispatch_slice"
    """backlog 에 남은 slice → 첫 항목 pop 후 enqueue."""

    SESSION_DONE = "session_done"
    """backlog 비어있음 → 세션 종료 후속 마커."""

    SKIPPED_NOT_MERGED = "skipped_not_merged"
    """pr_merge_stage 가 pr_merged 가 아니라 무시."""


@dataclass(frozen=True)
class NextSliceDecision:
    action: NextSliceAction
    slice_spec: Optional[Mapping[str, Any]] = None
    """``DISPATCH_SLICE`` 일 때 caller 가 enqueue 할 spec."""

    remaining_backlog: int = 0
    extra_updates: Mapping[str, Any] = field(default_factory=dict)
    """caller 가 session.extra 에 머지할 dict (backlog 정리 + audit)."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def decide_next_slice(
    *,
    session_id: str,
    session_extra: Mapping[str, Any],
) -> NextSliceDecision:
    """merge 직후 호출 — 다음에 무엇을 할지 결정.

    ``pr_merge_stage == pr_merged`` 가 아니면 ``SKIPPED_NOT_MERGED``
    반환 — 안전을 위해 merge 가 실제로 끝났을 때만 동작.
    """

    from .pr_merge_continuation import EXTRA_PR_MERGE_STAGE, STAGE_PR_MERGED

    extra = session_extra or {}
    if extra.get(EXTRA_PR_MERGE_STAGE) != STAGE_PR_MERGED:
        return NextSliceDecision(action=NextSliceAction.SKIPPED_NOT_MERGED)

    backlog_raw = extra.get(EXTRA_CODING_BACKLOG) or ()
    backlog = [dict(item) for item in backlog_raw if isinstance(item, Mapping)]

    now = _now_iso()
    if not backlog:
        new_extra: dict = dict(extra)
        new_extra[EXTRA_CODING_BACKLOG] = []
        new_extra[EXTRA_SESSION_COMPLETED_REASON] = "backlog_empty_after_merge"
        audit = list(new_extra.get(EXTRA_PR_MERGE_AUDIT) or ())
        audit.append(
            {
                "event": "session_done_after_merge",
                "reason": "backlog_empty_after_merge",
                "at": now,
            }
        )
        new_extra[EXTRA_PR_MERGE_AUDIT] = audit
        return NextSliceDecision(
            action=NextSliceAction.SESSION_DONE,
            remaining_backlog=0,
            extra_updates=new_extra,
        )

    # backlog 첫 항목 pop, 나머지 보존
    next_slice = backlog[0]
    remaining = backlog[1:]
    new_extra2: dict = dict(extra)
    new_extra2[EXTRA_CODING_BACKLOG] = remaining
    audit = list(new_extra2.get(EXTRA_PR_MERGE_AUDIT) or ())
    audit.append(
        {
            "event": "next_slice_dispatched",
            "slice_summary": str(next_slice.get("summary") or next_slice.get("title") or ""),
            "remaining_backlog": len(remaining),
            "at": now,
        }
    )
    new_extra2[EXTRA_PR_MERGE_AUDIT] = audit
    return NextSliceDecision(
        action=NextSliceAction.DISPATCH_SLICE,
        slice_spec=next_slice,
        remaining_backlog=len(remaining),
        extra_updates=new_extra2,
    )


def dispatch_next_coding_slice(
    *,
    session_id: str,
    session_extra: Mapping[str, Any],
    persist_extra: Callable[[Mapping[str, Any]], None],
    enqueue_slice: Optional[Callable[[str, Mapping[str, Any]], Any]] = None,
    on_session_done: Optional[Callable[[str], Any]] = None,
) -> NextSliceDecision:
    """결정 + side-effect 까지 한 번에 — caller 가 inject 한 콜백을 호출.

    ``enqueue_slice`` 는 :class:`NextSliceDecision` 의 ``slice_spec`` 을
    받아 실제 coding_proposal / coding_execute job 을 enqueue. session done
    시 ``on_session_done`` 콜백을 부른다 — 일반적으로 workflow_state 의
    state 를 ``COMPLETED`` 로 마무리.
    """

    decision = decide_next_slice(
        session_id=session_id, session_extra=session_extra
    )
    if decision.action == NextSliceAction.SKIPPED_NOT_MERGED:
        return decision

    if decision.extra_updates:
        persist_extra(decision.extra_updates)

    if decision.action == NextSliceAction.DISPATCH_SLICE:
        if enqueue_slice is not None and decision.slice_spec is not None:
            enqueue_slice(session_id, decision.slice_spec)
    elif decision.action == NextSliceAction.SESSION_DONE:
        if on_session_done is not None:
            on_session_done(session_id)

    return decision


__all__ = (
    "EXTRA_CODING_BACKLOG",
    "EXTRA_SESSION_COMPLETED_REASON",
    "NextSliceAction",
    "NextSliceDecision",
    "decide_next_slice",
    "dispatch_next_coding_slice",
)
