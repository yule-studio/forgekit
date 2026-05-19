"""P1-Z A — Approved 세션이 ``needs_issue`` dead-end 로 떨어지지 않게.

배경
====
이전 wiring:

  * Discord ``코딩 권한 제안`` phrase → coding_proposal stamp +
    ``enqueue_github_work_approval`` (approval card 게시 + approval row
    에 ``github_work_order_proposal`` payload stamp).
  * Discord ``승인`` reply → ``handle_github_work_approval_reply`` →
    ``dispatch_github_work_order`` (queue row 생성).
  * 그 사이 어딘가가 깨지거나 (proposal payload 누락 / approval_worker
    미주입), 또는 CLI ``yule engineer approve`` 처럼 approval row 자체가
    없는 경로면 session state 는 ``approved`` 인데도 work_order 가
    영원히 enqueue 되지 않음 → ``tracking_validation=needs_issue`` 의
    dead-end.

라이브 사례: session ``f2f36607d175`` 는 state=approved 이지만
``github_work_order_issue=None``, queue row 0, tracking=needs_issue.

본 모듈
========
* :func:`decide_post_approval_action` — pure 결정 함수 (no I/O). session
  하나를 받아 work_order 가 필요한지 판단.  결과:

    - ``noop`` + reason (이미 anchor 있음 / proposal 없음 / target 부족 ...)
    - ``needs_work_order`` + ``repo`` + ``existing_issue_number`` 등 payload

* :func:`dispatch_post_approval_work_order` — pure 결정 + queue dispatch.
  CLI / Discord 양쪽이 같은 contract 로 호출.  approval_id 가 명시되지
  않은 경로 (CLI) 면 deterministic synthetic id (``cli-approve-<sid>``)
  를 부여해 ``dispatch_github_work_order`` 의 approval-required guard 를
  통과.

idempotency
-----------
- 이미 anchor 가 있으면 noop.
- 이미 in-flight work_order (``find_active_work_order`` 매칭) 가 있으면
  ``dispatch_github_work_order`` 가 dedup → ``skipped_reason`` 으로 반환.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional


logger = logging.getLogger(__name__)


ACTION_NOOP: str = "noop"
ACTION_NEEDS_WORK_ORDER: str = "needs_work_order"
ACTION_DISPATCHED: str = "dispatched"
ACTION_FAILED: str = "failed"


# Noop reasons — operator surface 에서 "왜 dispatch 안 됐는지" 한 줄로 확인.
NOOP_REASON_NOT_APPROVED: str = "session_not_approved"
NOOP_REASON_NO_EXTRA: str = "session_extra_missing"
NOOP_REASON_NO_CODING_PROPOSAL: str = "no_coding_proposal"
NOOP_REASON_ANCHOR_ALREADY_STAMPED: str = "anchor_already_stamped"
NOOP_REASON_NO_GITHUB_TARGET: str = "no_github_target"
NOOP_REASON_UNSUPPORTED_TARGET_KIND: str = "unsupported_target_kind"
NOOP_REASON_NO_REPO: str = "no_repo_full_name"
NOOP_REASON_TERMINAL_SESSION: str = "terminal_session"
# P1-Z2 — coding_proposal absent + handoff packet path 가 lifecycle_mode 가
# research_only / 누락이면 work_order 안 만든다.  intake 가 explicit
# implementation 신호 또는 coding_proposal 둘 중 하나로 의도를 표명해야 함.
NOOP_REASON_NO_CODING_INTENT_SIGNAL: str = "no_coding_intent_signal"
NOOP_REASON_RESEARCH_ONLY_LIFECYCLE: str = "research_only_lifecycle"

FAIL_REASON_PROPOSAL_NOT_ELIGIBLE: str = "proposal_not_eligible"
FAIL_REASON_BUILD_RAISED: str = "proposal_build_raised"
FAIL_REASON_DISPATCH_RAISED: str = "dispatch_raised"


SESSION_EXTRA_POST_APPROVAL_DISPATCH_KEY: str = "post_approval_dispatch"


@dataclass(frozen=True)
class PostApprovalDecision:
    """Pure 결정 결과.  ``action`` 이 ``needs_work_order`` 일 때만 dispatch
    경로로 진입.  caller 가 ``noop`` 인 경우 reason 만 audit 에 남기면 됨.
    """

    action: str
    reason: Optional[str] = None
    repo: Optional[str] = None
    existing_issue_number: Optional[int] = None
    source_channel_id: Optional[int] = None
    source_thread_id: Optional[int] = None
    source_message_id: Optional[int] = None


def _coerce_int(value: Any) -> Optional[int]:
    try:
        result = int(value)
        return result if result > 0 else None
    except (TypeError, ValueError):
        return None


def _normalized_state(session: Any) -> str:
    raw = getattr(session, "state", None)
    if raw is None:
        return ""
    text = getattr(raw, "value", None)
    if text is None:
        text = str(raw)
    return str(text).strip().lower()


def _resolve_repo(session: Any, extra: Mapping[str, Any]) -> Optional[str]:
    """coding_proposal / handoff_packet / github_target / extra hint 에서 repo 추출.

    P1-Z2 — coding_proposal absent + handoff packet only 경로도 지원.
    handoff packet 의 ``github_target`` 은 동일 owner/repo 모양이라 그대로 흡수.
    """

    # 1. coding_proposal 의 명시 repo (옛 경로)
    proposal = extra.get("coding_proposal")
    if isinstance(proposal, Mapping):
        repo = str(proposal.get("repo_full_name") or "").strip()
        if repo and "/" in repo:
            return repo
    # 2. session.extra.repo_full_name
    repo = str(extra.get("repo_full_name") or "").strip()
    if repo and "/" in repo:
        return repo
    # 3. session.extra.github_target.owner + repo
    target = extra.get("github_target")
    if isinstance(target, Mapping):
        owner = str(target.get("owner") or "").strip()
        name = str(target.get("repo") or "").strip()
        if owner and name:
            return f"{owner}/{name}"
    # 4. P1-Z2 — coding_handoff_packet.github_target.owner + repo
    packet = extra.get("coding_handoff_packet")
    if isinstance(packet, Mapping):
        packet_target = packet.get("github_target")
        if isinstance(packet_target, Mapping):
            owner = str(packet_target.get("owner") or "").strip()
            name = str(packet_target.get("repo") or "").strip()
            if owner and name:
                return f"{owner}/{name}"
    return None


def decide_post_approval_action(session: Any) -> PostApprovalDecision:
    """Pure decision: should the caller dispatch a github_work_order?

    Caller (CLI / Discord) 가 본 함수 결과만으로 다음 액션을 정할 수 있어야
    한다 — 본 함수는 storage I/O / network 호출 절대 없음.
    """

    extra_raw = getattr(session, "extra", None)
    if not isinstance(extra_raw, Mapping):
        return PostApprovalDecision(action=ACTION_NOOP, reason=NOOP_REASON_NO_EXTRA)
    extra = extra_raw

    state = _normalized_state(session)
    if state in {"completed", "rejected"}:
        return PostApprovalDecision(
            action=ACTION_NOOP, reason=NOOP_REASON_TERMINAL_SESSION
        )
    if state != "approved":
        return PostApprovalDecision(
            action=ACTION_NOOP, reason=NOOP_REASON_NOT_APPROVED
        )

    # Already has anchor — work_order already produced an issue earlier
    anchor = extra.get("github_work_order_issue")
    if isinstance(anchor, Mapping) and _coerce_int(anchor.get("issue_number")):
        return PostApprovalDecision(
            action=ACTION_NOOP, reason=NOOP_REASON_ANCHOR_ALREADY_STAMPED
        )

    # P1-Z2 — coding intent eligibility:
    #   * coding_proposal 존재 (옛 경로) OR
    #   * coding_handoff_packet + lifecycle_mode != research_only (실제 intake)
    # research_only 신호가 명시되면 어떤 경우든 work_order 안 만든다.
    coding_proposal = extra.get("coding_proposal")
    has_coding_proposal = isinstance(coding_proposal, Mapping) and bool(coding_proposal)
    handoff_packet = extra.get("coding_handoff_packet")
    has_handoff_packet = isinstance(handoff_packet, Mapping) and bool(handoff_packet)
    lifecycle_mode = str(extra.get("lifecycle_mode") or "").strip().lower()

    if lifecycle_mode == "research_only":
        return PostApprovalDecision(
            action=ACTION_NOOP, reason=NOOP_REASON_RESEARCH_ONLY_LIFECYCLE
        )

    if not has_coding_proposal:
        # handoff packet 만 있는 실제 intake 경로 — implementation 신호 필요.
        # lifecycle_mode 명시 missing 이라도 packet 만으로는 의도 확신 못함 →
        # 안전한 default 는 "implementation 명시 또는 coding_proposal" 둘 중 하나.
        if not has_handoff_packet:
            return PostApprovalDecision(
                action=ACTION_NOOP, reason=NOOP_REASON_NO_CODING_PROPOSAL
            )
        if lifecycle_mode != "implementation":
            return PostApprovalDecision(
                action=ACTION_NOOP, reason=NOOP_REASON_NO_CODING_INTENT_SIGNAL
            )

    target = extra.get("github_target")
    if not isinstance(target, Mapping) or not target:
        # P1-Z2 — handoff packet 안의 github_target 도 본다 (캐싱된 사본).
        if has_handoff_packet:
            packet_target = handoff_packet.get("github_target")
            if isinstance(packet_target, Mapping) and packet_target:
                target = packet_target
    if not isinstance(target, Mapping) or not target:
        return PostApprovalDecision(
            action=ACTION_NOOP, reason=NOOP_REASON_NO_GITHUB_TARGET
        )

    kind = str(target.get("kind") or "").strip().lower()
    if kind not in {"repo", "issue", "pull_request"}:
        return PostApprovalDecision(
            action=ACTION_NOOP,
            reason=f"{NOOP_REASON_UNSUPPORTED_TARGET_KIND}:{kind or 'unknown'}",
        )

    repo = _resolve_repo(session, extra)
    if not repo:
        return PostApprovalDecision(action=ACTION_NOOP, reason=NOOP_REASON_NO_REPO)

    existing_issue = _coerce_int(extra.get("existing_issue_number"))
    if existing_issue is None and kind == "issue":
        existing_issue = _coerce_int(target.get("number"))

    # source ids — coding_proposal 의 message id 우선, 없으면 intake 캐시 / None.
    proposal_message_id = None
    if has_coding_proposal:
        proposal_message_id = _coerce_int(coding_proposal.get("source_message_id"))
    return PostApprovalDecision(
        action=ACTION_NEEDS_WORK_ORDER,
        repo=repo,
        existing_issue_number=existing_issue,
        source_channel_id=getattr(session, "channel_id", None),
        source_thread_id=getattr(session, "thread_id", None),
        source_message_id=_coerce_int(extra.get("intake_message_id"))
        or proposal_message_id,
    )


def _synthetic_approval_id(session_id: str) -> str:
    """approval row 없는 path (CLI) 용 deterministic id.

    ``dispatch_github_work_order`` 는 ``approval_id`` 가 빈 문자열이면
    refuse → CLI 가 같은 contract 로 진입할 수 있게 deterministic
    prefix 부여.  같은 session 으로 두 번 호출되면 같은 id → idempotent.
    """

    return f"cli-approve-{session_id or 'unknown'}"


def _now_iso(now: Optional[datetime] = None) -> str:
    base = now or datetime.now(tz=timezone.utc)
    return base.replace(microsecond=0).isoformat()


def dispatch_post_approval_work_order(
    *,
    session: Any,
    queue: Any,
    requested_by: str = "",
    approval_id: Optional[str] = None,
    approved_by: Optional[str] = None,
    dry_run: Optional[bool] = None,
    now: Optional[datetime] = None,
    proposal_builder: Optional[Any] = None,
) -> Mapping[str, Any]:
    """Approved 세션에 work_order 가 필요하면 build + dispatch.

    *proposal_builder* 는 ``build_github_work_order_proposal`` callable
    (테스트가 inject).  미주입 시 lazy import.  결과는 dict — caller
    가 그대로 audit / operator surface 에 stamp.
    """

    decision = decide_post_approval_action(session)
    if decision.action != ACTION_NEEDS_WORK_ORDER:
        return {
            "action": ACTION_NOOP,
            "reason": decision.reason,
            "session_id": getattr(session, "session_id", None),
        }

    builder = proposal_builder
    if builder is None:
        try:
            from ...discord.integrations.github_workos_adapter import (
                build_github_work_order_proposal,
            )

            builder = build_github_work_order_proposal
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dispatch_post_approval_work_order: proposal builder import failed",
                exc_info=True,
            )
            return {
                "action": ACTION_FAILED,
                "reason": f"{FAIL_REASON_BUILD_RAISED}:{type(exc).__name__}",
                "session_id": getattr(session, "session_id", None),
            }

    request_text = getattr(session, "prompt", "") or ""
    try:
        proposal = builder(
            session=session,
            request_text=request_text,
            source_channel_id=decision.source_channel_id,
            source_thread_id=decision.source_thread_id,
            source_message_id=decision.source_message_id,
            requested_by=requested_by,
            repo=decision.repo,
            existing_issue_number=decision.existing_issue_number,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "dispatch_post_approval_work_order: proposal builder raised "
            "for session=%s",
            getattr(session, "session_id", "?"),
            exc_info=True,
        )
        return {
            "action": ACTION_FAILED,
            "reason": f"{FAIL_REASON_BUILD_RAISED}:{type(exc).__name__}",
            "session_id": getattr(session, "session_id", None),
        }

    if proposal is None:
        return {
            "action": ACTION_FAILED,
            "reason": FAIL_REASON_PROPOSAL_NOT_ELIGIBLE,
            "session_id": getattr(session, "session_id", None),
        }

    from .github_work_order import (
        GitHubWorkOrder,
        dispatch_github_work_order,
    )

    effective_approval = (approval_id or "").strip() or _synthetic_approval_id(
        str(getattr(session, "session_id", "") or "")
    )
    effective_approver = (approved_by or "").strip() or requested_by or "engineer-cli"
    work_order = GitHubWorkOrder.from_proposal(
        proposal,
        approval_id=effective_approval,
        approved_by=effective_approver,
        approved_at=_now_iso(now),
        dry_run=dry_run,
    )

    try:
        outcome = dispatch_github_work_order(
            queue,
            work_order,
            now=(now.timestamp() if now is not None else None),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "dispatch_post_approval_work_order: dispatch_github_work_order "
            "raised for session=%s",
            getattr(session, "session_id", "?"),
            exc_info=True,
        )
        return {
            "action": ACTION_FAILED,
            "reason": f"{FAIL_REASON_DISPATCH_RAISED}:{type(exc).__name__}",
            "session_id": getattr(session, "session_id", None),
        }

    if outcome.skipped_reason:
        return {
            "action": ACTION_NOOP,
            "reason": outcome.skipped_reason,
            "session_id": getattr(session, "session_id", None),
        }

    job_id = outcome.job.job_id if outcome.job is not None else None
    return {
        "action": ACTION_DISPATCHED,
        "reason": None,
        "session_id": getattr(session, "session_id", None),
        "job_id": job_id,
        "approval_id": effective_approval,
        "approved_by": effective_approver,
        "repo": decision.repo,
        "existing_issue_number": decision.existing_issue_number,
    }


__all__ = (
    "ACTION_DISPATCHED",
    "ACTION_FAILED",
    "ACTION_NEEDS_WORK_ORDER",
    "ACTION_NOOP",
    "FAIL_REASON_BUILD_RAISED",
    "FAIL_REASON_DISPATCH_RAISED",
    "FAIL_REASON_PROPOSAL_NOT_ELIGIBLE",
    "NOOP_REASON_ANCHOR_ALREADY_STAMPED",
    "NOOP_REASON_NO_CODING_PROPOSAL",
    "NOOP_REASON_NO_EXTRA",
    "NOOP_REASON_NO_GITHUB_TARGET",
    "NOOP_REASON_NO_REPO",
    "NOOP_REASON_NOT_APPROVED",
    "NOOP_REASON_TERMINAL_SESSION",
    "NOOP_REASON_UNSUPPORTED_TARGET_KIND",
    "PostApprovalDecision",
    "SESSION_EXTRA_POST_APPROVAL_DISPATCH_KEY",
    "decide_post_approval_action",
    "dispatch_post_approval_work_order",
)
