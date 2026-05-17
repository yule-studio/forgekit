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
# P0-Y marker correctness: 이전엔 ``coding_dispatch_queued`` 가 coding_job
# stamp 시점에 찍혔는데, 실제 ``coding_execute`` queue row 는 그 이후
# dispatcher 가 enqueue 해야 생긴다. operator surface 가 "queued 됐다"
# 고 거짓말하는 회귀를 끊기 위해 두 단계로 분리:
#
#   * ``coding_job_ready`` — proposal → CodingJob(status=ready) 가
#     session.extra 에 stamp 된 시점. queue row 는 아직 없음.
#   * ``coding_dispatch_queued`` — ``dispatch_ready_coding_jobs`` 가
#     실제 queue row 를 만들고 ``coding_execute_dispatch`` marker 까지
#     찍은 시점. 이때만 operator 에게 "큐에 들어갔다" 고 말한다.
#
# 후속 단계 (``coding_in_progress`` / ``draft_pr_opened`` / ``coding_blocked``)
# 는 coding executor / operator_action 핸들러가 stamp.
PROGRESS_ISSUE_CREATED: str = "issue_created"
PROGRESS_CODING_JOB_READY: str = "coding_job_ready"
PROGRESS_CODING_DISPATCH_QUEUED: str = "coding_dispatch_queued"
PROGRESS_CODING_IN_PROGRESS: str = "coding_in_progress"
PROGRESS_DRAFT_PR_OPENED: str = "draft_pr_opened"
PROGRESS_CODING_BLOCKED: str = "coding_blocked"


