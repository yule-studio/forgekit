"""P1-L-2 — ``pr_merge_pending`` 소비자 (background loop).

``coding_executor_worker`` 가 draft PR open 직후 ``pr_merge_stage =
pr_merge_pending`` 을 session.extra 에 stamp 한다.  본 worker 는 그
stage 를 실제로 소비한다 — work_mode 에 따라 분기:

  * ``approval_required`` → :func:`enqueue_pr_merge_approval` 호출해서
    ``#승인-대기`` 카드를 한 번만 게시. ``audit`` 에
    ``event=approval_card_enqueued`` event 한 줄 남겨서 중복 enqueue 방지.
    stage 는 ``pr_merge_pending`` 유지 (사용자 reply 가 reply_router 를
    통해 들어와야 ``pr_merge_approved`` 로 advance).

  * ``autonomous_merge`` → ``merge_executor`` 호출 — gate fail / merge
    disabled 면 ``pr_merge_blocked`` 로 advance, merge_sha 가 있으면
    ``pr_merged`` 로 advance. ``pr_merged`` 진입 시 next slice
    dispatcher 도 호출해서 backlog 가 있으면 다음 coding job enqueue.

본 모듈은 GitHub 호출 / Discord 호출을 **하지 않는다**. 모든 side-effect 는
caller 가 inject 하는 ``approval_worker`` / ``merge_executor`` /
``next_slice_dispatcher`` 를 통해서만 발생 — 테스트 가능성 유지.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, List, Mapping, Optional, Sequence

from ..lifecycle.session_mode import (
    WORK_MODE_APPROVAL,
    WORK_MODE_AUTONOMOUS,
)
from .pr_approval import (
    APPROVAL_KIND_PR_MERGE,
    PRMergeExecutor,
    PRMergeProposal,
    PRMergeReplyDispatch,
)
from .pr_merge_continuation import (
    EXTRA_PR_MERGE_AUDIT,
    EXTRA_PR_MERGE_BASE_BRANCH,
    EXTRA_PR_MERGE_HEAD_SHA,
    EXTRA_PR_MERGE_PR_NUMBER,
    EXTRA_PR_MERGE_PR_URL,
    EXTRA_PR_MERGE_REPO,
    EXTRA_PR_MERGE_STAGE,
    STAGE_PR_MERGE_BLOCKED,
    STAGE_PR_MERGE_PENDING,
    STAGE_PR_MERGED,
    advance_stage,
    is_pending_approval_card,
    is_pending_autonomous_merge,
    is_pending_continuation,
    resolve_work_mode,
)


logger = logging.getLogger(__name__)


# Caller 가 inject 하는 next-slice 콜백 시그니처 — merge 성공 직후
# backlog 가 남아있으면 다음 coding job 을 enqueue. 없으면 session done
# 으로 마감. 콜백은 sync 또는 async 둘 다 OK.
NextSliceDispatcher = Callable[[str, Mapping[str, Any]], Any]


# Async approval enqueuer — discord adapter 의 :func:`enqueue_pr_merge_approval`
# 시그니처와 호환. 테스트는 fake 콜백 inject 가능.
ApprovalEnqueuer = Callable[
    ..., Awaitable[Any]
]


# action 결과 token
ACTION_SKIPPED_NOT_PENDING: str = "not_pending"
ACTION_SKIPPED_ALREADY_ENQUEUED: str = "approval_card_already_enqueued"
ACTION_APPROVAL_CARD_ENQUEUED: str = "approval_card_enqueued"
ACTION_AUTONOMOUS_MERGE_BLOCKED: str = "autonomous_merge_blocked"
ACTION_AUTONOMOUS_MERGE_SUCCEEDED: str = "autonomous_merge_succeeded"
ACTION_SKIPPED_NO_EXECUTOR: str = "no_executor_wired"
ACTION_SKIPPED_NO_APPROVAL_WORKER: str = "no_approval_worker_wired"


@dataclass(frozen=True)
class PRMergeContinuationOutcome:
    """한 세션에 대해 sweep tick 이 무엇을 했는지."""

    session_id: str
    action: str
    work_mode: str
    new_stage: Optional[str] = None
    reason: Optional[str] = None
    approval_job_id: Optional[str] = None
    merge_sha: Optional[str] = None
    extra_audit_fields: Mapping[str, Any] = field(default_factory=dict)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _truncate(text: str, *, limit: int = 240) -> str:
    """audit field 용 짧은 message — operator log 에 한 줄로 들어가게."""

    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _proposal_from_session_extra(
    session_extra: Mapping[str, Any],
    *,
    requested_by: str = "auto-continuation",
) -> Optional[PRMergeProposal]:
    """session.extra 에 stamp 된 PR 메타로 :class:`PRMergeProposal` 빌드.

    PR 메타가 부족하면 None — caller 는 skip 하고 audit 만 남긴다. 라이브
    GitHub state (check runs / mergeable_state) 는 :class:`PRMergeExecutor`
    가 호출 시점에 다시 fetch 하므로 여기서는 placeholder 만 채운다.
    """

    pr_number = session_extra.get(EXTRA_PR_MERGE_PR_NUMBER)
    repo = session_extra.get(EXTRA_PR_MERGE_REPO)
    pr_url = session_extra.get(EXTRA_PR_MERGE_PR_URL)
    head_sha = session_extra.get(EXTRA_PR_MERGE_HEAD_SHA)
    base_branch = session_extra.get(EXTRA_PR_MERGE_BASE_BRANCH)
    if not pr_number or not repo or not pr_url:
        return None
    return PRMergeProposal(
        repo=str(repo),
        pr_number=int(pr_number),
        pr_title="",
        pr_url=str(pr_url),
        head_sha=str(head_sha or ""),
        base_branch=str(base_branch or "main"),
        draft=True,
        mergeable_state="unknown",
        summary_md="",
        requested_by=requested_by,
    )


async def advance_pending_session(
    *,
    session_id: str,
    session_extra: Mapping[str, Any],
    persist_extra: Callable[[Mapping[str, Any]], None],
    approval_enqueuer: Optional[ApprovalEnqueuer] = None,
    merge_executor: Optional[PRMergeExecutor] = None,
    next_slice_dispatcher: Optional[NextSliceDispatcher] = None,
    approval_session_obj: Any = None,
) -> PRMergeContinuationOutcome:
    """한 세션의 ``pr_merge_pending`` 을 한 단계 진행.

    ``persist_extra`` 는 caller (loop runner) 가 inject 하는 콜백 —
    workflow_state.update_session 같은 store layer 를 호출해 새 dict 를
    persist 한다. 본 함수는 dict 만 만들고 store 는 안 건드림.

    ``approval_session_obj`` 는 :func:`enqueue_pr_merge_approval` 에
    넘길 session-like 객체 (session_id attribute 또는 dict). approval
    path 일 때만 사용.
    """

    work_mode = resolve_work_mode(session_extra)

    if not is_pending_continuation(session_extra):
        return PRMergeContinuationOutcome(
            session_id=session_id,
            action=ACTION_SKIPPED_NOT_PENDING,
            work_mode=work_mode,
        )

    # approval_required 경로
    if work_mode == WORK_MODE_APPROVAL:
        if not is_pending_approval_card(session_extra):
            return PRMergeContinuationOutcome(
                session_id=session_id,
                action=ACTION_SKIPPED_ALREADY_ENQUEUED,
                work_mode=work_mode,
            )
        if approval_enqueuer is None:
            return PRMergeContinuationOutcome(
                session_id=session_id,
                action=ACTION_SKIPPED_NO_APPROVAL_WORKER,
                work_mode=work_mode,
            )
        proposal = _proposal_from_session_extra(session_extra)
        if proposal is None:
            return PRMergeContinuationOutcome(
                session_id=session_id,
                action=ACTION_SKIPPED_NOT_PENDING,
                work_mode=work_mode,
                reason="missing_pr_metadata",
            )
        outcome = await approval_enqueuer(
            session=approval_session_obj or {"session_id": session_id},
            proposal=proposal,
        )
        approval_job_id = getattr(outcome, "approval_job_id", None)
        # audit 에 한 줄 남기고 persist — stage 는 그대로 유지 (사용자
        # reply 가 다음 stage 를 advance).
        new_extra = dict(session_extra)
        existing_audit = list(new_extra.get(EXTRA_PR_MERGE_AUDIT) or ())
        existing_audit.append(
            {
                "event": "approval_card_enqueued",
                "approval_job_id": approval_job_id,
                "at": _now_iso(),
            }
        )
        new_extra[EXTRA_PR_MERGE_AUDIT] = existing_audit
        persist_extra(new_extra)
        return PRMergeContinuationOutcome(
            session_id=session_id,
            action=ACTION_APPROVAL_CARD_ENQUEUED,
            work_mode=work_mode,
            approval_job_id=approval_job_id,
        )

    # autonomous_merge 경로
    if work_mode == WORK_MODE_AUTONOMOUS:
        if not is_pending_autonomous_merge(session_extra):
            return PRMergeContinuationOutcome(
                session_id=session_id,
                action=ACTION_SKIPPED_NOT_PENDING,
                work_mode=work_mode,
            )
        if merge_executor is None:
            return PRMergeContinuationOutcome(
                session_id=session_id,
                action=ACTION_SKIPPED_NO_EXECUTOR,
                work_mode=work_mode,
            )
        proposal = _proposal_from_session_extra(
            session_extra, requested_by="autonomous_merge"
        )
        if proposal is None:
            return PRMergeContinuationOutcome(
                session_id=session_id,
                action=ACTION_SKIPPED_NOT_PENDING,
                work_mode=work_mode,
                reason="missing_pr_metadata",
            )
        dispatch = PRMergeReplyDispatch(
            proposal=proposal,
            approval_job_id="auto-continuation",
            approved_by="autonomous_merge",
            approved_at=_now_iso(),
            source_message_id=None,
        )

        # P1-P — live GitHub 가 PR 을 못 찾으면 (404) 또는 다른 HTTP
        # 에러를 raise 하면 loop 가 noisy traceback 을 뿜는 대신 세션을
        # ``pr_merge_blocked`` 로 advance + audit reason ``pr_not_found`` /
        # ``http_error`` stamp.  다음 tick 에서는 not-pending 이므로 같은
        # 세션을 다시 건드리지 않는다 (fixture 세션 noise 차단).
        try:
            raw = merge_executor(dispatch)
            if hasattr(raw, "__await__"):
                raw = await raw  # type: ignore[assignment]
        except Exception as exc:  # noqa: BLE001 — translate to blocked outcome
            exc_name = type(exc).__name__
            if exc_name == "GitHubAppNotFoundError":
                reason_token = "pr_not_found"
            elif exc_name in (
                "LiveGithubAppHTTPError",
                "GitHubAppHTTPError",
                "GitHubAppAuthError",
                "GitHubAppPermissionError",
                "GitHubAppServerError",
            ):
                reason_token = f"github_http_error:{exc_name}"
            else:
                reason_token = f"merge_executor_raised:{exc_name}"
            audit_fields_err = {
                "exception_class": exc_name,
                "error": _truncate(str(exc)),
            }
            status_attr = getattr(exc, "status", None)
            if status_attr is not None:
                audit_fields_err["status"] = status_attr
            url_attr = getattr(exc, "url", None)
            if url_attr is not None:
                audit_fields_err["url"] = str(url_attr)
            new_extra = advance_stage(
                session_extra,
                new_stage=STAGE_PR_MERGE_BLOCKED,
                reason=reason_token,
                **audit_fields_err,
            )
            persist_extra(new_extra)
            logger.info(
                "advance_pending_session: session=%s blocked (%s) — %s",
                session_id,
                reason_token,
                _truncate(str(exc), limit=160),
            )
            return PRMergeContinuationOutcome(
                session_id=session_id,
                action=ACTION_AUTONOMOUS_MERGE_BLOCKED,
                work_mode=work_mode,
                new_stage=STAGE_PR_MERGE_BLOCKED,
                reason=reason_token,
                extra_audit_fields=audit_fields_err,
            )
        result: Mapping[str, Any] = dict(raw or {})

        merge_sha = str(result.get("merge_sha") or "")
        if merge_sha:
            new_extra = advance_stage(
                session_extra,
                new_stage=STAGE_PR_MERGED,
                reason="autonomous_merge_succeeded",
                merge_sha=merge_sha,
                method=str(result.get("method") or "squash"),
            )
            persist_extra(new_extra)
            # next slice — caller 가 backlog 처리
            if next_slice_dispatcher is not None:
                try:
                    res = next_slice_dispatcher(session_id, new_extra)
                    if hasattr(res, "__await__"):
                        await res  # type: ignore[func-returns-value]
                except Exception:  # noqa: BLE001 - loop must not crash
                    logger.warning(
                        "next_slice_dispatcher raised for session %s",
                        session_id,
                        exc_info=True,
                    )
            return PRMergeContinuationOutcome(
                session_id=session_id,
                action=ACTION_AUTONOMOUS_MERGE_SUCCEEDED,
                work_mode=work_mode,
                new_stage=STAGE_PR_MERGED,
                merge_sha=merge_sha,
            )

        # blocked 경로 — gate 실패, merge disabled, merge api 실패 모두 동일
        reason_token = "blocked"
        audit_fields: dict = {}
        if result.get("gate_failed_step"):
            reason_token = f"gate_failed:{result['gate_failed_step']}"
            audit_fields["gate_failed_step"] = result["gate_failed_step"]
            audit_fields["gate_reason"] = str(result.get("gate_reason") or "")
        elif result.get("merge_disabled"):
            reason_token = "merge_disabled"
            audit_fields["merge_disabled_reason"] = str(
                result.get("reason") or ""
            )
        elif result.get("merge_failed"):
            reason_token = "merge_api_failed"
            audit_fields["error"] = str(result.get("error") or "")
            audit_fields["status"] = result.get("status")
        new_extra = advance_stage(
            session_extra,
            new_stage=STAGE_PR_MERGE_BLOCKED,
            reason=reason_token,
            **audit_fields,
        )
        persist_extra(new_extra)
        return PRMergeContinuationOutcome(
            session_id=session_id,
            action=ACTION_AUTONOMOUS_MERGE_BLOCKED,
            work_mode=work_mode,
            new_stage=STAGE_PR_MERGE_BLOCKED,
            reason=reason_token,
            extra_audit_fields=audit_fields,
        )

    # 알 수 없는 work_mode — skip (decide_post_pr_action 이 이미 default 로
    # fallback 했으므로 여기 도달하면 데이터 corrupt).
    return PRMergeContinuationOutcome(
        session_id=session_id,
        action=ACTION_SKIPPED_NOT_PENDING,
        work_mode=work_mode,
        reason="unknown_work_mode",
    )


def iter_pending_session_ids(
    sessions: Sequence[Any],
) -> List[str]:
    """``pr_merge_stage = pr_merge_pending`` 인 세션 id 목록."""

    out: List[str] = []
    for session in sessions:
        extra = getattr(session, "extra", None) or {}
        if not isinstance(extra, Mapping):
            continue
        if extra.get(EXTRA_PR_MERGE_STAGE) == STAGE_PR_MERGE_PENDING:
            out.append(str(getattr(session, "session_id", "")))
    return [sid for sid in out if sid]


__all__ = (
    "ACTION_APPROVAL_CARD_ENQUEUED",
    "ACTION_AUTONOMOUS_MERGE_BLOCKED",
    "ACTION_AUTONOMOUS_MERGE_SUCCEEDED",
    "ACTION_SKIPPED_ALREADY_ENQUEUED",
    "ACTION_SKIPPED_NOT_PENDING",
    "ACTION_SKIPPED_NO_APPROVAL_WORKER",
    "ACTION_SKIPPED_NO_EXECUTOR",
    "ApprovalEnqueuer",
    "NextSliceDispatcher",
    "PRMergeContinuationOutcome",
    "advance_pending_session",
    "iter_pending_session_ids",
)
