"""GitHub work_order → coding_execute continuation — P0-S 종단 마지막 다리.

배경
====
PR #168 까지 닫힌 흐름:

  Discord intake → approval card → 승인 → GitHubWorkOrderWorker → issue
  create → ``session.extra["github_work_order_issue"]`` anchor stamp.

여기서 멈추면 "issue 는 생겼는데 PR 이 안 나옴" 상태. 본 모듈이
**anchor → coding_job=ready** continuation 을 담당한다.

흐름
====
:func:`promote_session_to_coding_ready` 가 호출되면:

1. session.extra 의 ``coding_proposal`` (engineering channel router 가
   `_run_coding_authorization_gate` 에서 stamp 한 값) 을 읽는다.
2. :func:`build_coding_job_from_proposal` 로 CodingJob 빌드.
3. metadata 에 anchor 정보 (issue_number / issue_url / repo_full_name /
   base_branch / dry_run / approval_id / approved_at) 를 stamp.
4. status=ready 로 설정.
5. session.extra["coding_job"] 가 이미 ready 이고 anchor 가 같으면 noop.

이렇게 stamp 된 ready coding_job 은 기존 :func:`iter_ready_coding_jobs`
가 자연스럽게 pick up — operator 가 추가 입력을 안 해도 dispatcher 가
coding_execute 큐로 enqueue 한다.

continuation marker
-------------------
``session.extra["github_work_order_progress"]`` 에 다음 단계 marker 가
누적된다:

  - ``issue_created`` — issue create 직후 (auto / existing 모두)
  - ``coding_dispatch_queued`` — coding_job=ready 로 promote 됐을 때
  - ``coding_in_progress`` — coding_executor 가 처리 시작 (이후 PR)
  - ``draft_pr_opened`` — draft PR 생성 완료 (이후 PR)
  - ``coding_blocked`` — operator action / 외부 요인으로 멈춤

본 모듈은 ``issue_created`` 와 ``coding_dispatch_queued`` 만 stamp 한다.
``in_progress`` / ``draft_pr_opened`` / ``blocked`` 는 coding executor /
operator-action 핸들러가 stamp 한다.

idempotency
-----------
- 같은 anchor 로 두 번 호출되면 두 번째는 noop 반환.
- coding_proposal 이 없으면 noop ("Discord intake 측에서 proposal 을
  stamp 안 한 비표준 진입" — operator 가 확인 가능하도록 audit 만 남김).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from ..coding.authorization import proposal_from_dict
from ..coding.job import (
    STATUS_READY,
    build_coding_job_from_proposal,
)


logger = logging.getLogger(__name__)


SESSION_EXTRA_CODING_JOB_KEY: str = "coding_job"
SESSION_EXTRA_CODING_PROPOSAL_KEY: str = "coding_proposal"
SESSION_EXTRA_PROGRESS_KEY: str = "github_work_order_progress"


# Progress markers — operator surface 에서 "어디까지 갔는지" 즉시 보이는 키.
# 본 모듈은 처음 2 종만 stamp. 나머지는 후속 단계 (coding_executor,
# operator_action_reply) 가 stamp.
PROGRESS_ISSUE_CREATED: str = "issue_created"
PROGRESS_CODING_DISPATCH_QUEUED: str = "coding_dispatch_queued"
PROGRESS_CODING_IN_PROGRESS: str = "coding_in_progress"
PROGRESS_DRAFT_PR_OPENED: str = "draft_pr_opened"
PROGRESS_CODING_BLOCKED: str = "coding_blocked"


PROGRESS_TIMELINE: tuple = (
    PROGRESS_ISSUE_CREATED,
    PROGRESS_CODING_DISPATCH_QUEUED,
    PROGRESS_CODING_IN_PROGRESS,
    PROGRESS_DRAFT_PR_OPENED,
)


CONTINUATION_NOOP_NO_PROPOSAL: str = "no_coding_proposal"
CONTINUATION_NOOP_ALREADY_READY: str = "coding_job_already_ready_same_anchor"
CONTINUATION_NOOP_BUILD_FAILED: str = "coding_job_build_failed"


@dataclass(frozen=True)
class ContinuationOutcome:
    """:func:`promote_session_to_coding_ready` 결과.

    ``coding_job`` 이 None 이면 promote 가 일어나지 않은 경우 (already
    ready / no_proposal / build_failed). ``promoted`` True 면 session.extra
    가 갱신되었음을 의미.
    """

    promoted: bool
    coding_job: Optional[Mapping[str, Any]] = None
    new_extra: Optional[Mapping[str, Any]] = None
    noop_reason: Optional[str] = None
    progress_markers: tuple = ()


def promote_session_to_coding_ready(
    *,
    session_extra: Mapping[str, Any],
    anchor: Mapping[str, Any],
    repo: Optional[str],
    base_branch: Optional[str],
    dry_run: bool,
    approval_id: Optional[str] = None,
    approved_by: Optional[str] = None,
    approved_at: Optional[str] = None,
    now: Optional[datetime] = None,
) -> ContinuationOutcome:
    """*session_extra* 를 pure 하게 갱신해 coding_job=ready 상태로 promote.

    return value 의 ``new_extra`` 를 caller 가 ``WorkflowSession.extra`` 에
    persist 한다. 본 함수 자체는 storage I/O 없음.

    idempotent 동작:
      - coding_job 이 이미 ready 이고 그 metadata 의 ``issue_number`` /
        ``approval_id`` 가 anchor 와 같으면 noop.
      - coding_proposal 이 session.extra 에 없으면 noop ("비표준 진입").

    progress markers:
      - 항상 ``issue_created`` 를 추가 (anchor 가 존재한다는 사실 자체).
      - promote 성공 시 ``coding_dispatch_queued`` 도 추가.
    """

    extra = dict(session_extra or {})
    anchor_issue_number = _coerce_int(anchor.get("issue_number"))

    # 1. progress marker — issue_created 는 anchor 가 있으면 무조건
    progress_markers = _ensure_progress_marker(
        extra,
        marker=PROGRESS_ISSUE_CREATED,
        at=_now_iso(now),
        detail={
            "issue_number": anchor_issue_number,
            "html_url": anchor.get("html_url"),
            "created_via": anchor.get("created_via"),
        },
    )

    # 2. coding_proposal 없으면 promote 불가 — issue_created marker 만 반환
    proposal_payload = extra.get(SESSION_EXTRA_CODING_PROPOSAL_KEY)
    if not isinstance(proposal_payload, Mapping) or not proposal_payload:
        return ContinuationOutcome(
            promoted=False,
            coding_job=None,
            new_extra=extra,
            noop_reason=CONTINUATION_NOOP_NO_PROPOSAL,
            progress_markers=progress_markers,
        )

    # 3. 같은 anchor 로 이미 ready 인지 확인
    existing_job = extra.get(SESSION_EXTRA_CODING_JOB_KEY)
    if (
        isinstance(existing_job, Mapping)
        and str(existing_job.get("status") or "").lower() == STATUS_READY
    ):
        existing_metadata = existing_job.get("metadata") or {}
        existing_anchor_issue = (
            existing_metadata.get("issue_number")
            if isinstance(existing_metadata, Mapping)
            else None
        )
        if (
            anchor_issue_number is not None
            and _coerce_int(existing_anchor_issue) == anchor_issue_number
        ):
            return ContinuationOutcome(
                promoted=False,
                coding_job=existing_job,
                new_extra=extra,
                noop_reason=CONTINUATION_NOOP_ALREADY_READY,
                progress_markers=progress_markers,
            )

    # 4. proposal → CodingJob (status=ready) + anchor metadata stamp
    try:
        proposal = proposal_from_dict(proposal_payload)
        approved_at_dt = _coerce_dt(approved_at) or _coerce_dt(_now_iso(now))
        job = build_coding_job_from_proposal(
            proposal,
            status=STATUS_READY,
            approved_at=approved_at_dt,
            now=now,
        )
    except Exception:  # noqa: BLE001 - keep continuation best-effort
        logger.warning(
            "promote_session_to_coding_ready: failed to build CodingJob — "
            "anchor stamp 만 남기고 promote 건너뜀",
            exc_info=True,
        )
        return ContinuationOutcome(
            promoted=False,
            coding_job=None,
            new_extra=extra,
            noop_reason=CONTINUATION_NOOP_BUILD_FAILED,
            progress_markers=progress_markers,
        )

    job_payload = dict(job.to_dict())
    metadata = dict(job_payload.get("metadata") or {})
    if anchor_issue_number is not None:
        metadata["issue_number"] = anchor_issue_number
    issue_url = anchor.get("html_url")
    if issue_url:
        metadata["issue_url"] = str(issue_url)
    if repo:
        metadata["repo_full_name"] = str(repo)
    if base_branch:
        metadata["base_branch"] = str(base_branch)
    metadata["dry_run"] = bool(dry_run)
    if approval_id:
        metadata["approval_id"] = str(approval_id)
    if approved_by:
        metadata["approved_by"] = str(approved_by)
    if approved_at:
        metadata["approved_at"] = str(approved_at)
    # work order anchor 자체를 보존 — executor / 후속 단계가 audit 가능
    metadata["github_work_order_anchor"] = dict(anchor)
    job_payload["metadata"] = metadata

    extra[SESSION_EXTRA_CODING_JOB_KEY] = job_payload

    # 5. 두 번째 progress marker
    progress_markers = _ensure_progress_marker(
        extra,
        marker=PROGRESS_CODING_DISPATCH_QUEUED,
        at=_now_iso(now),
        detail={
            "issue_number": anchor_issue_number,
            "executor_role": job_payload.get("executor_role"),
            "repo_full_name": repo,
        },
    )

    return ContinuationOutcome(
        promoted=True,
        coding_job=job_payload,
        new_extra=extra,
        noop_reason=None,
        progress_markers=progress_markers,
    )


def stamp_progress_marker(
    *,
    session_extra: Mapping[str, Any],
    marker: str,
    at: Optional[str] = None,
    detail: Optional[Mapping[str, Any]] = None,
) -> Mapping[str, Any]:
    """``session.extra`` 에 progress marker 한 줄 추가하고 새 dict 반환.

    이미 같은 marker 가 있으면 detail 만 갱신 (timestamp 는 유지) — 같은
    단계가 두 번 발생하지 않은 것으로 본다 (idempotent).

    public helper — coding_executor / operator_action 측에서도 호출.
    """

    extra = dict(session_extra or {})
    _ensure_progress_marker(
        extra, marker=marker, at=at or _now_iso(None), detail=dict(detail or {})
    )
    return extra


def _ensure_progress_marker(
    extra: dict,
    *,
    marker: str,
    at: str,
    detail: Mapping[str, Any],
) -> tuple:
    bucket = extra.get(SESSION_EXTRA_PROGRESS_KEY)
    if not isinstance(bucket, Mapping):
        bucket = {}
    bucket = dict(bucket)
    existing = bucket.get(marker)
    entry = {
        "at": existing.get("at") if isinstance(existing, Mapping) and existing.get("at") else at,
        "detail": {k: v for k, v in (detail or {}).items() if v is not None},
    }
    bucket[marker] = entry
    extra[SESSION_EXTRA_PROGRESS_KEY] = bucket
    return tuple(bucket.keys())


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        text = str(value)
        # GitHub-style ISO with Z suffix
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except Exception:  # noqa: BLE001
        return None


def _now_iso(now: Optional[datetime]) -> str:
    dt = now or datetime.now(tz=timezone.utc)
    return dt.replace(microsecond=0).isoformat()


__all__ = (
    "CONTINUATION_NOOP_ALREADY_READY",
    "CONTINUATION_NOOP_BUILD_FAILED",
    "CONTINUATION_NOOP_NO_PROPOSAL",
    "ContinuationOutcome",
    "PROGRESS_CODING_BLOCKED",
    "PROGRESS_CODING_DISPATCH_QUEUED",
    "PROGRESS_CODING_IN_PROGRESS",
    "PROGRESS_DRAFT_PR_OPENED",
    "PROGRESS_ISSUE_CREATED",
    "PROGRESS_TIMELINE",
    "SESSION_EXTRA_CODING_JOB_KEY",
    "SESSION_EXTRA_CODING_PROPOSAL_KEY",
    "SESSION_EXTRA_PROGRESS_KEY",
    "promote_session_to_coding_ready",
    "stamp_progress_marker",
)
