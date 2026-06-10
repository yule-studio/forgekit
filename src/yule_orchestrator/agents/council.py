"""Role council contract types — engineering role council runtime scaffolding.

본 모듈은 [docs/engineering-role-council-runtime.md](../../../../docs/
engineering-role-council-runtime.md) 의 8 개 deliverable schema (TaskBrief
/ RoleWorkOrder / RoleDraft / PeerReviewNote / RoleCouncilResult /
ApprovalPacket / ExecutionReview / RetrospectiveCandidate) 의 **타입 SSoT**
다. 런타임 로직 (router / runner / approval surface) 은 본 PR scope 가
아니다 — 본 모듈은 enum 과 frozen dataclass 와 idempotent helper 만
제공한다.

설계 원칙:

- `agents/deliberation.py` 의 `RoleTake` 와 호환된다 — 한 ``RoleDraft`` 가
  곧 owner seat 의 산출물 (challenger=False) 이고, challenger seat 는 같은
  shape 에 ``challenger=True`` 로 들어간다.
- ``provider`` 와 ``seat`` 는 직교한다 — provider 가 늘어도 council 의
  seat 수는 변하지 않는다.
- 모든 dataclass 는 frozen + JSON 친화 (``to_payload`` / ``from_payload``).
  ``session.extra`` 에 ``to_json_safe`` 로 그대로 박힌다.
- ``CouncilConsensusStatus = escalated`` 이고 ``round_index >=
  council_round_cap`` 이면 ``synthesize_thread`` 입력으로 사용 불가 ─
  helper ``ready_for_synthesis`` 가 1차 gate.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SeatRole(str, Enum):
    """Council 내부 토의 seat. provider 와는 직교."""

    OWNER = "owner"
    CHALLENGER = "challenger"
    REVIEWER = "reviewer"


class CouncilConsensusStatus(str, Enum):
    """Role council 한 round 의 종결 상태."""

    AGREED = "agreed"
    AGREED_WITH_CONDITIONS = "agreed_with_conditions"
    NEEDS_ANOTHER_ROUND = "needs_another_round"
    ESCALATED = "escalated"


class ApprovalPacketStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    CONDITIONAL = "conditional"
    ESCALATED = "escalated"
    ARCHIVED = "archived"


class TechLeadSignoffStatus(str, Enum):
    SIGNED_OFF = "signed_off"
    CONDITIONAL = "conditional"
    BLOCKED = "blocked"
    ESCALATED = "escalated"


class ExecutionReviewDecision(str, Enum):
    ACCEPT_AND_CLOSE = "accept_and_close"
    ACCEPT_WITH_FOLLOWUPS = "accept_with_followups"
    REOPEN_FOR_REWORK = "reopen_for_rework"
    REROUTE_TO_REVIEW_LOOP = "reroute_to_review_loop"


class CIBucketStatus(str, Enum):
    GREEN = "green"
    RED = "red"
    PARTIAL = "partial"
    NOT_APPLICABLE = "not_applicable"


class RoleCouncilRecheckStatus(str, Enum):
    OK = "ok"
    MINOR = "minor"
    NEEDS_REWORK = "needs_rework"


class RetrospectiveCandidateSource(str, Enum):
    COUNCIL_DISAGREEMENT = "council_disagreement"
    CI_FAILURE = "ci_failure"
    OK_WITH_FOLLOWUPS = "ok_with_followups"
    POSTMORTEM = "postmortem"


# ---------------------------------------------------------------------------
# Substage / lifecycle constants
# ---------------------------------------------------------------------------

SUBSTAGE_ROLE_BRIEF_DISTRIBUTED = "role_brief_distributed"
SUBSTAGE_ROLE_DRAFTS_IN_PROGRESS = "role_drafts_in_progress"
SUBSTAGE_PEER_REVIEW_PENDING = "peer_review_pending"
SUBSTAGE_COUNCIL_ROUND_COMPLETE = "council_round_complete"
SUBSTAGE_COUNCIL_ESCALATED = "council_escalated"
SUBSTAGE_COUNCIL_READY_FOR_SYNTHESIS = "council_ready_for_synthesis"
SUBSTAGE_TECH_LEAD_SYNTHESIS = "tech_lead_synthesis"
SUBSTAGE_APPROVAL_PACKET_DRAFTED = "approval_packet_drafted"
SUBSTAGE_APPROVAL_SURFACE_POSTED = "approval_surface_posted"
SUBSTAGE_CI_SIGNAL_RECEIVED = "ci_signal_received"
SUBSTAGE_ROLE_COUNCIL_RECONVENED = "role_council_reconvened"
SUBSTAGE_REVIEW_FEEDBACK_ROUTED = "review_feedback_routed"
SUBSTAGE_RETROSPECTIVE_CANDIDATE = "retrospective_candidate"

DELIBERATION_SUBSTAGES: Tuple[str, ...] = (
    SUBSTAGE_ROLE_BRIEF_DISTRIBUTED,
    SUBSTAGE_ROLE_DRAFTS_IN_PROGRESS,
    SUBSTAGE_PEER_REVIEW_PENDING,
    SUBSTAGE_COUNCIL_ROUND_COMPLETE,
    SUBSTAGE_COUNCIL_ESCALATED,
    SUBSTAGE_COUNCIL_READY_FOR_SYNTHESIS,
)
SYNTHESIS_SUBSTAGES: Tuple[str, ...] = (
    SUBSTAGE_TECH_LEAD_SYNTHESIS,
    SUBSTAGE_APPROVAL_PACKET_DRAFTED,
    SUBSTAGE_APPROVAL_SURFACE_POSTED,
)
EXECUTION_REVIEW_SUBSTAGES: Tuple[str, ...] = (
    SUBSTAGE_CI_SIGNAL_RECEIVED,
    SUBSTAGE_ROLE_COUNCIL_RECONVENED,
    SUBSTAGE_REVIEW_FEEDBACK_ROUTED,
    SUBSTAGE_RETROSPECTIVE_CANDIDATE,
)
ALL_SUBSTAGES: Tuple[str, ...] = (
    DELIBERATION_SUBSTAGES + SYNTHESIS_SUBSTAGES + EXECUTION_REVIEW_SUBSTAGES
)

DEFAULT_SEATS: Tuple[SeatRole, ...] = (SeatRole.OWNER, SeatRole.CHALLENGER, SeatRole.REVIEWER)
DEFAULT_COUNCIL_ROUND_CAP: int = 2


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskBrief:
    """tech-lead triage 의 산출물 — council 전체의 입력."""

    brief_id: str
    session_id: str
    title: str
    purpose: str
    in_scope: Tuple[str, ...] = ()
    out_of_scope: Tuple[str, ...] = ()
    references: Tuple[str, ...] = ()
    research_pack_ref: Optional[str] = None
    work_mode: Optional[str] = None  # "approval_required" / "autonomous_merge"
    revision: int = 1
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class RoleWorkOrder:
    """TaskBrief 안 한 role 의 분배 단위. council 진입 전 작성."""

    role: str  # "engineering-agent/backend-engineer" 같은 정규화 주소
    brief_id: str
    work_order_id: str
    purpose: str
    required_outputs: Tuple[str, ...] = ()
    forbidden_scope: Tuple[str, ...] = ()
    seats: Tuple[SeatRole, ...] = DEFAULT_SEATS
    council_round_cap: int = DEFAULT_COUNCIL_ROUND_CAP


@dataclass(frozen=True)
class RoleDraft:
    """한 seat 의 1차 draft. owner / challenger 모두 같은 shape."""

    role: str
    seat: SeatRole
    round_index: int
    provider: Optional[str] = None  # "claude" / "codex" / ... — provider × seat 직교
    perspective: Optional[str] = None
    evidence: Tuple[str, ...] = ()
    risks: Tuple[str, ...] = ()
    next_actions: Tuple[str, ...] = ()
    structured_fields: Mapping[str, Any] = field(default_factory=dict)
    draft_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def is_owner(self) -> bool:
        return self.seat is SeatRole.OWNER

    @property
    def is_challenger(self) -> bool:
        return self.seat is SeatRole.CHALLENGER


@dataclass(frozen=True)
class PeerReviewNote:
    """reviewer seat 의 종합. owner / challenger draft 의 reference 만 들고
    있고 새 입장을 만들지 않는다."""

    role: str
    round_index: int
    reviewer_provider: Optional[str]
    owner_draft_id: Optional[str]
    challenger_draft_id: Optional[str]
    consensus_status: CouncilConsensusStatus
    agreed_points: Tuple[str, ...] = ()
    open_questions: Tuple[str, ...] = ()
    conditions: Tuple[str, ...] = ()  # AGREED_WITH_CONDITIONS 일 때
    disagreement_summary: Optional[str] = None  # ESCALATED 일 때 필수
    public_summary: str = ""  # Discord 표면용 — raw 토의 dump 금지
    note_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class RoleCouncilResult:
    """한 role council 의 한 round 종결 결과."""

    role: str
    work_order_id: str
    round_index: int
    drafts: Tuple[RoleDraft, ...]
    peer_review: PeerReviewNote
    consensus_status: CouncilConsensusStatus
    public_summary: str
    disagreement_summary: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def is_settled(self) -> bool:
        return self.consensus_status in (
            CouncilConsensusStatus.AGREED,
            CouncilConsensusStatus.AGREED_WITH_CONDITIONS,
        )

    @property
    def is_escalated(self) -> bool:
        return self.consensus_status is CouncilConsensusStatus.ESCALATED


@dataclass(frozen=True)
class OperatorActionRef:
    """gateway 의 operator action card 참조 — packet 에 첨부."""

    request_type: str  # "APPROVAL_REQUIRED" / "INFO_REQUIRED" / ...
    request_id: str
    title: Optional[str] = None


@dataclass(frozen=True)
class TechLeadSignoff:
    status: TechLeadSignoffStatus
    rationale: str
    conditions: Tuple[str, ...] = ()
    signed_off_by: str = "engineering-agent/tech-lead"
    signed_off_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class ApprovalPacket:
    """tech-lead 의 technical approval surface — operator approval 과 분리."""

    packet_id: str
    session_id: str
    task_brief_ref: str
    role_council_results: Tuple[RoleCouncilResult, ...]
    tech_lead_signoff: TechLeadSignoff
    executor_role: str
    write_scope: Tuple[str, ...]
    forbidden_scope: Tuple[str, ...]
    test_strategy: str
    rollback_plan: str
    operator_requests: Tuple[OperatorActionRef, ...] = ()
    status: ApprovalPacketStatus = ApprovalPacketStatus.DRAFT
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class RoleCouncilRecheck:
    """execution_review 안에서 같은 role council 이 결과를 재확인."""

    role: str
    status: RoleCouncilRecheckStatus
    notes: str = ""


@dataclass(frozen=True)
class ExecutionReview:
    """coding / docs write 직후의 1급 검토 stage."""

    review_id: str
    packet_ref: str
    ci_status: CIBucketStatus
    role_council_recheck: Tuple[RoleCouncilRecheck, ...]
    reviewer_role: str
    decision: ExecutionReviewDecision
    follow_up_actions: Tuple[str, ...] = ()
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class RetrospectiveCandidate:
    """회고 자산 후보. status=pending 으로만 stamp — 본문은 운영자만 작성."""

    candidate_id: str
    session_id: str
    source: RetrospectiveCandidateSource
    candidate_topic: str
    why_candidate: str
    proposed_keep: Tuple[str, ...] = ()
    proposed_problem: Tuple[str, ...] = ()
    proposed_try: Tuple[str, ...] = ()
    status: str = "pending"
    created_at: datetime = field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_valid_substage(substage: str) -> bool:
    """C-시리즈 substage vocabulary 의 일원인지 검사."""

    return substage in ALL_SUBSTAGES


def ready_for_synthesis(results: Iterable[RoleCouncilResult]) -> bool:
    """Cross-role synthesis 진입 gate.

    모든 role 의 가장 최신 round 가 ``AGREED`` 또는 ``AGREED_WITH_CONDITIONS``
    여야 True. ``ESCALATED`` 또는 ``NEEDS_ANOTHER_ROUND`` 가 하나라도 있으면
    False — synthesis 차단.
    """

    results = list(results)
    if not results:
        return False
    by_role: dict[str, RoleCouncilResult] = {}
    for result in results:
        cur = by_role.get(result.role)
        if cur is None or result.round_index >= cur.round_index:
            by_role[result.role] = result
    return all(r.is_settled for r in by_role.values())


def synthesis_block_reason(
    results: Iterable[RoleCouncilResult],
) -> Optional[str]:
    """synthesis 차단 사유 1 줄. None 이면 통과 가능.

    가장 우선순위 높은 차단 사유만 surface — escalated > needs_another_round
    > empty.
    """

    results = list(results)
    if not results:
        return "no_role_councils_recorded"
    by_role: dict[str, RoleCouncilResult] = {}
    for result in results:
        cur = by_role.get(result.role)
        if cur is None or result.round_index >= cur.round_index:
            by_role[result.role] = result
    escalated = [r.role for r in by_role.values() if r.is_escalated]
    if escalated:
        return f"escalated: {', '.join(sorted(escalated))}"
    needs = [
        r.role
        for r in by_role.values()
        if r.consensus_status is CouncilConsensusStatus.NEEDS_ANOTHER_ROUND
    ]
    if needs:
        return f"needs_another_round: {', '.join(sorted(needs))}"
    return None


def must_escalate_to_tech_lead(
    council_history: Sequence[RoleCouncilResult],
    *,
    council_round_cap: int = DEFAULT_COUNCIL_ROUND_CAP,
) -> bool:
    """주어진 한 role 의 round 누적이 cap 을 넘었는지 + 미합의 인지.

    같은 role 의 history 만 들어와야 한다 (caller 책임).
    """

    if not council_history:
        return False
    latest = max(council_history, key=lambda r: r.round_index)
    if latest.is_settled:
        return False
    return latest.round_index >= council_round_cap


def can_create_approval_packet(
    *,
    results: Iterable[RoleCouncilResult],
    tech_lead_signoff: Optional[TechLeadSignoff],
) -> Optional[str]:
    """ApprovalPacket 생성 차단 사유 1 줄. None 이면 통과.

    1. role councils 가 모두 settled (ready_for_synthesis True) 여야 함.
    2. tech_lead_signoff 가 BLOCKED 이면 packet 자체가 만들어지지 않음.
    """

    block = synthesis_block_reason(results)
    if block is not None:
        return f"council_not_settled: {block}"
    if tech_lead_signoff is None:
        return "tech_lead_signoff_missing"
    if tech_lead_signoff.status is TechLeadSignoffStatus.BLOCKED:
        return "tech_lead_signoff_blocked"
    return None


# ---------------------------------------------------------------------------
# Multi-role escalation aggregator
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EscalationAggregate:
    """One-shot summary of every escalated role across rounds.

    Replaces / extends the head-only ``escalation_digest`` stamped by
    :func:`council_bootstrap._build_escalation_digest`. The aggregator
    answers: *which roles need tech-lead intervention, what is the
    disagreement for each, and which round are we at?*
    """

    escalated_roles: Tuple[str, ...]
    per_role_summary: Mapping[str, str]
    highest_round_index: int
    recommended_next_owner: str

    def is_empty(self) -> bool:
        return not self.escalated_roles


def aggregate_escalations(
    results: Iterable[RoleCouncilResult],
) -> EscalationAggregate:
    """Build a multi-role escalation summary from council results.

    For each role the *latest-round* result is what counts. ``recommended
    _next_owner`` is always ``engineering-agent/tech-lead`` — the C-runtime
    contract — but the field is explicit so callers can re-target if a
    future PR introduces a different escalation owner.
    """

    latest_by_role: dict[str, RoleCouncilResult] = {}
    for r in results:
        cur = latest_by_role.get(r.role)
        if cur is None or r.round_index >= cur.round_index:
            latest_by_role[r.role] = r

    escalated = [r for r in latest_by_role.values() if r.is_escalated]
    if not escalated:
        return EscalationAggregate(
            escalated_roles=(),
            per_role_summary={},
            highest_round_index=0,
            recommended_next_owner="engineering-agent/tech-lead",
        )

    roles = tuple(sorted({r.role for r in escalated}))
    summary: dict[str, str] = {}
    for r in escalated:
        text = (
            r.disagreement_summary
            or r.peer_review.disagreement_summary
            or "(사유 미기재)"
        )
        summary[r.role] = str(text).strip() or "(사유 미기재)"
    highest = max(r.round_index for r in escalated)
    return EscalationAggregate(
        escalated_roles=roles,
        per_role_summary=summary,
        highest_round_index=int(highest),
        recommended_next_owner="engineering-agent/tech-lead",
    )


def escalation_aggregate_to_payload(
    aggregate: EscalationAggregate,
) -> Mapping[str, Any]:
    return {
        "escalated_roles": list(aggregate.escalated_roles),
        "per_role_summary": dict(aggregate.per_role_summary),
        "highest_round_index": int(aggregate.highest_round_index),
        "recommended_next_owner": aggregate.recommended_next_owner,
    }


# ---------------------------------------------------------------------------
# JSON-safe payload helpers (session.extra round-trip)
# ---------------------------------------------------------------------------

# Council dataclasses are frozen + use only primitives / enums / nested
# council types / ``datetime``. Persistence boundary needs a flat dict
# whose values are JSON-safe so ``to_json_safe`` (lifecycle persistence)
# can store them in SQLite. We keep the helpers *here* — not in
# ``lifecycle_persistence`` — so the council contract owns its own
# serialisation shape.


def _datetime_to_iso(value: datetime) -> str:
    return value.isoformat()


def _iso_to_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except Exception:  # noqa: BLE001 — partial install / legacy stamp
        return datetime.utcnow()


def role_draft_to_payload(draft: RoleDraft) -> Mapping[str, Any]:
    return {
        "role": draft.role,
        "seat": draft.seat.value,
        "round_index": int(draft.round_index),
        "provider": draft.provider,
        "perspective": draft.perspective,
        "evidence": list(draft.evidence),
        "risks": list(draft.risks),
        "next_actions": list(draft.next_actions),
        "structured_fields": dict(draft.structured_fields or {}),
        "draft_id": draft.draft_id,
        "created_at": _datetime_to_iso(draft.created_at),
    }


def role_draft_from_payload(payload: Mapping[str, Any]) -> RoleDraft:
    return RoleDraft(
        role=str(payload["role"]),
        seat=SeatRole(str(payload["seat"])),
        round_index=int(payload.get("round_index", 1)),
        provider=payload.get("provider"),
        perspective=payload.get("perspective"),
        evidence=tuple(str(item) for item in (payload.get("evidence") or ())),
        risks=tuple(str(item) for item in (payload.get("risks") or ())),
        next_actions=tuple(
            str(item) for item in (payload.get("next_actions") or ())
        ),
        structured_fields=dict(payload.get("structured_fields") or {}),
        draft_id=str(payload.get("draft_id") or uuid.uuid4().hex[:12]),
        created_at=_iso_to_datetime(payload.get("created_at")),
    )


def peer_review_note_to_payload(note: PeerReviewNote) -> Mapping[str, Any]:
    return {
        "role": note.role,
        "round_index": int(note.round_index),
        "reviewer_provider": note.reviewer_provider,
        "owner_draft_id": note.owner_draft_id,
        "challenger_draft_id": note.challenger_draft_id,
        "consensus_status": note.consensus_status.value,
        "agreed_points": list(note.agreed_points),
        "open_questions": list(note.open_questions),
        "conditions": list(note.conditions),
        "disagreement_summary": note.disagreement_summary,
        "public_summary": note.public_summary,
        "note_id": note.note_id,
        "created_at": _datetime_to_iso(note.created_at),
    }


def peer_review_note_from_payload(payload: Mapping[str, Any]) -> PeerReviewNote:
    return PeerReviewNote(
        role=str(payload["role"]),
        round_index=int(payload.get("round_index", 1)),
        reviewer_provider=payload.get("reviewer_provider"),
        owner_draft_id=payload.get("owner_draft_id"),
        challenger_draft_id=payload.get("challenger_draft_id"),
        consensus_status=CouncilConsensusStatus(
            str(payload.get("consensus_status") or CouncilConsensusStatus.NEEDS_ANOTHER_ROUND.value)
        ),
        agreed_points=tuple(
            str(item) for item in (payload.get("agreed_points") or ())
        ),
        open_questions=tuple(
            str(item) for item in (payload.get("open_questions") or ())
        ),
        conditions=tuple(str(item) for item in (payload.get("conditions") or ())),
        disagreement_summary=payload.get("disagreement_summary"),
        public_summary=str(payload.get("public_summary") or ""),
        note_id=str(payload.get("note_id") or uuid.uuid4().hex[:12]),
        created_at=_iso_to_datetime(payload.get("created_at")),
    )


def role_council_result_to_payload(
    result: RoleCouncilResult,
) -> Mapping[str, Any]:
    return {
        "role": result.role,
        "work_order_id": result.work_order_id,
        "round_index": int(result.round_index),
        "drafts": [role_draft_to_payload(d) for d in result.drafts],
        "peer_review": peer_review_note_to_payload(result.peer_review),
        "consensus_status": result.consensus_status.value,
        "public_summary": result.public_summary,
        "disagreement_summary": result.disagreement_summary,
        "created_at": _datetime_to_iso(result.created_at),
    }


def role_council_result_from_payload(
    payload: Mapping[str, Any],
) -> RoleCouncilResult:
    return RoleCouncilResult(
        role=str(payload["role"]),
        work_order_id=str(payload.get("work_order_id") or ""),
        round_index=int(payload.get("round_index", 1)),
        drafts=tuple(
            role_draft_from_payload(d) for d in (payload.get("drafts") or ())
        ),
        peer_review=peer_review_note_from_payload(
            dict(payload.get("peer_review") or {"role": payload.get("role", ""), "consensus_status": "needs_another_round"})
        ),
        consensus_status=CouncilConsensusStatus(
            str(payload.get("consensus_status") or CouncilConsensusStatus.NEEDS_ANOTHER_ROUND.value)
        ),
        public_summary=str(payload.get("public_summary") or ""),
        disagreement_summary=payload.get("disagreement_summary"),
        created_at=_iso_to_datetime(payload.get("created_at")),
    )


def operator_action_ref_to_payload(ref: OperatorActionRef) -> Mapping[str, Any]:
    return {
        "request_type": ref.request_type,
        "request_id": ref.request_id,
        "title": ref.title,
    }


def operator_action_ref_from_payload(payload: Mapping[str, Any]) -> OperatorActionRef:
    return OperatorActionRef(
        request_type=str(payload.get("request_type") or ""),
        request_id=str(payload.get("request_id") or ""),
        title=payload.get("title"),
    )


def tech_lead_signoff_to_payload(signoff: TechLeadSignoff) -> Mapping[str, Any]:
    return {
        "status": signoff.status.value,
        "rationale": signoff.rationale,
        "conditions": list(signoff.conditions),
        "signed_off_by": signoff.signed_off_by,
        "signed_off_at": _datetime_to_iso(signoff.signed_off_at),
    }


def tech_lead_signoff_from_payload(payload: Mapping[str, Any]) -> TechLeadSignoff:
    return TechLeadSignoff(
        status=TechLeadSignoffStatus(
            str(payload.get("status") or TechLeadSignoffStatus.BLOCKED.value)
        ),
        rationale=str(payload.get("rationale") or ""),
        conditions=tuple(str(c) for c in (payload.get("conditions") or ())),
        signed_off_by=str(payload.get("signed_off_by") or "engineering-agent/tech-lead"),
        signed_off_at=_iso_to_datetime(payload.get("signed_off_at")),
    )


def approval_packet_to_payload(packet: ApprovalPacket) -> Mapping[str, Any]:
    return {
        "packet_id": packet.packet_id,
        "session_id": packet.session_id,
        "task_brief_ref": packet.task_brief_ref,
        "role_council_results": [
            role_council_result_to_payload(r) for r in packet.role_council_results
        ],
        "tech_lead_signoff": tech_lead_signoff_to_payload(packet.tech_lead_signoff),
        "executor_role": packet.executor_role,
        "write_scope": list(packet.write_scope),
        "forbidden_scope": list(packet.forbidden_scope),
        "test_strategy": packet.test_strategy,
        "rollback_plan": packet.rollback_plan,
        "operator_requests": [
            operator_action_ref_to_payload(r) for r in packet.operator_requests
        ],
        "status": packet.status.value,
        "created_at": _datetime_to_iso(packet.created_at),
    }


def approval_packet_from_payload(payload: Mapping[str, Any]) -> ApprovalPacket:
    return ApprovalPacket(
        packet_id=str(payload["packet_id"]),
        session_id=str(payload.get("session_id") or ""),
        task_brief_ref=str(payload.get("task_brief_ref") or ""),
        role_council_results=tuple(
            role_council_result_from_payload(r)
            for r in (payload.get("role_council_results") or ())
        ),
        tech_lead_signoff=tech_lead_signoff_from_payload(
            dict(payload.get("tech_lead_signoff") or {"status": TechLeadSignoffStatus.BLOCKED.value, "rationale": ""})
        ),
        executor_role=str(payload.get("executor_role") or ""),
        write_scope=tuple(str(s) for s in (payload.get("write_scope") or ())),
        forbidden_scope=tuple(str(s) for s in (payload.get("forbidden_scope") or ())),
        test_strategy=str(payload.get("test_strategy") or ""),
        rollback_plan=str(payload.get("rollback_plan") or ""),
        operator_requests=tuple(
            operator_action_ref_from_payload(r)
            for r in (payload.get("operator_requests") or ())
        ),
        status=ApprovalPacketStatus(
            str(payload.get("status") or ApprovalPacketStatus.DRAFT.value)
        ),
        created_at=_iso_to_datetime(payload.get("created_at")),
    )


def task_brief_to_payload(brief: TaskBrief) -> Mapping[str, Any]:
    return {
        "brief_id": brief.brief_id,
        "session_id": brief.session_id,
        "title": brief.title,
        "purpose": brief.purpose,
        "in_scope": list(brief.in_scope),
        "out_of_scope": list(brief.out_of_scope),
        "references": list(brief.references),
        "research_pack_ref": brief.research_pack_ref,
        "work_mode": brief.work_mode,
        "revision": int(brief.revision),
        "created_at": _datetime_to_iso(brief.created_at),
    }


def task_brief_from_payload(payload: Mapping[str, Any]) -> TaskBrief:
    return TaskBrief(
        brief_id=str(payload["brief_id"]),
        session_id=str(payload["session_id"]),
        title=str(payload.get("title") or ""),
        purpose=str(payload.get("purpose") or ""),
        in_scope=tuple(str(item) for item in (payload.get("in_scope") or ())),
        out_of_scope=tuple(
            str(item) for item in (payload.get("out_of_scope") or ())
        ),
        references=tuple(str(item) for item in (payload.get("references") or ())),
        research_pack_ref=payload.get("research_pack_ref"),
        work_mode=payload.get("work_mode"),
        revision=int(payload.get("revision", 1)),
        created_at=_iso_to_datetime(payload.get("created_at")),
    )


def role_work_order_to_payload(order: RoleWorkOrder) -> Mapping[str, Any]:
    return {
        "role": order.role,
        "brief_id": order.brief_id,
        "work_order_id": order.work_order_id,
        "purpose": order.purpose,
        "required_outputs": list(order.required_outputs),
        "forbidden_scope": list(order.forbidden_scope),
        "seats": [seat.value for seat in order.seats],
        "council_round_cap": int(order.council_round_cap),
    }


def role_work_order_from_payload(payload: Mapping[str, Any]) -> RoleWorkOrder:
    seats_payload = payload.get("seats") or [s.value for s in DEFAULT_SEATS]
    return RoleWorkOrder(
        role=str(payload["role"]),
        brief_id=str(payload.get("brief_id") or ""),
        work_order_id=str(payload.get("work_order_id") or ""),
        purpose=str(payload.get("purpose") or ""),
        required_outputs=tuple(
            str(item) for item in (payload.get("required_outputs") or ())
        ),
        forbidden_scope=tuple(
            str(item) for item in (payload.get("forbidden_scope") or ())
        ),
        seats=tuple(SeatRole(str(s)) for s in seats_payload),
        council_round_cap=int(
            payload.get("council_round_cap", DEFAULT_COUNCIL_ROUND_CAP)
        ),
    )


# ---------------------------------------------------------------------------
# Role normalization — short ("backend-engineer") ↔ canonical
# ("engineering-agent/backend-engineer") drift guard.
# ---------------------------------------------------------------------------
#
# C3 contract: every council artefact (work_order.role,
# RoleCouncilResult.role, role_councils session.extra key) uses the
# CANONICAL form. Inputs from the router / role_selection / message
# protocol can be either short or canonical; ``canonical_role`` /
# ``short_role`` / ``normalize_roles`` are the single SSoT for
# conversion.

_CANONICAL_PREFIX = "engineering-agent/"


def canonical_role(role: str) -> str:
    """Return the ``engineering-agent/<short>`` form.

    Empty / falsy inputs become an empty string so callers can filter.
    Already-canonical inputs are returned unchanged. A role with a
    different prefix (e.g. ``cto-agent/...``) is preserved verbatim —
    council ownership is per-department, not global.
    """

    text = (role or "").strip()
    if not text:
        return ""
    if "/" in text:
        return text
    return f"{_CANONICAL_PREFIX}{text}"


def short_role(role: str) -> str:
    """``engineering-agent/backend-engineer`` → ``backend-engineer``."""

    text = (role or "").strip()
    if not text:
        return "unknown-role"
    if "/" in text:
        return text.split("/", 1)[1] or text
    return text


def normalize_roles(roles: Iterable[str]) -> Tuple[str, ...]:
    """Dedup + canonicalize an iterable of role hints.

    Preserves first-seen order. Short and canonical forms of the same
    role collapse into one canonical entry. Empty strings are dropped.
    """

    seen: list[str] = []
    seen_set: set[str] = set()
    for raw in roles or ():
        normal = canonical_role(str(raw))
        if not normal or normal in seen_set:
            continue
        seen.append(normal)
        seen_set.add(normal)
    return tuple(seen)


# ---------------------------------------------------------------------------
# Fallback helpers — public_summary 가 비면 보일러플레이트 1줄 생성
# ---------------------------------------------------------------------------


def _short_role_name(role: str) -> str:
    """Backward-compat alias of :func:`short_role` — kept for older
    council.py callers that imported the private name."""

    return short_role(role)


def fallback_public_summary(
    *,
    role: str,
    round_index: int,
    consensus_status: CouncilConsensusStatus,
    risks: Sequence[str] = (),
    next_actions: Sequence[str] = (),
) -> str:
    """Empty ``public_summary`` 가 user surface 에 노출되지 않게 1줄 보강.

    포함: role 명 + round_index + consensus status + (risks[0] 또는
    next_actions[0]) 1 개. raw 토의 dump 가 아니라 외부 노출용 한 줄.
    """

    short = _short_role_name(role)
    status_label = {
        CouncilConsensusStatus.AGREED: "합의",
        CouncilConsensusStatus.AGREED_WITH_CONDITIONS: "조건부 합의",
        CouncilConsensusStatus.NEEDS_ANOTHER_ROUND: "추가 라운드 필요",
        CouncilConsensusStatus.ESCALATED: "tech-lead escalate",
    }.get(consensus_status, consensus_status.value)
    extra = ""
    if risks:
        extra = f" / 핵심 리스크: {str(risks[0])}"
    elif next_actions:
        extra = f" / 다음 행동: {str(next_actions[0])}"
    return (
        f"[{short}] round {int(round_index)} — {status_label}{extra}"
    )


def ensure_public_summary(
    summary: Optional[str],
    *,
    role: str,
    round_index: int,
    consensus_status: CouncilConsensusStatus,
    risks: Sequence[str] = (),
    next_actions: Sequence[str] = (),
) -> str:
    """Return *summary* if non-blank else :func:`fallback_public_summary`."""

    text = (summary or "").strip()
    if text:
        return text
    return fallback_public_summary(
        role=role,
        round_index=round_index,
        consensus_status=consensus_status,
        risks=risks,
        next_actions=next_actions,
    )


# ---------------------------------------------------------------------------
# Disagreement summary guard — never empty when status != AGREED*
# ---------------------------------------------------------------------------


def ensure_disagreement_summary(
    summary: Optional[str],
    *,
    role: str,
    round_index: int,
    consensus_status: CouncilConsensusStatus,
    open_questions: Sequence[str] = (),
    risks: Sequence[str] = (),
) -> Optional[str]:
    """Return a non-empty disagreement summary for ESCALATED /
    NEEDS_ANOTHER_ROUND. Settled statuses pass through as-is (may be
    None).

    Empty summaries on an unsettled council are forbidden — the C3
    contract: reviewer must always tell tech-lead *why* it could not
    settle. If the caller passes blank, this helper synthesises a 1-line
    fallback from open_questions / risks.
    """

    if consensus_status in (
        CouncilConsensusStatus.AGREED,
        CouncilConsensusStatus.AGREED_WITH_CONDITIONS,
    ):
        text = (summary or "").strip()
        return text or None

    text = (summary or "").strip()
    if text:
        return text

    short = short_role(role)
    parts = [f"[{short}] round {int(round_index)} 미합의"]
    if open_questions:
        parts.append(f"open_questions: {open_questions[0]}")
    elif risks:
        parts.append(f"risks: {risks[0]}")
    else:
        parts.append("reviewer 가 구체적 사유를 남기지 않음")
    return " — ".join(parts)


# ---------------------------------------------------------------------------
# session.extra round-trip — list[RoleCouncilResult] flat helpers
# ---------------------------------------------------------------------------


def role_councils_to_extra(
    results: Iterable[RoleCouncilResult],
) -> Mapping[str, list[Mapping[str, Any]]]:
    """role → list[serialized result] (모든 round 보존)."""

    out: dict[str, list[Mapping[str, Any]]] = {}
    for result in results:
        out.setdefault(result.role, []).append(role_council_result_to_payload(result))
    return out


def role_councils_from_extra(
    payload: Mapping[str, Any],
) -> Tuple[RoleCouncilResult, ...]:
    """역방향 — flat list of RoleCouncilResult (역할 별 round 누적 포함)."""

    if not isinstance(payload, Mapping):
        return ()
    out: list[RoleCouncilResult] = []
    for results in payload.values():
        if not isinstance(results, Iterable):
            continue
        for item in results:
            if isinstance(item, Mapping):
                try:
                    out.append(role_council_result_from_payload(item))
                except Exception:  # noqa: BLE001
                    continue
    return tuple(out)


__all__ = [
    # enums
    "SeatRole",
    "CouncilConsensusStatus",
    "ApprovalPacketStatus",
    "TechLeadSignoffStatus",
    "ExecutionReviewDecision",
    "CIBucketStatus",
    "RoleCouncilRecheckStatus",
    "RetrospectiveCandidateSource",
    # dataclasses
    "TaskBrief",
    "RoleWorkOrder",
    "RoleDraft",
    "PeerReviewNote",
    "RoleCouncilResult",
    "OperatorActionRef",
    "TechLeadSignoff",
    "ApprovalPacket",
    "RoleCouncilRecheck",
    "ExecutionReview",
    "RetrospectiveCandidate",
    # substage constants
    "SUBSTAGE_ROLE_BRIEF_DISTRIBUTED",
    "SUBSTAGE_ROLE_DRAFTS_IN_PROGRESS",
    "SUBSTAGE_PEER_REVIEW_PENDING",
    "SUBSTAGE_COUNCIL_ROUND_COMPLETE",
    "SUBSTAGE_COUNCIL_ESCALATED",
    "SUBSTAGE_COUNCIL_READY_FOR_SYNTHESIS",
    "SUBSTAGE_TECH_LEAD_SYNTHESIS",
    "SUBSTAGE_APPROVAL_PACKET_DRAFTED",
    "SUBSTAGE_APPROVAL_SURFACE_POSTED",
    "SUBSTAGE_CI_SIGNAL_RECEIVED",
    "SUBSTAGE_ROLE_COUNCIL_RECONVENED",
    "SUBSTAGE_REVIEW_FEEDBACK_ROUTED",
    "SUBSTAGE_RETROSPECTIVE_CANDIDATE",
    "DELIBERATION_SUBSTAGES",
    "SYNTHESIS_SUBSTAGES",
    "EXECUTION_REVIEW_SUBSTAGES",
    "ALL_SUBSTAGES",
    "DEFAULT_SEATS",
    "DEFAULT_COUNCIL_ROUND_CAP",
    # helpers
    "is_valid_substage",
    "ready_for_synthesis",
    "synthesis_block_reason",
    "must_escalate_to_tech_lead",
    "can_create_approval_packet",
    # payload round-trip
    "task_brief_to_payload",
    "task_brief_from_payload",
    "role_work_order_to_payload",
    "role_work_order_from_payload",
    "role_draft_to_payload",
    "role_draft_from_payload",
    "peer_review_note_to_payload",
    "peer_review_note_from_payload",
    "role_council_result_to_payload",
    "role_council_result_from_payload",
    "role_councils_to_extra",
    "role_councils_from_extra",
    # fallback / summary
    "fallback_public_summary",
    "ensure_public_summary",
    "ensure_disagreement_summary",
    # role normalization
    "canonical_role",
    "short_role",
    "normalize_roles",
    # approval packet payload round-trip
    "operator_action_ref_to_payload",
    "operator_action_ref_from_payload",
    "tech_lead_signoff_to_payload",
    "tech_lead_signoff_from_payload",
    "approval_packet_to_payload",
    "approval_packet_from_payload",
    # multi-role escalation
    "EscalationAggregate",
    "aggregate_escalations",
    "escalation_aggregate_to_payload",
]
