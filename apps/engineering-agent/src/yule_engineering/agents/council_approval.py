"""Approval-packet pipeline — C4 wiring.

Builds the bridge from *settled council results + tech-lead signoff* to
the **first ApprovalPacket stamp** on ``session.extra``. The gateway
surface payload (technical vs operator split) is also produced here so
the Discord card layer can render ``[기술]`` / ``[운영]`` prefixes
without re-reading the council vocabulary.

Hard rails (C4 contract):

- ApprovalPacket cannot be created while *any* role council is
  ESCALATED or NEEDS_ANOTHER_ROUND (``can_create_approval_packet``).
- Without a ``TechLeadSignoff`` the packet stays at ``DRAFT`` — never
  promoted to ``READY``.
- ``signoff.status = BLOCKED`` returns ``None`` (no packet).
- ``signoff.status = CONDITIONAL`` produces a packet with
  ``ApprovalPacketStatus.CONDITIONAL`` and the signoff's conditions
  copied into the packet (visible to gateway / operator surface).
- ``signoff.status = ESCALATED`` returns ``None`` and a 1-line reason;
  caller should escalate to tech-lead instead of opening a packet.
- The gateway *operator approval card* is **always** derived from the
  packet — it never duplicates the technical decision text. Two distinct
  surface lines:

    [기술] tech-lead signoff: <status> — <rationale>
    [운영] operator approval: <kind> — <expected_answer>

This keeps approval matrix L3/L4 ownership at gateway while the
technical decision lives on tech-lead's signoff field.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Tuple

from .council import (
    ApprovalPacket,
    ApprovalPacketStatus,
    CouncilConsensusStatus,
    EscalationAggregate,
    OperatorActionRef,
    RoleCouncilResult,
    TaskBrief,
    TechLeadSignoff,
    TechLeadSignoffStatus,
    aggregate_escalations,
    approval_packet_from_payload,
    approval_packet_to_payload,
    can_create_approval_packet,
    canonical_role,
    escalation_aggregate_to_payload,
    ready_for_synthesis,
    role_council_result_from_payload,
    short_role,
    synthesis_block_reason,
    task_brief_from_payload,
    tech_lead_signoff_from_payload,
    tech_lead_signoff_to_payload,
)
from .lifecycle.council_substage import (
    COUNCIL_ESCALATION_AGGREGATE_EXTRA_KEY,
    GATEWAY_SURFACE_PAYLOAD_EXTRA_KEY,
    LIFECYCLE_SUBSTAGE_EXTRA_KEY,
    ROLE_COUNCILS_EXTRA_KEY,
    SUBSTAGE_APPROVAL_PACKET_DRAFTED,
    SUBSTAGE_APPROVAL_SURFACE_POSTED,
    SUBSTAGE_TECH_LEAD_SYNTHESIS,
    TECH_LEAD_SIGNOFF_EXTRA_KEY,
)


APPROVAL_PACKET_EXTRA_KEY = "approval_packet"
"""``session.extra`` key holding the serialized :class:`ApprovalPacket`."""

TASK_BRIEF_EXTRA_KEY = "task_brief"
ROLE_WORK_ORDERS_EXTRA_KEY = "role_work_orders"


# ---------------------------------------------------------------------------
# Packet build
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PacketBuildOutcome:
    """Either a packet (with persistence-ready extras), or a reason
    string explaining why no packet was created."""

    packet: Optional[ApprovalPacket] = None
    block_reason: Optional[str] = None
    extras_update: Mapping[str, Any] = field(default_factory=dict)

    @property
    def created(self) -> bool:
        return self.packet is not None


def _status_for_signoff(signoff: TechLeadSignoff) -> ApprovalPacketStatus:
    """Map TechLeadSignoffStatus → ApprovalPacketStatus.

    SIGNED_OFF → READY (gateway can open the operator card).
    CONDITIONAL → CONDITIONAL (gateway includes condition list).
    BLOCKED → caller drops the packet entirely.
    ESCALATED → caller drops the packet entirely.
    """

    if signoff.status is TechLeadSignoffStatus.SIGNED_OFF:
        return ApprovalPacketStatus.READY
    if signoff.status is TechLeadSignoffStatus.CONDITIONAL:
        return ApprovalPacketStatus.CONDITIONAL
    return ApprovalPacketStatus.DRAFT


def _resolve_executor(
    *,
    executor_role: Optional[str],
    council_results: Sequence[RoleCouncilResult],
) -> str:
    """Pick a single executor. C4 contract: caller-provided or first
    non-tech-lead settled role. Tech-lead is *never* a code executor by
    default — its role is the technical signoff owner."""

    if executor_role:
        return canonical_role(executor_role)
    for r in council_results:
        if short_role(r.role) != "tech-lead":
            return r.role
    if council_results:
        return council_results[0].role
    return ""


def _open_risks_from(results: Sequence[RoleCouncilResult]) -> Tuple[str, ...]:
    """Collect open questions / risks across settled councils — surfaced
    to the operator so a CONDITIONAL signoff makes sense in context."""

    out: list[str] = []
    seen: set[str] = set()
    for r in results:
        for risk in r.peer_review.open_questions:
            text = (risk or "").strip()
            if not text or text in seen:
                continue
            out.append(f"[{short_role(r.role)}] {text}")
            seen.add(text)
        for draft in r.drafts:
            for risk in draft.risks:
                text = (risk or "").strip()
                if not text or text in seen:
                    continue
                out.append(f"[{short_role(r.role)}] {text}")
                seen.add(text)
                if len(out) >= 12:  # cap to keep packet payload bounded
                    return tuple(out)
    return tuple(out)


def build_approval_packet(
    *,
    session_id: str,
    task_brief: TaskBrief,
    council_results: Sequence[RoleCouncilResult],
    tech_lead_signoff: Optional[TechLeadSignoff],
    executor_role: Optional[str] = None,
    write_scope: Sequence[str] = (),
    forbidden_scope: Sequence[str] = (),
    test_strategy: str = "",
    rollback_plan: str = "",
    operator_requests: Sequence[OperatorActionRef] = (),
) -> PacketBuildOutcome:
    """Pure builder — no IO.

    Returns a :class:`PacketBuildOutcome` whose ``packet`` is None when
    the council prerequisites fail or the signoff says BLOCKED /
    ESCALATED. The ``block_reason`` then tells the caller *why* so it
    can surface that on the status diagnostic instead of silently no-
    op'ing.
    """

    # 1. Council prerequisites — every role settled.
    block = can_create_approval_packet(
        results=council_results,
        tech_lead_signoff=tech_lead_signoff,
    )
    if block is not None:
        return PacketBuildOutcome(block_reason=block)
    # ESCALATED signoff means tech-lead deferred — caller escalates,
    # does *not* draft a packet.
    assert tech_lead_signoff is not None  # for type narrowing; can_create guards
    if tech_lead_signoff.status is TechLeadSignoffStatus.ESCALATED:
        return PacketBuildOutcome(
            block_reason="tech_lead_signoff_escalated"
        )

    status = _status_for_signoff(tech_lead_signoff)
    executor = _resolve_executor(
        executor_role=executor_role,
        council_results=council_results,
    )

    packet = ApprovalPacket(
        packet_id=f"pkt-{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        task_brief_ref=task_brief.brief_id,
        role_council_results=tuple(council_results),
        tech_lead_signoff=tech_lead_signoff,
        executor_role=executor,
        write_scope=tuple(write_scope),
        forbidden_scope=tuple(forbidden_scope),
        test_strategy=test_strategy,
        rollback_plan=rollback_plan,
        operator_requests=tuple(operator_requests),
        status=status,
    )
    extras_update = {
        APPROVAL_PACKET_EXTRA_KEY: dict(approval_packet_to_payload(packet)),
        TECH_LEAD_SIGNOFF_EXTRA_KEY: dict(
            tech_lead_signoff_to_payload(tech_lead_signoff)
        ),
        LIFECYCLE_SUBSTAGE_EXTRA_KEY: SUBSTAGE_APPROVAL_PACKET_DRAFTED,
    }
    return PacketBuildOutcome(packet=packet, extras_update=extras_update)


# ---------------------------------------------------------------------------
# Tech-lead signoff persistence
# ---------------------------------------------------------------------------


def apply_tech_lead_signoff(
    session_extra: Mapping[str, Any],
    signoff: TechLeadSignoff,
) -> Mapping[str, Any]:
    """Return an ``extras_update`` dict that stamps the signoff onto
    ``session.extra``.

    Behaviour by signoff.status:

    - SIGNED_OFF / CONDITIONAL → substage advances to
      ``tech_lead_synthesis``. The actual packet draft is the caller's
      responsibility (see :func:`build_approval_packet`).
    - BLOCKED / ESCALATED → signoff still stamped (audit trail) but no
      substage advance.
    """

    updates: dict[str, Any] = {
        TECH_LEAD_SIGNOFF_EXTRA_KEY: dict(tech_lead_signoff_to_payload(signoff)),
    }
    if signoff.status in (
        TechLeadSignoffStatus.SIGNED_OFF,
        TechLeadSignoffStatus.CONDITIONAL,
    ):
        updates[LIFECYCLE_SUBSTAGE_EXTRA_KEY] = SUBSTAGE_TECH_LEAD_SYNTHESIS
    return updates


def read_tech_lead_signoff(
    session_extra: Mapping[str, Any],
) -> Optional[TechLeadSignoff]:
    payload = session_extra.get(TECH_LEAD_SIGNOFF_EXTRA_KEY)
    if not isinstance(payload, Mapping):
        return None
    try:
        return tech_lead_signoff_from_payload(payload)
    except Exception:  # noqa: BLE001
        return None


def read_approval_packet(
    session_extra: Mapping[str, Any],
) -> Optional[ApprovalPacket]:
    payload = session_extra.get(APPROVAL_PACKET_EXTRA_KEY)
    if not isinstance(payload, Mapping):
        return None
    try:
        return approval_packet_from_payload(payload)
    except Exception:  # noqa: BLE001
        return None


def read_role_councils(
    session_extra: Mapping[str, Any],
) -> Tuple[RoleCouncilResult, ...]:
    payload = session_extra.get(ROLE_COUNCILS_EXTRA_KEY)
    if not isinstance(payload, Mapping):
        return ()
    out: list[RoleCouncilResult] = []
    # Pick the *latest* round per role — settled-at-latest is what feeds
    # the packet. Older rounds remain in session.extra for audit.
    latest_round_by_role: dict[str, RoleCouncilResult] = {}
    for role_key, entries in payload.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            try:
                r = role_council_result_from_payload(entry)
            except Exception:  # noqa: BLE001
                continue
            cur = latest_round_by_role.get(r.role)
            if cur is None or r.round_index >= cur.round_index:
                latest_round_by_role[r.role] = r
    out.extend(latest_round_by_role.values())
    return tuple(out)


def read_task_brief(
    session_extra: Mapping[str, Any],
) -> Optional[TaskBrief]:
    payload = session_extra.get(TASK_BRIEF_EXTRA_KEY)
    if not isinstance(payload, Mapping):
        return None
    try:
        return task_brief_from_payload(payload)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Gateway surface payload — technical signoff vs operator approval split
# ---------------------------------------------------------------------------


_KO_STATUS_LABEL: Mapping[ApprovalPacketStatus, str] = {
    ApprovalPacketStatus.DRAFT: "draft",
    ApprovalPacketStatus.READY: "ready",
    ApprovalPacketStatus.CONDITIONAL: "조건부 합의",
    ApprovalPacketStatus.ESCALATED: "tech-lead escalate",
    ApprovalPacketStatus.ARCHIVED: "archived",
}

_KO_SIGNOFF_LABEL: Mapping[TechLeadSignoffStatus, str] = {
    TechLeadSignoffStatus.SIGNED_OFF: "승인",
    TechLeadSignoffStatus.CONDITIONAL: "조건부 승인",
    TechLeadSignoffStatus.BLOCKED: "보류",
    TechLeadSignoffStatus.ESCALATED: "escalate",
}


@dataclass(frozen=True)
class GatewaySurfacePayload:
    """Split surface payload — two clearly-labelled sections.

    The gateway renders ``#승인-대기`` cards by reading this struct.
    Technical vs operator are *separate strings* so the Discord layer
    cannot accidentally merge them. ``operator_approval_request`` is
    ``None`` when L3/L4 operator action is not yet required (e.g. CI
    has not requested a secret yet).
    """

    technical_signoff_summary: str
    operator_approval_request: Optional[str]
    packet_status: str
    technical_conditions: Tuple[str, ...] = ()
    open_risks: Tuple[str, ...] = ()


def build_gateway_surface_payload(
    packet: ApprovalPacket,
) -> GatewaySurfacePayload:
    """Render a 2-section payload for the gateway.

    The technical line always starts with ``[기술]`` and the operator
    line (if any) with ``[운영]`` so review-loop / status renderers can
    grep without parsing the full packet.
    """

    signoff = packet.tech_lead_signoff
    signoff_label = _KO_SIGNOFF_LABEL.get(signoff.status, signoff.status.value)
    rationale = signoff.rationale.strip() or "(사유 미기재)"
    technical_line = (
        f"[기술] tech-lead signoff: {signoff_label} — {rationale}"
    )

    operator_line: Optional[str] = None
    if packet.operator_requests:
        head = packet.operator_requests[0]
        title = head.title or head.request_id or head.request_type
        operator_line = (
            f"[운영] operator approval: {head.request_type} — {title}"
        )
        if len(packet.operator_requests) > 1:
            operator_line += f" (+{len(packet.operator_requests) - 1} more)"
    elif packet.status is ApprovalPacketStatus.CONDITIONAL:
        # Conditional packet without operator card → the operator still
        # needs to acknowledge the conditions. Make that explicit.
        operator_line = (
            "[운영] operator approval: 조건부 승인 검토 필요 — "
            "tech-lead 조건을 사용자가 확인해야 합니다"
        )

    return GatewaySurfacePayload(
        technical_signoff_summary=technical_line,
        operator_approval_request=operator_line,
        packet_status=_KO_STATUS_LABEL.get(packet.status, packet.status.value),
        technical_conditions=tuple(signoff.conditions),
        open_risks=_open_risks_from(packet.role_council_results),
    )


def gateway_surface_payload_to_dict(
    payload: GatewaySurfacePayload,
) -> Mapping[str, Any]:
    return {
        "technical_signoff_summary": payload.technical_signoff_summary,
        "operator_approval_request": payload.operator_approval_request,
        "packet_status": payload.packet_status,
        "technical_conditions": list(payload.technical_conditions),
        "open_risks": list(payload.open_risks),
    }


def post_gateway_surface(
    packet: ApprovalPacket,
) -> Mapping[str, Any]:
    """Build the extras update that *posts* the surface payload.

    Currently this means: stamp ``GATEWAY_SURFACE_PAYLOAD_EXTRA_KEY`` +
    advance the lifecycle substage to ``approval_surface_posted``. The
    actual Discord card render is the gateway's job — this helper is the
    contract that says "the operator can now see it".
    """

    surface = build_gateway_surface_payload(packet)
    return {
        GATEWAY_SURFACE_PAYLOAD_EXTRA_KEY: dict(
            gateway_surface_payload_to_dict(surface)
        ),
        LIFECYCLE_SUBSTAGE_EXTRA_KEY: SUBSTAGE_APPROVAL_SURFACE_POSTED,
    }


# ---------------------------------------------------------------------------
# Multi-role escalation aggregate — session.extra side
# ---------------------------------------------------------------------------


def refresh_escalation_aggregate(
    session_extra: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Recompute the multi-role escalation aggregate and return the
    extras_update to stamp.

    Returns ``{}`` (no-op) when no role is escalated — caller should
    *not* clear an existing aggregate without rationale (audit trail).
    """

    payload = session_extra.get(ROLE_COUNCILS_EXTRA_KEY)
    if not isinstance(payload, Mapping):
        return {}
    # Flatten history → list[RoleCouncilResult]. We pass full history so
    # the aggregate can pick the latest round per role itself.
    flat: list[RoleCouncilResult] = []
    for entries in payload.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            try:
                flat.append(role_council_result_from_payload(entry))
            except Exception:  # noqa: BLE001
                continue
    aggregate = aggregate_escalations(flat)
    if aggregate.is_empty():
        return {}
    return {
        COUNCIL_ESCALATION_AGGREGATE_EXTRA_KEY: dict(
            escalation_aggregate_to_payload(aggregate)
        ),
    }