PROGRESS_TIMELINE: tuple = (
    PROGRESS_ISSUE_CREATED,
    PROGRESS_CODING_JOB_READY,
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
    session_prompt: Optional[str] = None,
    session_id_for_proposal: Optional[str] = None,
    auto_rebuild_proposal: bool = True,
) -> ContinuationOutcome:
    """*session_extra* 를 pure 하게 갱신해 coding_job=ready 상태로 promote.

    return value 의 ``new_extra`` 를 caller 가 ``WorkflowSession.extra`` 에
    persist 한다. 본 함수 자체는 storage I/O 없음.

    idempotent 동작:
      - coding_job 이 이미 ready 이고 그 metadata 의 ``issue_number`` /
        ``approval_id`` 가 anchor 와 같으면 noop.
      - coding_proposal 이 session.extra 에 없고 *session_prompt* 가
        주어졌고 *auto_rebuild_proposal* True 면 즉석에서 재구성. 그렇지
        않으면 (옛 동작) noop.

    P0-X self-heal: slash command intake 가 (a) coding_proposal 을
    stamp 하기 전에 race 로 anchor 가 먼저 만들어졌거나, (b) intake
    경로 자체가 stamp 를 건너뛴 경우, 모두 continuation 단계에서 prompt
    만 있으면 자체적으로 회복한다. 동일 사고의 silent stall (라이브
    canonical session ``11917bf1e75d``) 재발 방지.

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

    # 2. coding_proposal 없으면 — P0-X 자동 재구성 시도 후에도 비어있을 때만 noop.
    proposal_payload = extra.get(SESSION_EXTRA_CODING_PROPOSAL_KEY)
    if not isinstance(proposal_payload, Mapping) or not proposal_payload:
        rebuilt = None
        if auto_rebuild_proposal:
            rebuilt = _rebuild_proposal_payload(
                prompt=session_prompt, session_id=session_id_for_proposal
            )
        if rebuilt is None:
            return ContinuationOutcome(
                promoted=False,
                coding_job=None,
                new_extra=extra,
                noop_reason=CONTINUATION_NOOP_NO_PROPOSAL,
                progress_markers=progress_markers,
            )
        extra[SESSION_EXTRA_CODING_PROPOSAL_KEY] = dict(rebuilt)
        proposal_payload = extra[SESSION_EXTRA_CODING_PROPOSAL_KEY]

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

    # 5. P0-Y marker correctness: 이 시점은 coding_job=ready stamp 만
    # 완료된 상태 — queue row 는 아직 ``dispatch_ready_coding_jobs`` 가
    # 만들지 않았다. 따라서 ``coding_job_ready`` 만 stamp 하고,
    # ``coding_dispatch_queued`` 는 dispatcher 가 실제 enqueue 한 뒤에
    # ``_persist_dispatch_marker`` 가 stamp 한다.
    progress_markers = _ensure_progress_marker(
        extra,
        marker=PROGRESS_CODING_JOB_READY,
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


def _rebuild_proposal_payload(
    *,
    prompt: Optional[str],
    session_id: Optional[str],
) -> Optional[Mapping[str, Any]]:
    """Rebuild a ``coding_proposal`` payload from *prompt* alone.

    SSoT 는 ``agents.coding.authorization.recommend_authorization`` —
    `_persist_coding_proposal` 가 stamp 하는 모양과 똑같이 serialise
    한다. 본 helper 는 storage 와 무관하게 pure dict 를 반환한다.

    Returns None when:
      * prompt 가 비어있어 의미 있는 proposal 을 만들 수 없을 때.
      * import / build 실패 (partial install 등).
    """

    text = str(prompt or "").strip()
    if not text:
        return None
    try:
        from ..coding.authorization import recommend_authorization
    except Exception:  # noqa: BLE001 - partial install
        return None
    try:
        proposal = recommend_authorization(
            user_request=text, session_id=session_id
        )
    except Exception:  # noqa: BLE001
        return None
    if proposal is None:
        return None
    return {
        "session_id": proposal.session_id,
        "user_request": proposal.user_request,
        "executor_role": proposal.executor_role,
        "review_roles": list(proposal.review_roles),
        "participant_roles": list(proposal.participant_roles),
        "write_scope": list(proposal.write_scope),
        "forbidden_scope": list(proposal.forbidden_scope),
        "reason": proposal.reason,
        "safety_rules": list(proposal.safety_rules),
        "approval_required": bool(proposal.approval_required),
        "metadata": {**dict(proposal.metadata), "rebuilt_by": "continuation_self_heal"},
        "lifecycle_mode": proposal.lifecycle_mode,
        "research_leads": list(proposal.research_leads),
    }


# ---------------------------------------------------------------------------
# Repair helper — 잘못 분류돼 멈춘 session 의 coding_proposal 재구성
# ---------------------------------------------------------------------------


REPAIR_OUTCOME_REPAIRED: str = "repaired"
REPAIR_OUTCOME_NO_SESSION: str = "session_not_found"
REPAIR_OUTCOME_NO_ANCHOR: str = "no_github_work_order_anchor"
REPAIR_OUTCOME_NO_PROMPT: str = "session_prompt_empty"
REPAIR_OUTCOME_PROPOSAL_BUILD_FAILED: str = "proposal_build_failed"
REPAIR_OUTCOME_ALREADY_READY: str = "coding_job_already_ready"


@dataclass(frozen=True)
class SessionRepairOutcome:
    """:func:`repair_session_for_coding_dispatch` 결과.

    ``outcome`` 은 ``REPAIR_OUTCOME_*`` 상수 중 하나.
    ``coding_proposal_rebuilt`` True 면 본 호출이 proposal 을 새로
    persist 했다는 뜻. ``promoted`` True 면 추가로 coding_job=ready
    로 promote 됐다는 뜻 — 둘은 독립적이다 (proposal 만 rebuild 하고
    promote 는 idempotent noop 일 수 있음).
    """

    outcome: str
    coding_proposal_rebuilt: bool = False
    task_type_reclassified: bool = False
    promoted: bool = False
    continuation: Optional[ContinuationOutcome] = None
    detail: Mapping[str, Any] = None  # type: ignore[assignment]


def repair_session_for_coding_dispatch(
    *,
    session_id: str,
    load_session_fn,
    update_session_fn,
    now: Optional[datetime] = None,
    reclassify_task_type: bool = True,
) -> SessionRepairOutcome:
    """Repair a session that already has an issue anchor but can't
    advance to ``coding_execute``.

    Live live-smoke scenario (canonical session ``11917bf1e75d``):

      * ``session.task_type`` 가 ``qa-test`` 로 잘못 분류돼 있고
      * ``session.extra['coding_proposal']`` 가 비어있고
      * ``session.extra['github_work_order_issue']`` 가 issue 1 anchor 를
        들고 있음

      → executor 가 anchor 까지 만들었지만 continuation 이
      ``CONTINUATION_NOOP_NO_PROPOSAL`` 로 멈춘 상태.

    Repair steps:

      1. *load_session_fn(session_id)* → session
      2. anchor 가 없으면 ``REPAIR_OUTCOME_NO_ANCHOR`` 로 종료.
      3. prompt 가 비어있으면 ``REPAIR_OUTCOME_NO_PROMPT`` 로 종료.
      4. ``coding_proposal`` 가 없으면 :func:`recommend_authorization` 로
         재구성. *reclassify_task_type* True 면 dispatcher.classify 로
         task_type / executor_role 도 재정렬.
      5. session 갱신 후 :func:`promote_session_to_coding_ready` 호출.
         (anchor 가 이미 있으므로 같은 anchor 로 promote — idempotent.)

    Caller (CLI / repair script) 가 load/update injection 으로 storage
    선택. production 은 ``workflow_state.load_session`` + ``update_session``.
    """

    session = load_session_fn(session_id)
    if session is None:
        return SessionRepairOutcome(
            outcome=REPAIR_OUTCOME_NO_SESSION,
            detail={"session_id": session_id},
        )

    extra = dict(getattr(session, "extra", None) or {})
    anchor = extra.get("github_work_order_issue")
    if not isinstance(anchor, Mapping) or not _coerce_int(
        anchor.get("issue_number")
    ):
        return SessionRepairOutcome(
            outcome=REPAIR_OUTCOME_NO_ANCHOR,
            detail={"session_id": session_id},
        )

    prompt = str(getattr(session, "prompt", "") or "").strip()
    if not prompt:
        return SessionRepairOutcome(
            outcome=REPAIR_OUTCOME_NO_PROMPT,
            detail={"session_id": session_id},
        )

    coding_proposal_rebuilt = False
    task_type_reclassified = False

    existing_proposal = extra.get(SESSION_EXTRA_CODING_PROPOSAL_KEY)
    if not isinstance(existing_proposal, Mapping) or not existing_proposal:
        try:
            from ..coding.authorization import recommend_authorization
        except Exception as exc:  # noqa: BLE001 - partial install
            return SessionRepairOutcome(
                outcome=REPAIR_OUTCOME_PROPOSAL_BUILD_FAILED,
                detail={"reason": f"import_failed:{type(exc).__name__}"},
            )
        try:
            proposal = recommend_authorization(
                user_request=prompt, session_id=session_id
            )
        except Exception as exc:  # noqa: BLE001
            return SessionRepairOutcome(
                outcome=REPAIR_OUTCOME_PROPOSAL_BUILD_FAILED,
                detail={"reason": str(exc)},
            )
        try:
            from ...discord.engineering_channel_router.session_persistence import (
                _proposal_to_dict,
            )
        except Exception:  # noqa: BLE001
            _proposal_to_dict = None  # type: ignore[assignment]
        if _proposal_to_dict is None:
            return SessionRepairOutcome(
                outcome=REPAIR_OUTCOME_PROPOSAL_BUILD_FAILED,
                detail={"reason": "proposal_serializer_missing"},
            )
        extra[SESSION_EXTRA_CODING_PROPOSAL_KEY] = dict(
            _proposal_to_dict(proposal)
        )
        coding_proposal_rebuilt = True

    # Optional reclassification — pure (no side-effect 외에 task_type 갱신)
    new_task_type: Optional[str] = None
    new_executor_role: Optional[str] = None
    if reclassify_task_type:
        classified = None
        try:
            from ..messaging.dispatcher import (
                DispatchRequest,
                Dispatcher,
                TASK_EXECUTOR_ROLE,
            )
            from ..messaging.registry import ParticipantsPool

            dispatcher = Dispatcher(
                ParticipantsPool(
                    agent_id="engineering-agent", runners={}, warnings=()
                )
            )
            request = DispatchRequest(
                prompt=prompt, task_type=None, write_requested=True
            )
            classified = dispatcher.classify(request)
        except Exception:  # noqa: BLE001 - reclassification is best-effort
            classified = None
        if classified is not None:
            new_task_type = classified.value
            new_executor_role = TASK_EXECUTOR_ROLE.get(classified)
            current_task_type = getattr(session, "task_type", None)
            if (
                current_task_type != new_task_type
                or getattr(session, "executor_role", None) != new_executor_role
            ):
                task_type_reclassified = True

    # Apply updates to session: extra + (optionally) task_type / executor_role
    try:
        from dataclasses import replace as _replace
    except Exception:  # noqa: BLE001
        _replace = None  # type: ignore[assignment]

    updated = session
    fields_to_replace: dict = {"extra": extra}
    if task_type_reclassified:
        if new_task_type is not None:
            fields_to_replace["task_type"] = new_task_type
        if new_executor_role is not None:
            fields_to_replace["executor_role"] = new_executor_role

    # P0-Y session normalization — stale qa-engineer block reason 등 옛
    # 분류의 흔적이 남아있으면 operator surface 가 헷갈린다. anchor +
    # coding_proposal 까지 도달했다는 사실 자체가 "write 가 사실상 승인
    # 된" 상태이므로 write_blocked_reason 도 비운다.
    current_block_reason = (
        str(getattr(session, "write_blocked_reason", "") or "")
    )
    stale_qa_marker = (
        "qa-engineer" in current_block_reason
        or "qa_engineer" in current_block_reason
    )
    needs_block_reason_clear = bool(current_block_reason) and (
        stale_qa_marker
        or task_type_reclassified  # 재분류됐으면 옛 reason 는 stale
    )
    if needs_block_reason_clear:
        fields_to_replace["write_blocked_reason"] = None
    if _replace is not None:
        try:
            updated = _replace(session, **fields_to_replace)
        except TypeError:
            # Non-dataclass stub — apply in-place
            for key, value in fields_to_replace.items():
                try:
                    setattr(updated, key, value)
                except Exception:  # noqa: BLE001
                    pass
    update_session_fn(updated, extra)

    # 4. promote_session_to_coding_ready — anchor 가 이미 있으므로 같은
    # anchor 로 promote (idempotent — 이미 ready 면 noop)
    continuation = promote_session_to_coding_ready(
        session_extra=extra,
        anchor=anchor,
        repo=str(anchor.get("repo") or "") or None,
        base_branch=None,
        dry_run=bool(anchor.get("dry_run", True)),
        approval_id=anchor.get("approval_id"),
        approved_by=anchor.get("approved_by"),
        approved_at=anchor.get("approved_at"),
        now=now,
    )
    if continuation.promoted and continuation.new_extra is not None:
        # 두 번째 update — coding_job 까지 stamp. WorkflowSession 처럼
        # frozen 인 경우 ``_replace`` 가 새 인스턴스를 만드는데, 그 과정
        # 에서 위에서 in-place setattr 로 채운 task_type/executor_role 가
        # dropped 되지 않도록 둘 다 다시 명시 전달.
        new_extra_dict = dict(continuation.new_extra)
        second_fields: dict = {"extra": new_extra_dict}
        if task_type_reclassified:
            if new_task_type is not None:
                second_fields["task_type"] = new_task_type
            if new_executor_role is not None:
                second_fields["executor_role"] = new_executor_role
        if needs_block_reason_clear:
            second_fields["write_blocked_reason"] = None
        updated_after = updated
        if _replace is not None:
            try:
                updated_after = _replace(updated, **second_fields)
            except TypeError:
                for key, value in second_fields.items():
                    try:
                        setattr(updated, key, value)
                    except Exception:  # noqa: BLE001
                        pass
                updated_after = updated
        update_session_fn(updated_after, new_extra_dict)

    return SessionRepairOutcome(
        outcome=REPAIR_OUTCOME_REPAIRED,
        coding_proposal_rebuilt=coding_proposal_rebuilt,
        task_type_reclassified=task_type_reclassified,
        promoted=bool(continuation.promoted),
        continuation=continuation,
        detail={
            "session_id": session_id,
            "anchor_issue_number": _coerce_int(anchor.get("issue_number")),
            "new_task_type": new_task_type,
            "new_executor_role": new_executor_role,
            "continuation_noop_reason": continuation.noop_reason,
        },
    )


__all__ = (
    "CONTINUATION_NOOP_ALREADY_READY",
    "CONTINUATION_NOOP_BUILD_FAILED",
    "CONTINUATION_NOOP_NO_PROPOSAL",
    "ContinuationOutcome",
    "PROGRESS_CODING_BLOCKED",
    "PROGRESS_CODING_DISPATCH_QUEUED",
    "PROGRESS_CODING_IN_PROGRESS",
    "PROGRESS_CODING_JOB_READY",
    "PROGRESS_DRAFT_PR_OPENED",
    "PROGRESS_ISSUE_CREATED",
    "PROGRESS_TIMELINE",
    "REPAIR_OUTCOME_ALREADY_READY",
    "REPAIR_OUTCOME_NO_ANCHOR",
    "REPAIR_OUTCOME_NO_PROMPT",
    "REPAIR_OUTCOME_NO_SESSION",
    "REPAIR_OUTCOME_PROPOSAL_BUILD_FAILED",
    "REPAIR_OUTCOME_REPAIRED",
    "SESSION_EXTRA_CODING_JOB_KEY",
    "SESSION_EXTRA_CODING_PROPOSAL_KEY",
    "SESSION_EXTRA_PROGRESS_KEY",
    "SessionRepairOutcome",
    "promote_session_to_coding_ready",
    "repair_session_for_coding_dispatch",
    "stamp_progress_marker",
)
