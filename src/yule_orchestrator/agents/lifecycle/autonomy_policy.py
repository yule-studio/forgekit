"""Autonomy policy — A-M10a 5-tier ladder.

Layered **above** :mod:`agents.approval_policy` (M5a). The legacy
policy uses a four-rung ladder (L0 record / L1 auto-approved /
L2 tech-lead review / L3 human required) sized for the M5/M6
"queue + approval card" world.

M10 widens the surface: the engineering-agent must act like an
employee, not a remote-control toy. That means it has to be able
to:

  * auto-record its own research / dedup / failure audits without
    asking permission,
  * generate post-reports for low-risk drafts (blog drafts, self-
    improvement proposals, docs/test fixes) and *act* — not just
    "request action",
  * still gate destructive and externally-visible work behind a
    human (or, for the worst class, a hard "절대 금지" rail).

The five levels match the M10 spec exactly. Producers call
:func:`decide_autonomy` with an :class:`AutonomyContext` and the
returned :class:`AutonomyDecision` says:

  * what level the action lives at,
  * whether an audit entry MUST be written,
  * whether a human is on the path (and therefore the producer
    must hand off to ``ApprovalWorker`` instead of running),
  * a non-empty rationale string the audit log + ``#봇-상태``
    reporter can quote.

The bridge :meth:`AutonomyDecision.to_action_context` converts an
L3/L4 decision into the legacy :class:`ActionContext`, so the
existing M5a queue / Discord card pipeline keeps owning the human
approval surface — we don't fork it.

Nothing here writes audit records itself; that is
:mod:`agents.lifecycle.agent_ops_log`'s job.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Tuple


class AutonomyLevel(str, Enum):
    """M10 five-rung autonomy ladder.

    Numeric ordering matches the spec: lower means *more* autonomy
    for the agent, higher means *more* of a human gate.
    """

    L0_AUTO_RECORD_OPTIONAL = "L0_AUTO_RECORD_OPTIONAL"
    L1_AUTO_RECORD_REQUIRED = "L1_AUTO_RECORD_REQUIRED"
    L2_AUTO_POST_REPORT = "L2_AUTO_POST_REPORT"
    L3_HUMAN_APPROVAL = "L3_HUMAN_APPROVAL"
    L4_STRONG_APPROVAL_OR_FORBIDDEN = "L4_STRONG_APPROVAL_OR_FORBIDDEN"


_LEVEL_ORDER: Mapping[AutonomyLevel, int] = {
    AutonomyLevel.L0_AUTO_RECORD_OPTIONAL: 0,
    AutonomyLevel.L1_AUTO_RECORD_REQUIRED: 1,
    AutonomyLevel.L2_AUTO_POST_REPORT: 2,
    AutonomyLevel.L3_HUMAN_APPROVAL: 3,
    AutonomyLevel.L4_STRONG_APPROVAL_OR_FORBIDDEN: 4,
}


def _max_level(*levels: AutonomyLevel) -> AutonomyLevel:
    return max(levels, key=_LEVEL_ORDER.__getitem__)


# ---------------------------------------------------------------------------
# Action catalog
# ---------------------------------------------------------------------------
#
# Action ids are short, lowercase, snake_case. Producers must reuse
# the constants below — inventing freeform strings makes the audit
# log un-greppable.


# L0 — read-only orchestration plumbing.
ACTION_STATUS_QUERY: str = "status_query"
ACTION_QUEUE_INSPECT: str = "queue_inspect"
ACTION_HEARTBEAT_CHECK: str = "heartbeat_check"
ACTION_LOCAL_FILE_READ: str = "local_file_read"
ACTION_SESSION_LOOKUP: str = "session_lookup"
ACTION_TOPIC_LOOKUP: str = "topic_lookup"
ACTION_MEMORY_READ: str = "memory_read"

# L1 — auto-execute, must leave an agent-ops audit entry.
ACTION_USER_ORDERED_RESEARCH: str = "user_ordered_research"
ACTION_THREAD_SNAPSHOT_CAPTURE: str = "thread_snapshot_capture"
ACTION_LINK_COLLECTION: str = "link_collection"
ACTION_ROLE_TAKE_RECORD: str = "role_take_record"
ACTION_FAILURE_AUDIT_RECORD: str = "failure_audit_record"
ACTION_RETRY_AUDIT_RECORD: str = "retry_audit_record"
ACTION_RESEARCH_LOG_SAVE: str = "research_log_save"
ACTION_AGENT_OPS_RECORD: str = "agent_ops_record"
ACTION_FORUM_HANDOFF_DECISION: str = "forum_handoff_decision"

# L2 — auto-execute with mandatory post-report (#봇-상태 / agent-ops).
ACTION_DRAFT_DOCUMENT_CREATE: str = "draft_document_create"
ACTION_BLOG_DRAFT_CREATE: str = "blog_draft_create"
ACTION_SELF_IMPROVEMENT_PROPOSAL: str = "self_improvement_proposal"
ACTION_FAILURE_POSTMORTEM_CREATE: str = "failure_postmortem_create"
ACTION_TEST_EXECUTE: str = "test_execute"
ACTION_LOW_RISK_DOCS_EDIT: str = "low_risk_docs_edit"
ACTION_LOW_RISK_TEST_EDIT: str = "low_risk_test_edit"
ACTION_FEATURE_BRANCH_CREATE: str = "feature_branch_create"
ACTION_LOCAL_COMMIT: str = "local_commit"
ACTION_VAULT_RESEARCH_LOG_COMMIT: str = "vault_research_log_commit"

# L3 — explicit human approval required.
ACTION_KNOWLEDGE_NOTE_FINALIZE: str = "knowledge_note_finalize"
ACTION_DECISION_RECORD_FINALIZE: str = "decision_record_finalize"
ACTION_DOCUMENT_OVERWRITE: str = "document_overwrite"
ACTION_RUNTIME_CODE_CHANGE: str = "runtime_code_change"
ACTION_PUSH_TO_SHARED_REPO: str = "push_to_shared_repo"
ACTION_DRAFT_PR_CREATE: str = "draft_pr_create"
ACTION_RUNTIME_RESTART: str = "runtime_restart"
ACTION_EXTERNAL_PAID_CALL: str = "external_paid_call"
ACTION_LARGE_SCALE_CRAWL: str = "large_scale_crawl"
ACTION_VAULT_REMOTE_PUSH: str = "vault_remote_push"

# L4 — hard rails; strong approval or outright forbidden.
ACTION_MAIN_BRANCH_PUSH: str = "main_branch_push"
ACTION_BRANCH_MERGE: str = "branch_merge"
ACTION_DEPLOY: str = "deploy"
ACTION_SECRET_ACCESS: str = "secret_access"
ACTION_SECRET_MODIFY: str = "secret_modify"
ACTION_PROD_DB_WRITE: str = "prod_db_write"
ACTION_DESTRUCTIVE_DELETE: str = "destructive_delete"
ACTION_EXTERNAL_PUBLICATION: str = "external_publication"
ACTION_BLOG_PUBLICATION: str = "blog_publication"


_DEFAULT_LEVELS: Mapping[str, AutonomyLevel] = {
    # L0
    ACTION_STATUS_QUERY: AutonomyLevel.L0_AUTO_RECORD_OPTIONAL,
    ACTION_QUEUE_INSPECT: AutonomyLevel.L0_AUTO_RECORD_OPTIONAL,
    ACTION_HEARTBEAT_CHECK: AutonomyLevel.L0_AUTO_RECORD_OPTIONAL,
    ACTION_LOCAL_FILE_READ: AutonomyLevel.L0_AUTO_RECORD_OPTIONAL,
    ACTION_SESSION_LOOKUP: AutonomyLevel.L0_AUTO_RECORD_OPTIONAL,
    ACTION_TOPIC_LOOKUP: AutonomyLevel.L0_AUTO_RECORD_OPTIONAL,
    ACTION_MEMORY_READ: AutonomyLevel.L0_AUTO_RECORD_OPTIONAL,
    # L1
    ACTION_USER_ORDERED_RESEARCH: AutonomyLevel.L1_AUTO_RECORD_REQUIRED,
    ACTION_THREAD_SNAPSHOT_CAPTURE: AutonomyLevel.L1_AUTO_RECORD_REQUIRED,
    ACTION_LINK_COLLECTION: AutonomyLevel.L1_AUTO_RECORD_REQUIRED,
    ACTION_ROLE_TAKE_RECORD: AutonomyLevel.L1_AUTO_RECORD_REQUIRED,
    ACTION_FAILURE_AUDIT_RECORD: AutonomyLevel.L1_AUTO_RECORD_REQUIRED,
    ACTION_RETRY_AUDIT_RECORD: AutonomyLevel.L1_AUTO_RECORD_REQUIRED,
    ACTION_RESEARCH_LOG_SAVE: AutonomyLevel.L1_AUTO_RECORD_REQUIRED,
    ACTION_AGENT_OPS_RECORD: AutonomyLevel.L1_AUTO_RECORD_REQUIRED,
    ACTION_FORUM_HANDOFF_DECISION: AutonomyLevel.L1_AUTO_RECORD_REQUIRED,
    # L2
    ACTION_DRAFT_DOCUMENT_CREATE: AutonomyLevel.L2_AUTO_POST_REPORT,
    ACTION_BLOG_DRAFT_CREATE: AutonomyLevel.L2_AUTO_POST_REPORT,
    ACTION_SELF_IMPROVEMENT_PROPOSAL: AutonomyLevel.L2_AUTO_POST_REPORT,
    ACTION_FAILURE_POSTMORTEM_CREATE: AutonomyLevel.L2_AUTO_POST_REPORT,
    ACTION_TEST_EXECUTE: AutonomyLevel.L2_AUTO_POST_REPORT,
    ACTION_LOW_RISK_DOCS_EDIT: AutonomyLevel.L2_AUTO_POST_REPORT,
    ACTION_LOW_RISK_TEST_EDIT: AutonomyLevel.L2_AUTO_POST_REPORT,
    ACTION_FEATURE_BRANCH_CREATE: AutonomyLevel.L2_AUTO_POST_REPORT,
    ACTION_LOCAL_COMMIT: AutonomyLevel.L2_AUTO_POST_REPORT,
    ACTION_VAULT_RESEARCH_LOG_COMMIT: AutonomyLevel.L2_AUTO_POST_REPORT,
    # L3
    ACTION_KNOWLEDGE_NOTE_FINALIZE: AutonomyLevel.L3_HUMAN_APPROVAL,
    ACTION_DECISION_RECORD_FINALIZE: AutonomyLevel.L3_HUMAN_APPROVAL,
    ACTION_DOCUMENT_OVERWRITE: AutonomyLevel.L3_HUMAN_APPROVAL,
    ACTION_RUNTIME_CODE_CHANGE: AutonomyLevel.L3_HUMAN_APPROVAL,
    ACTION_PUSH_TO_SHARED_REPO: AutonomyLevel.L3_HUMAN_APPROVAL,
    ACTION_DRAFT_PR_CREATE: AutonomyLevel.L3_HUMAN_APPROVAL,
    ACTION_RUNTIME_RESTART: AutonomyLevel.L3_HUMAN_APPROVAL,
    ACTION_EXTERNAL_PAID_CALL: AutonomyLevel.L3_HUMAN_APPROVAL,
    ACTION_LARGE_SCALE_CRAWL: AutonomyLevel.L3_HUMAN_APPROVAL,
    ACTION_VAULT_REMOTE_PUSH: AutonomyLevel.L3_HUMAN_APPROVAL,
    # L4
    ACTION_MAIN_BRANCH_PUSH: AutonomyLevel.L4_STRONG_APPROVAL_OR_FORBIDDEN,
    ACTION_BRANCH_MERGE: AutonomyLevel.L4_STRONG_APPROVAL_OR_FORBIDDEN,
    ACTION_DEPLOY: AutonomyLevel.L4_STRONG_APPROVAL_OR_FORBIDDEN,
    ACTION_SECRET_ACCESS: AutonomyLevel.L4_STRONG_APPROVAL_OR_FORBIDDEN,
    ACTION_SECRET_MODIFY: AutonomyLevel.L4_STRONG_APPROVAL_OR_FORBIDDEN,
    ACTION_PROD_DB_WRITE: AutonomyLevel.L4_STRONG_APPROVAL_OR_FORBIDDEN,
    ACTION_DESTRUCTIVE_DELETE: AutonomyLevel.L4_STRONG_APPROVAL_OR_FORBIDDEN,
    ACTION_EXTERNAL_PUBLICATION: AutonomyLevel.L4_STRONG_APPROVAL_OR_FORBIDDEN,
    ACTION_BLOG_PUBLICATION: AutonomyLevel.L4_STRONG_APPROVAL_OR_FORBIDDEN,
}


_DEFAULT_REASONS: Mapping[str, str] = {
    # L0
    ACTION_STATUS_QUERY: "L0 — 읽기 전용 상태 조회",
    ACTION_QUEUE_INSPECT: "L0 — 큐 상태 조회, 변경 없음",
    ACTION_HEARTBEAT_CHECK: "L0 — supervisor heartbeat 조회",
    ACTION_LOCAL_FILE_READ: "L0 — 로컬 파일 읽기, 부작용 없음",
    ACTION_SESSION_LOOKUP: "L0 — 세션 조회",
    ACTION_TOPIC_LOOKUP: "L0 — topic ledger 조회",
    ACTION_MEMORY_READ: "L0 — 메모리 조회",
    # L1
    ACTION_USER_ORDERED_RESEARCH: "L1 — 사용자 명시 오더 기반 리서치, audit 필수",
    ACTION_THREAD_SNAPSHOT_CAPTURE: "L1 — 운영-리서치 thread 스냅샷, audit 필수",
    ACTION_LINK_COLLECTION: "L1 — 공개 링크 수집, audit 필수",
    ACTION_ROLE_TAKE_RECORD: "L1 — 역할별 의견 기록, audit 필수",
    ACTION_FAILURE_AUDIT_RECORD: "L1 — 실패 audit, audit 필수",
    ACTION_RETRY_AUDIT_RECORD: "L1 — 재시도 audit, audit 필수",
    ACTION_RESEARCH_LOG_SAVE: "L1 — research-log Obsidian 자동 저장",
    ACTION_AGENT_OPS_RECORD: "L1 — agent-ops 자동 기록",
    ACTION_FORUM_HANDOFF_DECISION: "L1 — 운영-리서치 thread 저장 dispatch 결정 audit",
    # L2
    ACTION_DRAFT_DOCUMENT_CREATE: "L2 — draft 문서 자동 생성, 사후 보고",
    ACTION_BLOG_DRAFT_CREATE: "L2 — 블로그 초안 자동 생성, 외부 발행은 별도 L4",
    ACTION_SELF_IMPROVEMENT_PROPOSAL: "L2 — self-improvement 제안 자동 생성, 사후 보고",
    ACTION_FAILURE_POSTMORTEM_CREATE: "L2 — failure postmortem 자동 생성, 사후 보고",
    ACTION_TEST_EXECUTE: "L2 — 테스트 자동 실행, 결과 기록",
    ACTION_LOW_RISK_DOCS_EDIT: "L2 — 낮은 위험 docs 수정, 사후 보고",
    ACTION_LOW_RISK_TEST_EDIT: "L2 — 낮은 위험 test 수정, 사후 보고",
    ACTION_FEATURE_BRANCH_CREATE: "L2 — feature branch 생성, 로컬 한정",
    ACTION_LOCAL_COMMIT: "L2 — 로컬 commit, push 별도 L3",
    ACTION_VAULT_RESEARCH_LOG_COMMIT: "L2 — vault research-log branch 자동 commit",
}


# ---------------------------------------------------------------------------
# Risk / cost / sensitivity vocabulary (matches approval_policy)
# ---------------------------------------------------------------------------


RISK_LOW: str = "low"
RISK_MEDIUM: str = "medium"
RISK_HIGH: str = "high"
RISK_CRITICAL: str = "critical"

COST_NONE: str = "none"
COST_MINOR: str = "minor"
COST_MAJOR: str = "major"

DATA_PUBLIC: str = "public"
DATA_INTERNAL: str = "internal"
DATA_CONFIDENTIAL: str = "confidential"
DATA_SECRET: str = "secret"


# ---------------------------------------------------------------------------
# Input + output dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AutonomyContext:
    """One autonomy-level decision's inputs.

    Producers build this just before :func:`decide_autonomy`.

    ``proposed_level`` lets a caller pre-declare a verdict; it can
    only **escalate**, never relax — a tech-lead pushing a hostile
    L4 down to L1 must use the explicit override path, not this
    field.
    """

    action: str
    session_id: str = ""
    job_id: Optional[str] = None
    topic_key: Optional[str] = None
    summary: str = ""
    risk_level: str = RISK_MEDIUM
    reversible: bool = True
    external_side_effect: bool = False
    cost_impact: str = COST_NONE
    data_sensitivity: str = DATA_PUBLIC
    requested_by: str = "policy"
    proposed_level: Optional[AutonomyLevel] = None
    reason: Optional[str] = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AutonomyDecision:
    """Policy verdict on one :class:`AutonomyContext`.

    ``audit_required`` is True for L1+; the producer MUST write an
    :mod:`agents.lifecycle.agent_ops_log` entry. ``requires_human``
    is True for L3/L4 and the producer MUST hand off to the M5a
    queue / Discord card pipeline (no agent-side execution).

    ``escalation_reasons`` is the list of risk-metadata fields
    that pushed the level above the default — an audit reader can
    grep for "external_side_effect" or "cost_major" without parsing
    the human reason string.
    """

    decision_id: str
    session_id: str
    job_id: Optional[str]
    action: str
    autonomy_level: AutonomyLevel
    audit_required: bool
    requires_human: bool
    reason: str
    escalation_reasons: Tuple[str, ...]
    risk_level: str
    reversible: bool
    external_side_effect: bool
    cost_impact: str
    data_sensitivity: str
    topic_key: Optional[str]
    summary: str
    created_at: str

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "decision_id": self.decision_id,
            "session_id": self.session_id,
            "job_id": self.job_id,
            "action": self.action,
            "autonomy_level": self.autonomy_level.value,
            "audit_required": self.audit_required,
            "requires_human": self.requires_human,
            "reason": self.reason,
            "escalation_reasons": list(self.escalation_reasons),
            "risk_level": self.risk_level,
            "reversible": self.reversible,
            "external_side_effect": self.external_side_effect,
            "cost_impact": self.cost_impact,
            "data_sensitivity": self.data_sensitivity,
            "topic_key": self.topic_key,
            "summary": self.summary,
            "created_at": self.created_at,
        }

    def to_action_context(self) -> Any:
        """Bridge L3/L4 decisions to the legacy approval policy.

        Raises ``ValueError`` for L0/L1/L2 — those don't go through
        :class:`ApprovalRequest`. Lazy-imports to keep the autonomy
        module light when only the L0–L2 surface is needed.
        """

        if self.autonomy_level not in (
            AutonomyLevel.L3_HUMAN_APPROVAL,
            AutonomyLevel.L4_STRONG_APPROVAL_OR_FORBIDDEN,
        ):
            raise ValueError(
                "to_action_context requires L3 or L4; got "
                f"{self.autonomy_level.value}"
            )
        from ..approval_policy import (
            ActionContext,
            ApprovalLevel as LegacyApprovalLevel,
        )

        legacy_action = _LEGACY_ACTION_BRIDGE.get(self.action, self.action)
        return ActionContext(
            action_type=legacy_action,
            session_id=self.session_id,
            job_id=self.job_id,
            risk_level=self.risk_level,
            reversible=self.reversible,
            external_side_effect=self.external_side_effect,
            cost_impact=self.cost_impact,
            data_sensitivity=self.data_sensitivity,
            requested_by=self.action,
            proposed_level=LegacyApprovalLevel.L3_HUMAN_REQUIRED,
            reason_human_approval_not_required=None,
            extra={
                "autonomy_decision_id": self.decision_id,
                "autonomy_level": self.autonomy_level.value,
                "topic_key": self.topic_key,
                "summary": self.summary,
            },
        )


# Bridge from M10 autonomy actions → M5 legacy action types. Only
# the L3/L4 actions need an entry here; lower levels never hit the
# legacy queue.
_LEGACY_ACTION_BRIDGE: Mapping[str, str] = {
    ACTION_KNOWLEDGE_NOTE_FINALIZE: "obsidian_final_knowledge_write",
    ACTION_DECISION_RECORD_FINALIZE: "obsidian_final_knowledge_write",
    ACTION_DOCUMENT_OVERWRITE: "data_overwrite",
    ACTION_RUNTIME_CODE_CHANGE: "code_write",
    ACTION_PUSH_TO_SHARED_REPO: "git_push",
    ACTION_DRAFT_PR_CREATE: "github_pr_create",
    ACTION_RUNTIME_RESTART: "infra_change",
    ACTION_EXTERNAL_PAID_CALL: "external_paid_call",
    ACTION_VAULT_REMOTE_PUSH: "git_push",
    ACTION_MAIN_BRANCH_PUSH: "git_push",
    ACTION_BRANCH_MERGE: "git_push",
    ACTION_DEPLOY: "deploy",
    ACTION_SECRET_ACCESS: "secret_access",
    ACTION_SECRET_MODIFY: "secret_access",
    ACTION_PROD_DB_WRITE: "data_overwrite",
    ACTION_DESTRUCTIVE_DELETE: "data_delete",
    ACTION_EXTERNAL_PUBLICATION: "external_publication",
    ACTION_BLOG_PUBLICATION: "external_publication",
}


# ---------------------------------------------------------------------------
# Decision engine
# ---------------------------------------------------------------------------


def decide_autonomy(context: AutonomyContext) -> AutonomyDecision:
    """Decide the autonomy level for *context*.

    Resolution order (later steps can only escalate, never relax):

      1. Default level from :data:`_DEFAULT_LEVELS` (or
         L4_STRONG_APPROVAL_OR_FORBIDDEN if the action is unknown —
         the safer regression path for an unrecognised verb).
      2. ``proposed_level`` is taken iff strictly higher.
      3. Risk metadata escalation, with each rule recording the
         escalation reason for the audit trail:
           - ``risk_level == "critical"`` → at least L4
           - ``not reversible`` → at least L3
           - ``external_side_effect=True`` → at least L3
           - ``cost_impact == "major"`` → at least L3
           - ``data_sensitivity in ("confidential", "secret")`` → at least L4
    """

    base = _DEFAULT_LEVELS.get(
        context.action, AutonomyLevel.L4_STRONG_APPROVAL_OR_FORBIDDEN
    )

    escalations: list[str] = []

    if context.proposed_level is not None and (
        _LEVEL_ORDER[context.proposed_level] > _LEVEL_ORDER[base]
    ):
        base = context.proposed_level
        escalations.append("proposed_level_override")

    if context.risk_level == RISK_CRITICAL:
        escalated = _max_level(base, AutonomyLevel.L4_STRONG_APPROVAL_OR_FORBIDDEN)
        if escalated is not base:
            escalations.append("risk_critical")
        base = escalated

    if not context.reversible:
        escalated = _max_level(base, AutonomyLevel.L3_HUMAN_APPROVAL)
        if escalated is not base:
            escalations.append("irreversible")
        base = escalated

    if context.external_side_effect:
        escalated = _max_level(base, AutonomyLevel.L3_HUMAN_APPROVAL)
        if escalated is not base:
            escalations.append("external_side_effect")
        base = escalated

    if context.cost_impact == COST_MAJOR:
        escalated = _max_level(base, AutonomyLevel.L3_HUMAN_APPROVAL)
        if escalated is not base:
            escalations.append("cost_major")
        base = escalated

    if context.data_sensitivity in (DATA_CONFIDENTIAL, DATA_SECRET):
        escalated = _max_level(base, AutonomyLevel.L4_STRONG_APPROVAL_OR_FORBIDDEN)
        if escalated is not base:
            escalations.append(f"data_{context.data_sensitivity}")
        base = escalated

    requires_human = base in (
        AutonomyLevel.L3_HUMAN_APPROVAL,
        AutonomyLevel.L4_STRONG_APPROVAL_OR_FORBIDDEN,
    )
    audit_required = base != AutonomyLevel.L0_AUTO_RECORD_OPTIONAL

    reason = (
        (context.reason or "").strip()
        or _DEFAULT_REASONS.get(context.action)
        or _generic_reason_for_level(base)
    )

    return AutonomyDecision(
        decision_id=_new_decision_id(),
        session_id=context.session_id,
        job_id=context.job_id,
        action=context.action,
        autonomy_level=base,
        audit_required=audit_required,
        requires_human=requires_human,
        reason=reason,
        escalation_reasons=tuple(escalations),
        risk_level=context.risk_level,
        reversible=context.reversible,
        external_side_effect=context.external_side_effect,
        cost_impact=context.cost_impact,
        data_sensitivity=context.data_sensitivity,
        topic_key=context.topic_key,
        summary=context.summary,
        created_at=_utc_now_iso(),
    )


def can_auto_execute(decision: AutonomyDecision) -> bool:
    """True iff the agent may run the action without human routing.

    L0/L1/L2 → True. L3/L4 → False (the producer must hand off to
    the approval card pipeline).
    """

    return not decision.requires_human


def _generic_reason_for_level(level: AutonomyLevel) -> str:
    if level == AutonomyLevel.L0_AUTO_RECORD_OPTIONAL:
        return "L0 — 자동 실행, 기록 선택"
    if level == AutonomyLevel.L1_AUTO_RECORD_REQUIRED:
        return "L1 — 자동 실행, audit 기록 필수"
    if level == AutonomyLevel.L2_AUTO_POST_REPORT:
        return "L2 — 자동 실행, 사후 보고 필수"
    if level == AutonomyLevel.L3_HUMAN_APPROVAL:
        return "L3 — 사용자 승인 필요"
    return "L4 — 강한 승인 또는 금지 (기본 차단)"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def _new_decision_id() -> str:
    return f"{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:12]}"


__all__ = (
    # Levels
    "AutonomyLevel",
    # Vocabulary
    "RISK_LOW",
    "RISK_MEDIUM",
    "RISK_HIGH",
    "RISK_CRITICAL",
    "COST_NONE",
    "COST_MINOR",
    "COST_MAJOR",
    "DATA_PUBLIC",
    "DATA_INTERNAL",
    "DATA_CONFIDENTIAL",
    "DATA_SECRET",
    # L0
    "ACTION_STATUS_QUERY",
    "ACTION_QUEUE_INSPECT",
    "ACTION_HEARTBEAT_CHECK",
    "ACTION_LOCAL_FILE_READ",
    "ACTION_SESSION_LOOKUP",
    "ACTION_TOPIC_LOOKUP",
    "ACTION_MEMORY_READ",
    # L1
    "ACTION_USER_ORDERED_RESEARCH",
    "ACTION_THREAD_SNAPSHOT_CAPTURE",
    "ACTION_LINK_COLLECTION",
    "ACTION_ROLE_TAKE_RECORD",
    "ACTION_FAILURE_AUDIT_RECORD",
    "ACTION_RETRY_AUDIT_RECORD",
    "ACTION_RESEARCH_LOG_SAVE",
    "ACTION_AGENT_OPS_RECORD",
    "ACTION_FORUM_HANDOFF_DECISION",
    # L2
    "ACTION_DRAFT_DOCUMENT_CREATE",
    "ACTION_BLOG_DRAFT_CREATE",
    "ACTION_SELF_IMPROVEMENT_PROPOSAL",
    "ACTION_FAILURE_POSTMORTEM_CREATE",
    "ACTION_TEST_EXECUTE",
    "ACTION_LOW_RISK_DOCS_EDIT",
    "ACTION_LOW_RISK_TEST_EDIT",
    "ACTION_FEATURE_BRANCH_CREATE",
    "ACTION_LOCAL_COMMIT",
    "ACTION_VAULT_RESEARCH_LOG_COMMIT",
    # L3
    "ACTION_KNOWLEDGE_NOTE_FINALIZE",
    "ACTION_DECISION_RECORD_FINALIZE",
    "ACTION_DOCUMENT_OVERWRITE",
    "ACTION_RUNTIME_CODE_CHANGE",
    "ACTION_PUSH_TO_SHARED_REPO",
    "ACTION_DRAFT_PR_CREATE",
    "ACTION_RUNTIME_RESTART",
    "ACTION_EXTERNAL_PAID_CALL",
    "ACTION_LARGE_SCALE_CRAWL",
    "ACTION_VAULT_REMOTE_PUSH",
    # L4
    "ACTION_MAIN_BRANCH_PUSH",
    "ACTION_BRANCH_MERGE",
    "ACTION_DEPLOY",
    "ACTION_SECRET_ACCESS",
    "ACTION_SECRET_MODIFY",
    "ACTION_PROD_DB_WRITE",
    "ACTION_DESTRUCTIVE_DELETE",
    "ACTION_EXTERNAL_PUBLICATION",
    "ACTION_BLOG_PUBLICATION",
    # Models
    "AutonomyContext",
    "AutonomyDecision",
    # Functions
    "decide_autonomy",
    "can_auto_execute",
)
