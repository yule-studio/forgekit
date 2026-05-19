"""P1-Z4 C — anchor stamp 이후 tracking_validation 재평가 helper.

배경
----
``tracking_validation`` 은 intake 시점에 ``session_persistence`` 에서
한 번 계산되어 ``session.extra`` 에 stamp 된 뒤 갱신되지 않았다.
이후 anchor (``github_work_order_issue``) 가 생기거나 coding_job 이
ready 로 promote 돼도 ``tracking_validation.status = "needs_issue"`` 가
stale 하게 남아 operator surface 가 "아직 issue 없어 멈춤" 처럼 보임.

canonical session ``000f13fb121b`` 가 직접 사례: anchor stamp 됐고
coding_execute 까지 dispatch 됐는데 tracking_validation 은 여전히
needs_issue.

본 모듈
========
* :func:`refresh_tracking_validation(session) -> RefreshResult` — pure
  helper.  ``session.extra`` 를 다시 읽어 ``validate_tracking_chain``
  으로 재평가, 결과를 dict 로 반환.  caller (worker / dispatcher) 가
  ``session.extra`` 에 stamp + persist.
* :func:`apply_tracking_refresh(session, *, update_session_fn)` —
  편의 wrapper.  실제 storage 갱신까지 처리.

trigger 지점 (caller 가 본 helper 호출):
  - github_work_order_executor 가 anchor stamp 직후
  - work_order_coding_continuation 가 coding_job ready promote 직후
  - coding_execute_dispatcher 가 dispatch marker stamp 직후
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional


logger = logging.getLogger(__name__)


SESSION_EXTRA_TRACKING_KEY: str = "tracking_validation"
SESSION_EXTRA_TRACKING_REFRESH_AUDIT_KEY: str = "tracking_validation_refresh_audit"


@dataclass(frozen=True)
class RefreshResult:
    """:func:`refresh_tracking_validation` 결과.

    ``changed`` 가 True 면 status 가 실제로 바뀜 (e.g., needs_issue → ok).
    caller 가 audit 에 stamp 할 source 정보를 ``triggered_by`` 로 받는다.
    """

    changed: bool
    previous_status: Optional[str]
    new_status: Optional[str]
    new_validation: Mapping[str, Any]
    triggered_by: str


def refresh_tracking_validation(
    *,
    session: Any,
    triggered_by: str,
) -> RefreshResult:
    """Pure — session.extra 를 다시 평가해 갱신된 tracking_validation 반환.

    storage I/O 없음.  caller 가 결과를 ``session.extra`` 에 stamp + persist.

    *triggered_by* 는 caller 식별자 (예: ``"anchor_stamp"`` /
    ``"coding_job_ready"`` / ``"dispatch_marker"``) — operator audit 에 노출.
    """

    extra_raw = getattr(session, "extra", None)
    if not isinstance(extra_raw, Mapping):
        return RefreshResult(
            changed=False,
            previous_status=None,
            new_status=None,
            new_validation={},
            triggered_by=triggered_by,
        )

    from .tracking_enforcement import validate_tracking_chain

    previous = extra_raw.get(SESSION_EXTRA_TRACKING_KEY)
    previous_status = None
    if isinstance(previous, Mapping):
        previous_status = previous.get("status")

    new_validation_obj = validate_tracking_chain(extra_raw)
    new_validation = dict(new_validation_obj.to_dict())
    new_status = new_validation.get("status")
    changed = previous_status != new_status
    return RefreshResult(
        changed=changed,
        previous_status=previous_status,
        new_status=new_status,
        new_validation=new_validation,
        triggered_by=triggered_by,
    )


def apply_tracking_refresh(
    *,
    session: Any,
    triggered_by: str,
    update_session_fn: Optional[Callable[..., Any]] = None,
    now: Optional[datetime] = None,
) -> Optional[RefreshResult]:
    """편의 wrapper — refresh 결과를 session.extra 에 stamp + persist.

    *update_session_fn* 이 주어지지 않으면 ``agents.workflow_state.update_session``
    lazy import.  storage 실패는 warning 만 log — caller 흐름 안 깨뜨림.
    """

    result = refresh_tracking_validation(session=session, triggered_by=triggered_by)
    if not result.new_validation:
        return result

    extra_raw = getattr(session, "extra", None)
    extra = dict(extra_raw or {})
    extra[SESSION_EXTRA_TRACKING_KEY] = dict(result.new_validation)

    when = (now or datetime.now(tz=timezone.utc)).replace(microsecond=0).isoformat()
    audit_bucket = extra.get(SESSION_EXTRA_TRACKING_REFRESH_AUDIT_KEY)
    if not isinstance(audit_bucket, list):
        audit_bucket = []
    audit_bucket.append(
        {
            "triggered_by": triggered_by,
            "previous_status": result.previous_status,
            "new_status": result.new_status,
            "at": when,
        }
    )
    # bound — last 10 entries 만 보관 (operator surface 가 작아지도록)
    if len(audit_bucket) > 10:
        audit_bucket = audit_bucket[-10:]
    extra[SESSION_EXTRA_TRACKING_REFRESH_AUDIT_KEY] = audit_bucket

    try:
        from dataclasses import replace as _replace

        updated = _replace(session, extra=extra)
    except Exception:  # noqa: BLE001
        return result

    persist = update_session_fn
    if persist is None:
        try:
            from ..workflow_state import update_session as _update

            persist = _update
        except Exception:  # noqa: BLE001
            return result
    try:
        persist(updated, now=(now or datetime.now(tz=timezone.utc)))
    except Exception:  # noqa: BLE001
        logger.warning(
            "apply_tracking_refresh: persist failed (triggered_by=%s)",
            triggered_by,
            exc_info=True,
        )
    return result


__all__ = (
    "RefreshResult",
    "SESSION_EXTRA_TRACKING_KEY",
    "SESSION_EXTRA_TRACKING_REFRESH_AUDIT_KEY",
    "apply_tracking_refresh",
    "refresh_tracking_validation",
)
