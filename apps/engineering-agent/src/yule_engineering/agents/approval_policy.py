"""Approval policy layer — A-M5-policy.

Decides whether an action proceeds automatically, needs tech-lead
review, or requires explicit user approval. Sits **above** the
job_queue layer (M5a's :mod:`agents.job_queue.approval_worker` is
the *broadcast mechanism*; this module is the *decision*).

Key separations of authority:

  * **gateway** is *not* an approval authority. It calls into
    :func:`decide_approval`, routes the result to the correct
    surface (audit log / tech-lead worker / approval-channel
    card), and stamps the audit trail. It never auto-promotes an
    L3 action.
  * **tech-lead** is the engineering-risk reviewer. L2 actions go
    through a tech-lead review job (wired in a later milestone).
  * **human user** is the final approver for high-risk / external
    / irreversible work. L3 actions land as approval cards on
    ``#승인-대기``.

Outputs:

  * :class:`ApprovalDecision` — the policy verdict on one
    :class:`ActionContext`. Convertible to :class:`ApprovalRequest`
    only when ``approval_level == L3_HUMAN_REQUIRED``.
  * :class:`AutoApprovalAuditRecord` — what gets written to the
    audit log when ``approval_level in (L0_RECORD_ONLY,
    L1_AUTO_APPROVED)`` (or L2 after a tech-lead approves it).
    Always carries a non-empty ``reason_human_approval_not_required``
    so an operator can later defend why the action ran without a
    human gate.

This commit lands *only* the policy + audit record + helper
surface. The actual call from the gateway / runtime that turns a
decision into an ``ApprovalWorker.run_one`` invocation (or a
tech-lead review job) is deferred to the M5a-2 follow-up.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Tuple


class ApprovalLevel(str, Enum):
    """Four-rung policy ladder.

    String-valued so the enum round-trips through SQLite TEXT and
    JSON directly. Order matters — comparisons use the position in
    declaration, with L0 lowest and L3 highest.
    """

    L0_RECORD_ONLY = "L0_RECORD_ONLY"
    L1_AUTO_APPROVED = "L1_AUTO_APPROVED"
    L2_AGENT_REVIEW = "L2_AGENT_REVIEW"
    L3_HUMAN_REQUIRED = "L3_HUMAN_REQUIRED"


_LEVEL_ORDER: Mapping[ApprovalLevel, int] = {
    ApprovalLevel.L0_RECORD_ONLY: 0,
    ApprovalLevel.L1_AUTO_APPROVED: 1,
    ApprovalLevel.L2_AGENT_REVIEW: 2,
    ApprovalLevel.L3_HUMAN_REQUIRED: 3,
}


def _max_level(*levels: ApprovalLevel) -> ApprovalLevel:
    return max(levels, key=_LEVEL_ORDER.__getitem__)


# ---------------------------------------------------------------------------
# Authority + context vocabulary
# ---------------------------------------------------------------------------


AUTHORITY_POLICY: str = "policy"
AUTHORITY_TECH_LEAD: str = "tech-lead"
AUTHORITY_HUMAN: str = "human"


# ``risk_level`` enum-as-strings. We don't bind it to an Enum here
# so legacy callers can pass plain strings without an import dance.
RISK_LOW: str = "low"
RISK_MEDIUM: str = "medium"
RISK_HIGH: str = "high"
RISK_CRITICAL: str = "critical"


# ``cost_impact`` vocabulary.
COST_NONE: str = "none"
COST_MINOR: str = "minor"
COST_MAJOR: str = "major"


# ``data_sensitivity`` vocabulary.
DATA_PUBLIC: str = "public"
DATA_INTERNAL: str = "internal"
DATA_CONFIDENTIAL: str = "confidential"
DATA_SECRET: str = "secret"


# ---------------------------------------------------------------------------
# Action type vocabulary + default level mapping
# ---------------------------------------------------------------------------
#
# These string ids identify *what* the agent wants to do. Keep the
# value space small + grep-able; producers should reuse these
# constants instead of inventing new strings for the same concept.


# L0 — orchestration plumbing the operator never has to opt into.
ACTION_HEARTBEAT_EMIT: str = "heartbeat_emit"
ACTION_SUPERVISOR_SWEEP: str = "supervisor_sweep"
ACTION_HEALTH_CHECK: str = "health_check"

# L1 — automatic, must leave an audit trail with a reason.
ACTION_PUBLIC_RESEARCH_COLLECT: str = "public_research_collect"
ACTION_RSS_FETCH: str = "rss_fetch"
ACTION_SITEMAP_FETCH: str = "sitemap_fetch"
ACTION_PUBLIC_HTML_METADATA: str = "public_html_metadata"
ACTION_DEPARTMENT_FEED_POST: str = "department_feed_post"
ACTION_RESEARCH_DEDUP_TAG: str = "research_dedup_tag"
ACTION_RESEARCH_PROMOTION_CANDIDATE: str = "research_promotion_candidate"
ACTION_OBSIDIAN_DRAFT_CREATE: str = "obsidian_draft_create"

# L2 — tech-lead engineering-risk review.
ACTION_TECH_LEAD_RISK_REVIEW: str = "tech_lead_risk_review"
ACTION_ENGINEERING_CHANGE_REVIEW: str = "engineering_change_review"

# L3 — explicit human approval required.
ACTION_CODE_WRITE: str = "code_write"
ACTION_FILE_WRITE: str = "file_write"
ACTION_GIT_COMMIT: str = "git_commit"
ACTION_GIT_PUSH: str = "git_push"
ACTION_GITHUB_PR_CREATE: str = "github_pr_create"
ACTION_DEPLOY: str = "deploy"
ACTION_INFRA_CHANGE: str = "infra_change"
ACTION_SECRET_ACCESS: str = "secret_access"
ACTION_EXTERNAL_PAID_CALL: str = "external_paid_call"
ACTION_EXTERNAL_PUBLICATION: str = "external_publication"
ACTION_DATA_DELETE: str = "data_delete"
ACTION_DATA_OVERWRITE: str = "data_overwrite"
ACTION_OBSIDIAN_FINAL_KNOWLEDGE_WRITE: str = "obsidian_final_knowledge_write"


# Default policy mapping. ``decide_approval`` consults this first,
# then escalates if the :class:`ActionContext` carries higher-risk
# metadata (e.g. cost_impact=major can lift L1 → L3 for an action
# that would otherwise be auto-approved).
_DEFAULT_LEVELS: Mapping[str, ApprovalLevel] = {
    # L0
    ACTION_HEARTBEAT_EMIT: ApprovalLevel.L0_RECORD_ONLY,
    ACTION_SUPERVISOR_SWEEP: ApprovalLevel.L0_RECORD_ONLY,
    ACTION_HEALTH_CHECK: ApprovalLevel.L0_RECORD_ONLY,
    # L1
    ACTION_PUBLIC_RESEARCH_COLLECT: ApprovalLevel.L1_AUTO_APPROVED,
    ACTION_RSS_FETCH: ApprovalLevel.L1_AUTO_APPROVED,
    ACTION_SITEMAP_FETCH: ApprovalLevel.L1_AUTO_APPROVED,
    ACTION_PUBLIC_HTML_METADATA: ApprovalLevel.L1_AUTO_APPROVED,
    ACTION_DEPARTMENT_FEED_POST: ApprovalLevel.L1_AUTO_APPROVED,
    ACTION_RESEARCH_DEDUP_TAG: ApprovalLevel.L1_AUTO_APPROVED,
    ACTION_RESEARCH_PROMOTION_CANDIDATE: ApprovalLevel.L1_AUTO_APPROVED,
    ACTION_OBSIDIAN_DRAFT_CREATE: ApprovalLevel.L1_AUTO_APPROVED,
    # L2
    ACTION_TECH_LEAD_RISK_REVIEW: ApprovalLevel.L2_AGENT_REVIEW,
    ACTION_ENGINEERING_CHANGE_REVIEW: ApprovalLevel.L2_AGENT_REVIEW,
    # L3
    ACTION_CODE_WRITE: ApprovalLevel.L3_HUMAN_REQUIRED,
    ACTION_FILE_WRITE: ApprovalLevel.L3_HUMAN_REQUIRED,
    ACTION_GIT_COMMIT: ApprovalLevel.L3_HUMAN_REQUIRED,
    ACTION_GIT_PUSH: ApprovalLevel.L3_HUMAN_REQUIRED,
    ACTION_GITHUB_PR_CREATE: ApprovalLevel.L3_HUMAN_REQUIRED,
    ACTION_DEPLOY: ApprovalLevel.L3_HUMAN_REQUIRED,
    ACTION_INFRA_CHANGE: ApprovalLevel.L3_HUMAN_REQUIRED,
    ACTION_SECRET_ACCESS: ApprovalLevel.L3_HUMAN_REQUIRED,
    ACTION_EXTERNAL_PAID_CALL: ApprovalLevel.L3_HUMAN_REQUIRED,
    ACTION_EXTERNAL_PUBLICATION: ApprovalLevel.L3_HUMAN_REQUIRED,
    ACTION_DATA_DELETE: ApprovalLevel.L3_HUMAN_REQUIRED,
    ACTION_DATA_OVERWRITE: ApprovalLevel.L3_HUMAN_REQUIRED,
    ACTION_OBSIDIAN_FINAL_KNOWLEDGE_WRITE: ApprovalLevel.L3_HUMAN_REQUIRED,
}


# Default reason templates for L0 / L1 so a producer that doesn't
# supply ``reason_human_approval_not_required`` still gets a
# non-empty record. Producers should override when they have a
# more specific reason — generic strings make audit reviews harder.
_DEFAULT_AUTO_REASONS: Mapping[str, str] = {
    ACTION_HEARTBEAT_EMIT: "L0 운영 plumbing — 사용자 승인 표면이 아님",
    ACTION_SUPERVISOR_SWEEP: "L0 supervisor 자가 진단 — 외부 효과 없음",
    ACTION_HEALTH_CHECK: "L0 health probe — 읽기 전용",
    ACTION_PUBLIC_RESEARCH_COLLECT: "L1 공개 자료 수집 — 외부 결제·발행 없음",
    ACTION_RSS_FETCH: "L1 공개 RSS 피드 — 비용 없음, 데이터 외부 발행 없음",
    ACTION_SITEMAP_FETCH: "L1 공개 sitemap — 비용 없음",
    ACTION_PUBLIC_HTML_METADATA: "L1 공개 HTML metadata — 외부 효과 없음",
    ACTION_DEPARTMENT_FEED_POST: "L1 내부 부서 피드 게시 — 외부 발행 아님",
    ACTION_RESEARCH_DEDUP_TAG: "L1 자료 분류·태깅 — 비가역적 변경 없음",
    ACTION_RESEARCH_PROMOTION_CANDIDATE: "L1 승격 후보 생성 — 실제 저장은 별도 L3 절차",
    ACTION_OBSIDIAN_DRAFT_CREATE: "L1 Obsidian draft — final knowledge 저장은 별도 L3 절차",
}


# ---------------------------------------------------------------------------
# Input + output dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionContext:
    """One agent's intent to perform *action_type* in a session.

    Producers build this just before calling :func:`decide_approval`.
    The risk metadata (``risk_level``, ``reversible``, ``external_side_effect``,
    ``cost_impact``, ``data_sensitivity``) lets the policy escalate
    a default level — e.g. an L1 ``public_research_collect`` lifts
    to L3 if the producer flags ``cost_impact=major`` because the
    crawler is about to hit a paid API.

    ``proposed_level`` lets a tech-lead pre-declare a verdict
    without bypassing the policy — a tech-lead pushing for L2 is
    still bound by the L3 escalation rules below if the action is
    inherently destructive.
    """

    action_type: str
    session_id: str
    job_id: Optional[str] = None
    risk_level: str = RISK_MEDIUM
    reversible: bool = True
    external_side_effect: bool = False
    cost_impact: str = COST_NONE
    data_sensitivity: str = DATA_PUBLIC
    requested_by: str = "policy"
    proposed_level: Optional[ApprovalLevel] = None
    reason_human_approval_not_required: Optional[str] = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ApprovalDecision:
    """Policy verdict on one :class:`ActionContext`.

    Carries enough metadata that the audit log + the L3 approval
    card + the L2 tech-lead routing hint can all be derived without
    re-running the policy.
    """

    decision_id: str
    session_id: str
    job_id: Optional[str]
    action_type: str
    approval_level: ApprovalLevel
    authority: str
    human_approval_required: bool
    reason_human_approval_not_required: Optional[str]
    risk_level: str
    reversible: bool
    external_side_effect: bool
    cost_impact: str
    data_sensitivity: str
    routing_hint: str
    created_at: str  # ISO-8601 UTC

    def to_audit_record(self) -> "AutoApprovalAuditRecord":
        """Build the auto-approval audit record for an L0/L1/L2
        decision.

        Raises ``ValueError`` for L3 — those need a human approval
        card, not an audit-only record. Once a tech-lead actually
        approves an L2 (M5a-2), the same decision can be re-frozen
        as L1 with ``authority=tech-lead`` and that L1 record is
        what we eventually call ``to_audit_record`` on.
        """

        if self.approval_level == ApprovalLevel.L3_HUMAN_REQUIRED:
            raise ValueError(
                "L3 decisions require a human approval card; call "
                "to_approval_request instead of to_audit_record"
            )
        if not (self.reason_human_approval_not_required or "").strip():
            raise ValueError(
                "auto-approval audit record requires a non-empty "
                "reason_human_approval_not_required"
            )
        return AutoApprovalAuditRecord(
            decision_id=self.decision_id,
            session_id=self.session_id,
            job_id=self.job_id,
            action_type=self.action_type,
            approval_level=self.approval_level,
            authority=self.authority,
            human_approval_required=self.human_approval_required,
            reason_human_approval_not_required=self.reason_human_approval_not_required,
            risk_level=self.risk_level,
            reversible=self.reversible,
            external_side_effect=self.external_side_effect,
            cost_impact=self.cost_impact,
            data_sensitivity=self.data_sensitivity,
            created_at=self.created_at,
        )

    def to_approval_request(
        self,
        *,
        title: str,
        summary: str,
        requested_action: str,
        created_by: str,
        source_channel_id: Optional[int] = None,
        source_thread_id: Optional[int] = None,
        source_message_id: Optional[int] = None,
        approval_kind: Optional[str] = None,
    ) -> "ApprovalRequest":
        """Convert this decision into an M5a :class:`ApprovalRequest`.

        Raises ``ValueError`` for non-L3 levels — those don't go to
        ``#승인-대기``. The conversion is a straight copy with the
        decision metadata stashed into ``extra`` so the audit trail
        survives the queue trip.
        """

        if self.approval_level != ApprovalLevel.L3_HUMAN_REQUIRED:
            raise ValueError(
                f"to_approval_request requires L3_HUMAN_REQUIRED, got "
                f"{self.approval_level.value}"
            )
        # Lazy import to avoid coupling this module to the queue layer
        # at import time. The queue is optional (CLI tools / tests
        # may import the policy without the worker side).
        from .job_queue.approval_worker import (
            APPROVAL_KIND_ENGINEERING_WRITE,
            ApprovalRequest,
        )

        kind = approval_kind or APPROVAL_KIND_ENGINEERING_WRITE
        return ApprovalRequest(
            session_id=self.session_id,
            approval_kind=kind,
            title=title,
            summary=summary,
            requested_action=requested_action,
            created_by=created_by,
            source_channel_id=source_channel_id,
            source_thread_id=source_thread_id,
            source_message_id=source_message_id,
            extra={
                "decision_id": self.decision_id,
                "policy_level": self.approval_level.value,
                "risk_level": self.risk_level,
                "reversible": self.reversible,
                "external_side_effect": self.external_side_effect,
                "cost_impact": self.cost_impact,
                "data_sensitivity": self.data_sensitivity,
            },
        )


@dataclass(frozen=True)
class AutoApprovalAuditRecord:
    """Permanent record for actions that ran without a human gate.

    Producers append this to the session's audit trail (and to the
    supervisor diagnostic surface, eventually) so a future operator
    can answer "왜 이게 사용자 승인 없이 진행됐어?" by reading the
    ``reason_human_approval_not_required`` field — required to be
    non-empty by :meth:`ApprovalDecision.to_audit_record`.
    """

    decision_id: str
    session_id: str
    job_id: Optional[str]
    action_type: str
    approval_level: ApprovalLevel
    authority: str
    human_approval_required: bool
    reason_human_approval_not_required: Optional[str]
    risk_level: str
    reversible: bool
    external_side_effect: bool
    cost_impact: str
    data_sensitivity: str
    created_at: str

    def to_payload(self) -> Mapping[str, Any]:
        """JSON-friendly mirror for SQLite stash + supervisor read."""

        return {
            "decision_id": self.decision_id,
            "session_id": self.session_id,
            "job_id": self.job_id,
            "action_type": self.action_type,
            "approval_level": self.approval_level.value,
            "authority": self.authority,
            "human_approval_required": self.human_approval_required,
            "reason_human_approval_not_required": self.reason_human_approval_not_required,
            "risk_level": self.risk_level,
            "reversible": self.reversible,
            "external_side_effect": self.external_side_effect,
            "cost_impact": self.cost_impact,
            "data_sensitivity": self.data_sensitivity,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Decision engine
# ---------------------------------------------------------------------------


def decide_approval(context: ActionContext) -> ApprovalDecision:
    """Decide the approval level for *context*.

    Resolution order (later steps can only escalate, never relax):

      1. Default level from :data:`_DEFAULT_LEVELS` (or L3 if the
         action_type is unknown — defaulting unknown actions to
         human-required is the safer regression path).
      2. If ``proposed_level`` is set and *higher* than the default,
         take the higher one. Tech-lead may escalate but never
         downgrade via this hook.
      3. Risk metadata escalation:
           - ``risk_level == "critical"`` → L3
           - ``not reversible`` → at least L3
           - ``external_side_effect=True`` → at least L3
           - ``cost_impact == "major"`` → at least L3
           - ``data_sensitivity in ("confidential", "secret")`` → at least L3
      4. Authority is derived from the final level:
           - L0/L1 → ``policy``
           - L2 → ``tech-lead``
           - L3 → ``human``
    """

    base = _DEFAULT_LEVELS.get(
        context.action_type, ApprovalLevel.L3_HUMAN_REQUIRED
    )
    if context.proposed_level is not None:
        base = _max_level(base, context.proposed_level)

    if context.risk_level == RISK_CRITICAL:
        base = _max_level(base, ApprovalLevel.L3_HUMAN_REQUIRED)
    if not context.reversible:
        base = _max_level(base, ApprovalLevel.L3_HUMAN_REQUIRED)
    if context.external_side_effect:
        base = _max_level(base, ApprovalLevel.L3_HUMAN_REQUIRED)
    if context.cost_impact == COST_MAJOR:
        base = _max_level(base, ApprovalLevel.L3_HUMAN_REQUIRED)
    if context.data_sensitivity in (DATA_CONFIDENTIAL, DATA_SECRET):
        base = _max_level(base, ApprovalLevel.L3_HUMAN_REQUIRED)

    if base == ApprovalLevel.L3_HUMAN_REQUIRED:
        authority = AUTHORITY_HUMAN
        reason = None
        routing_hint = "human-approval"
    elif base == ApprovalLevel.L2_AGENT_REVIEW:
        authority = AUTHORITY_TECH_LEAD
        reason = (
            context.reason_human_approval_not_required
            or _DEFAULT_AUTO_REASONS.get(context.action_type)
            or "L2 — tech-lead 검토 후 진행"
        )
        routing_hint = "tech-lead-review"
    elif base == ApprovalLevel.L1_AUTO_APPROVED:
        authority = AUTHORITY_POLICY
        reason = (
            context.reason_human_approval_not_required
            or _DEFAULT_AUTO_REASONS.get(context.action_type)
        )
        routing_hint = "auto-record"
    else:  # L0
        authority = AUTHORITY_POLICY
        reason = (
            context.reason_human_approval_not_required
            or _DEFAULT_AUTO_REASONS.get(context.action_type)
        )
        routing_hint = "no-record"

    return ApprovalDecision(
        decision_id=_new_decision_id(),
        session_id=context.session_id,
        job_id=context.job_id,
        action_type=context.action_type,
        approval_level=base,
        authority=authority,
        human_approval_required=(base == ApprovalLevel.L3_HUMAN_REQUIRED),
        reason_human_approval_not_required=reason,
        risk_level=context.risk_level,
        reversible=context.reversible,
        external_side_effect=context.external_side_effect,
        cost_impact=context.cost_impact,
        data_sensitivity=context.data_sensitivity,
        routing_hint=routing_hint,
        created_at=_utc_now_iso(),
    )


# ---------------------------------------------------------------------------
# Gateway guard
# ---------------------------------------------------------------------------


def gateway_can_auto_approve(decision: ApprovalDecision) -> bool:
    """True when the gateway can run the action without a human.

    L3 always returns False — the gateway must hand off to the
    approval card path. L2 also returns False — the gateway is not
    a tech-lead and cannot self-approve engineering risk. L0/L1
    return True. This is the canonical gate the gateway calls
    before invoking any worker.
    """

    return decision.approval_level in (
        ApprovalLevel.L0_RECORD_ONLY,
        ApprovalLevel.L1_AUTO_APPROVED,
    )


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def format_audit_record_markdown(record: AutoApprovalAuditRecord) -> str:
    """Render *record* as the audit-log line the supervisor surfaces.

    Format is intentionally compact — one fenced "기록" block with
    every field on its own line so a future log scanner can grep
    for specific keys without parsing JSON.
    """

    lines = [
        f"**[자동 승인 기록 — {record.approval_level.value}] {record.action_type}**",
        "",
        f"decision: `{record.decision_id}`",
        f"세션: `{record.session_id}` · 작업: `{record.job_id or '-'}`",
        f"권한자: `{record.authority}` · 사람 승인 필요: "
        f"{'예' if record.human_approval_required else '아니오'}",
    ]
    reason = (record.reason_human_approval_not_required or "").strip()
    if reason:
        lines.append(f"사유: {reason}")
    lines.append(
        "메타: "
        f"risk={record.risk_level} · "
        f"reversible={record.reversible} · "
        f"external_side_effect={record.external_side_effect} · "
        f"cost_impact={record.cost_impact} · "
        f"data_sensitivity={record.data_sensitivity}"
    )
    lines.append(f"기록 시각: {record.created_at}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def _new_decision_id() -> str:
    """ulid-ish identifier — sortable timestamp prefix + random tail."""

    return f"{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:12]}"


__all__ = (
    # Action types
    "ACTION_CODE_WRITE",
    "ACTION_DATA_DELETE",
    "ACTION_DATA_OVERWRITE",
    "ACTION_DEPARTMENT_FEED_POST",
    "ACTION_DEPLOY",
    "ACTION_ENGINEERING_CHANGE_REVIEW",
    "ACTION_EXTERNAL_PAID_CALL",
    "ACTION_EXTERNAL_PUBLICATION",
    "ACTION_FILE_WRITE",
    "ACTION_GIT_COMMIT",
    "ACTION_GIT_PUSH",
    "ACTION_GITHUB_PR_CREATE",
    "ACTION_HEALTH_CHECK",
    "ACTION_HEARTBEAT_EMIT",
    "ACTION_INFRA_CHANGE",
    "ACTION_OBSIDIAN_DRAFT_CREATE",
    "ACTION_OBSIDIAN_FINAL_KNOWLEDGE_WRITE",
    "ACTION_PUBLIC_HTML_METADATA",
    "ACTION_PUBLIC_RESEARCH_COLLECT",
    "ACTION_RESEARCH_DEDUP_TAG",
    "ACTION_RESEARCH_PROMOTION_CANDIDATE",
    "ACTION_RSS_FETCH",
    "ACTION_SECRET_ACCESS",
    "ACTION_SITEMAP_FETCH",
    "ACTION_SUPERVISOR_SWEEP",
    "ACTION_TECH_LEAD_RISK_REVIEW",
    # Authority + vocabulary
    "AUTHORITY_HUMAN",
    "AUTHORITY_POLICY",
    "AUTHORITY_TECH_LEAD",
    "COST_MAJOR",
    "COST_MINOR",
    "COST_NONE",
    "DATA_CONFIDENTIAL",
    "DATA_INTERNAL",
    "DATA_PUBLIC",
    "DATA_SECRET",
    "RISK_CRITICAL",
    "RISK_HIGH",
    "RISK_LOW",
    "RISK_MEDIUM",
    # Levels
    "ApprovalLevel",
    # Models
    "ActionContext",
    "ApprovalDecision",
    "AutoApprovalAuditRecord",
    # Functions
    "decide_approval",
    "format_audit_record_markdown",
    "gateway_can_auto_approve",
)