# ---------------------------------------------------------------------------
# End-to-end pipeline — read state, build packet, build surface, return
# merged extras update.
# ---------------------------------------------------------------------------


def draft_packet_from_session_extra(
    session_extra: Mapping[str, Any],
    *,
    signoff: Optional[TechLeadSignoff] = None,
    executor_role: Optional[str] = None,
    write_scope: Sequence[str] = (),
    forbidden_scope: Sequence[str] = (),
    test_strategy: str = "",
    rollback_plan: str = "",
    operator_requests: Sequence[OperatorActionRef] = (),
) -> PacketBuildOutcome:
    """Read brief / councils / signoff off ``session.extra`` and build
    the packet in one call.

    If ``signoff`` is None we try to read an existing one from
    ``session.extra[TECH_LEAD_SIGNOFF_EXTRA_KEY]`` so router callers can
    omit it after :func:`apply_tech_lead_signoff` has run.
    """

    brief = read_task_brief(session_extra)
    if brief is None:
        return PacketBuildOutcome(block_reason="task_brief_missing")
    results = read_role_councils(session_extra)
    if not results:
        return PacketBuildOutcome(block_reason="role_councils_missing")
    signoff = signoff or read_tech_lead_signoff(session_extra)
    if signoff is None:
        return PacketBuildOutcome(block_reason="tech_lead_signoff_missing")
    session_id = str(brief.session_id or "")
    return build_approval_packet(
        session_id=session_id,
        task_brief=brief,
        council_results=results,
        tech_lead_signoff=signoff,
        executor_role=executor_role,
        write_scope=write_scope,
        forbidden_scope=forbidden_scope,
        test_strategy=test_strategy,
        rollback_plan=rollback_plan,
        operator_requests=operator_requests,
    )


__all__ = [
    "APPROVAL_PACKET_EXTRA_KEY",
    "PacketBuildOutcome",
    "GatewaySurfacePayload",
    "build_approval_packet",
    "build_gateway_surface_payload",
    "gateway_surface_payload_to_dict",
    "post_gateway_surface",
    "apply_tech_lead_signoff",
    "read_tech_lead_signoff",
    "read_approval_packet",
    "read_role_councils",
    "read_task_brief",
    "refresh_escalation_aggregate",
    "draft_packet_from_session_extra",
]
