"""P1-L post-PR merge continuation — work_mode 분기.

배경
----
이전까지 ``coding_execute`` 파이프라인은 draft PR 을 열고 ``saved`` 로
종료했다. 그 다음 운영자에게 "지금 이 PR 머지해도 되니?" 라는 신호가
가지 않았다 — 자율 모드든 승인 모드든 멈춤. 사용자가 별도 메시지를
보내야만 다음 슬라이스가 굴러갔다.

본 모듈은 그 결정을 **session.extra 에 결정 인쇄 + 다음 액션 토큰
반환** 방식으로 표면화한다. 실제 머지 / 승인 카드 enqueue / 다음 슬라이스
producer 호출은 caller (``coding_executor_worker`` 와 background 루프) 에
위임한다.

코드 SSoT — work_mode 종류는 [`agents/lifecycle/session_mode.py`]
의 ``WORK_MODE_AUTONOMOUS`` / ``WORK_MODE_APPROVAL`` 를 그대로 사용.

session.extra schema (한 세션 = 한 머지 사이클):

    pr_merge_stage           — pr_merge_pending | pr_merge_approved
                                | pr_merged | pr_merge_blocked
    pr_merge_pr_number       — int
    pr_merge_pr_url          — str
    pr_merge_repo            — str (owner/name)
    pr_merge_head_sha        — str
    pr_merge_base_branch     — str
    pr_merge_decided_at      — iso8601 utc
    pr_merge_reason          — "draft_pr_opened:autonomous_merge" 등
    pr_merge_continuation_audit — list of stage transition dicts

함수는 모두 pure / network-free.  workflow_state persistence 는 caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, List, Mapping, Optional

from ..lifecycle.session_mode import (
    EXTRA_WORK_MODE,
    WORK_MODE_APPROVAL,
    WORK_MODE_AUTONOMOUS,
    WORK_MODE_DEFAULT,
)


# session.extra 키 이름 — operator status panel / dispatcher 가 같이 읽음
EXTRA_PR_MERGE_STAGE: str = "pr_merge_stage"
EXTRA_PR_MERGE_PR_NUMBER: str = "pr_merge_pr_number"
EXTRA_PR_MERGE_PR_URL: str = "pr_merge_pr_url"
EXTRA_PR_MERGE_REPO: str = "pr_merge_repo"
EXTRA_PR_MERGE_HEAD_SHA: str = "pr_merge_head_sha"
EXTRA_PR_MERGE_BASE_BRANCH: str = "pr_merge_base_branch"
EXTRA_PR_MERGE_DECIDED_AT: str = "pr_merge_decided_at"
EXTRA_PR_MERGE_REASON: str = "pr_merge_reason"
EXTRA_PR_MERGE_AUDIT: str = "pr_merge_continuation_audit"


# pr_merge_stage 값 — 4-state 머지 사이클
STAGE_PR_MERGE_PENDING: str = "pr_merge_pending"
"""draft PR 열림 / 다음 액션 대기 (autonomous 머지 시도 또는 approval card)."""

STAGE_PR_MERGE_APPROVED: str = "pr_merge_approved"
"""approval card 에 사용자가 승인 회신 — gate + 실제 merge 호출 직전."""

STAGE_PR_MERGED: str = "pr_merged"
"""실제 GitHub merge 호출 성공. 다음 슬라이스 producer 가 깨우는 신호."""

STAGE_PR_MERGE_BLOCKED: str = "pr_merge_blocked"
"""gate fail / merge API 실패 / merge env disabled — 운영자가 봐야 함."""

# P1-Q — draft PR escalation 전용 stage.
# autonomous_merge 가 gate 1단계에서 draft 를 만나면 옛 wiring 은 즉시
# pr_merge_blocked 로 끝났지만, 본 stage 는 사람 승인 카드를 게시한 뒤
# 사용자가 "draft 해제 + 머지 진행" 을 명시 승인하면 ready_for_review +
# gate rerun 으로 다음 단계로 넘어간다.  reply path 가 이 stage 만 보고
# escalation 분기를 가동.
STAGE_AWAITING_DRAFT_APPROVAL: str = "awaiting_draft_approval"


PR_MERGE_STAGES: tuple = (
    STAGE_PR_MERGE_PENDING,
    STAGE_AWAITING_DRAFT_APPROVAL,
    STAGE_PR_MERGE_APPROVED,
    STAGE_PR_MERGED,
    STAGE_PR_MERGE_BLOCKED,
)


class PostPRAction(str, Enum):
    """Caller (worker / background loop) 가 다음에 무엇을 해야 하는지."""

    AUTONOMOUS_MERGE = "autonomous_merge_continuation"
    """work_mode=autonomous_merge — gate poll + auto-merge 루프 시동."""

    APPROVAL_REQUIRED = "approval_required_continuation"
    """work_mode=approval_required — PRMergeProposal 빌드 + 승인 카드 enqueue."""

    SKIP = "skip"
    """dry_run 또는 PR 메타 부족 — 진행하지 않음."""


@dataclass(frozen=True)
class ContinuationContext:
    """다음 단계가 필요로 하는 PR 식별 + work_mode 묶음."""

    session_id: str
    repo_full_name: str
    pr_number: int
    pr_url: str
    head_sha: str
    base_branch: str
    work_mode: str


@dataclass(frozen=True)
class ContinuationDecision:
    """``decide_post_pr_action`` 의 결과.

    ``extra_updates`` 를 caller 가 session.extra 에 머지하면 stage / 모드 /
    PR 메타가 한 번에 일관되게 기록된다.
    """

    action: PostPRAction
    reason: str
    context: Optional[ContinuationContext] = None
    extra_updates: Mapping[str, Any] = field(default_factory=dict)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def resolve_work_mode(session_extra: Optional[Mapping[str, Any]]) -> str:
    """session.extra 의 work_mode 를 반환. 미설정/이상 값이면 default."""

    value = (session_extra or {}).get(EXTRA_WORK_MODE)
    if value in (WORK_MODE_AUTONOMOUS, WORK_MODE_APPROVAL):
        return str(value)
    return WORK_MODE_DEFAULT


def decide_post_pr_action(
    *,
    session_id: str,
    session_extra: Optional[Mapping[str, Any]],
    repo_full_name: Optional[str],
    pr_number: Optional[int],
    pr_url: Optional[str],
    head_sha: Optional[str],
    base_branch: str = "main",
    dry_run: bool = False,
) -> ContinuationDecision:
    """draft PR 직후 어떤 continuation 경로로 갈지 결정.

    work_mode 가 누락/이상이면 ``WORK_MODE_DEFAULT`` (= approval_required) 로
    fallback — 자동 머지로 잘못 빠지는 사고 방지. ``dry_run`` 또는 PR
    메타 부족 시 ``SKIP`` (caller 는 stage 를 스탬프하지 말 것).
    """

    if dry_run:
        return ContinuationDecision(
            action=PostPRAction.SKIP, reason="dry_run"
        )
    if not pr_number or not pr_url or not repo_full_name:
        return ContinuationDecision(
            action=PostPRAction.SKIP, reason="missing_pr_metadata"
        )

    work_mode = resolve_work_mode(session_extra)
    context = ContinuationContext(
        session_id=str(session_id or ""),
        repo_full_name=str(repo_full_name),
        pr_number=int(pr_number),
        pr_url=str(pr_url),
        head_sha=str(head_sha or ""),
        base_branch=str(base_branch or "main"),
        work_mode=work_mode,
    )

    decided_at = _now_iso()
    extras: dict = {
        EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_PENDING,
        EXTRA_PR_MERGE_PR_NUMBER: int(pr_number),
        EXTRA_PR_MERGE_PR_URL: str(pr_url),
        EXTRA_PR_MERGE_REPO: str(repo_full_name),
        EXTRA_PR_MERGE_HEAD_SHA: str(head_sha or ""),
        EXTRA_PR_MERGE_BASE_BRANCH: str(base_branch or "main"),
        EXTRA_PR_MERGE_DECIDED_AT: decided_at,
        EXTRA_PR_MERGE_REASON: f"draft_pr_opened:{work_mode}",
    }

    if work_mode == WORK_MODE_AUTONOMOUS:
        action = PostPRAction.AUTONOMOUS_MERGE
    else:
        action = PostPRAction.APPROVAL_REQUIRED

    return ContinuationDecision(
        action=action,
        reason=f"draft_pr_opened:{work_mode}",
        context=context,
        extra_updates=extras,
    )


def advance_stage(
    session_extra: Optional[Mapping[str, Any]],
    *,
    new_stage: str,
    reason: str,
    **fields: Any,
) -> dict:
    """session.extra dict 를 새 stage 로 advance — caller 가 persist.

    audit list 에도 한 줄 추가해서 어떤 stage 변화가 언제 일어났는지
    operator 가 볼 수 있게 한다. 입력은 mutate 하지 않음 — 새 dict 반환.
    """

    if new_stage not in PR_MERGE_STAGES:
        raise ValueError(f"unknown pr_merge_stage: {new_stage!r}")

    base = dict(session_extra or {})
    prior_stage = base.get(EXTRA_PR_MERGE_STAGE)
    now = _now_iso()

    base[EXTRA_PR_MERGE_STAGE] = new_stage
    base[EXTRA_PR_MERGE_DECIDED_AT] = now
    base[EXTRA_PR_MERGE_REASON] = reason

    audit_entry: dict = {
        "stage": new_stage,
        "prior_stage": prior_stage,
        "reason": reason,
        "at": now,
    }
    for key, value in fields.items():
        audit_entry[key] = value
    existing_audit: List[Mapping[str, Any]] = list(
        base.get(EXTRA_PR_MERGE_AUDIT) or ()
    )
    existing_audit.append(audit_entry)
    base[EXTRA_PR_MERGE_AUDIT] = existing_audit
    return base


def is_pending_continuation(
    session_extra: Optional[Mapping[str, Any]],
) -> bool:
    """background 루프가 pick 할 세션 — stage 가 pr_merge_pending 인 것."""

    return (
        (session_extra or {}).get(EXTRA_PR_MERGE_STAGE)
        == STAGE_PR_MERGE_PENDING
    )


def is_pending_autonomous_merge(
    session_extra: Optional[Mapping[str, Any]],
) -> bool:
    """autonomous_merge 모드 + pending stage 인 세션."""

    if not is_pending_continuation(session_extra):
        return False
    return resolve_work_mode(session_extra) == WORK_MODE_AUTONOMOUS


def is_pending_approval_card(
    session_extra: Optional[Mapping[str, Any]],
) -> bool:
    """approval_required 모드 + pending stage + 카드 아직 enqueue 안 된 세션."""

    if not is_pending_continuation(session_extra):
        return False
    if resolve_work_mode(session_extra) != WORK_MODE_APPROVAL:
        return False
    # audit 안에 "approval_card_enqueued" 가 이미 있으면 skip — 중복 enqueue
    # 방지. background producer 는 한 번만 카드 올림.
    for entry in (session_extra or {}).get(EXTRA_PR_MERGE_AUDIT) or ():
        if (
            isinstance(entry, Mapping)
            and entry.get("event") == "approval_card_enqueued"
        ):
            return False
    return True


__all__ = (
    "ContinuationContext",
    "ContinuationDecision",
    "EXTRA_PR_MERGE_AUDIT",
    "EXTRA_PR_MERGE_BASE_BRANCH",
    "EXTRA_PR_MERGE_DECIDED_AT",
    "EXTRA_PR_MERGE_HEAD_SHA",
    "EXTRA_PR_MERGE_PR_NUMBER",
    "EXTRA_PR_MERGE_PR_URL",
    "EXTRA_PR_MERGE_REASON",
    "EXTRA_PR_MERGE_REPO",
    "EXTRA_PR_MERGE_STAGE",
    "PR_MERGE_STAGES",
    "PostPRAction",
    "STAGE_AWAITING_DRAFT_APPROVAL",
    "STAGE_PR_MERGE_APPROVED",
    "STAGE_PR_MERGE_BLOCKED",
    "STAGE_PR_MERGED",
    "STAGE_PR_MERGE_PENDING",
    "advance_stage",
    "decide_post_pr_action",
    "is_pending_approval_card",
    "is_pending_autonomous_merge",
    "is_pending_continuation",
    "resolve_work_mode",
)
