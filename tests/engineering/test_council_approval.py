"""Council C4 approval-packet tests.

Nine C4 requirements, pinned individually:

1. settled council + tech-lead signoff → ApprovalPacket 생성 가능
2. escalated council 존재 → ApprovalPacket 생성 불가 (block reason 반환)
3. CONDITIONAL signoff → packet status = CONDITIONAL
4. signoff 없으면 packet READY 금지 (block reason: tech_lead_signoff_missing)
5. multi-role escalated → aggregator 가 per-role summary 반환
6. gateway surface payload 가 [기술]/[운영] 두 섹션으로 분리
7. active_research_roles 가 새 저장 경로에서 canonical 형으로 저장
8. unavailable provider 후보가 matrix availability metadata 에 표시
9. C1-C3 회귀 없음 (settled bootstrap → ready substage 유지)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


import unittest

from yule_engineering.agents.council import (
    ApprovalPacketStatus,
    CouncilConsensusStatus,
    EscalationAggregate,
    OperatorActionRef,
    TechLeadSignoff,
    TechLeadSignoffStatus,
    aggregate_escalations,
    approval_packet_from_payload,
    approval_packet_to_payload,
    canonical_role,
    normalize_roles,
)
from yule_engineering.agents.council_approval import (
    APPROVAL_PACKET_EXTRA_KEY,
    apply_tech_lead_signoff,
    build_approval_packet,
    build_gateway_surface_payload,
    draft_packet_from_session_extra,
    post_gateway_surface,
    read_approval_packet,
    read_role_councils,
    read_tech_lead_signoff,
    refresh_escalation_aggregate,
)
from yule_engineering.agents.council_bootstrap import (
    PROVIDER_AVAILABILITY_EXTRA_KEY,
    PROVIDER_SEAT_MATRIX_EXTRA_KEY,
    ROLE_WORK_ORDERS_EXTRA_KEY,
    TASK_BRIEF_EXTRA_KEY,
    advance_council_round,
    bootstrap_council,
    build_deterministic_role_council,
    build_provider_availability,
    build_provider_seat_matrix,
    build_role_work_orders,
    build_task_brief,
    role_work_order_to_payload,
    task_brief_to_payload,
)
from yule_engineering.agents.lifecycle.council_status_signals import (
    APPROVAL_PACKET_DRAFTED,
    APPROVAL_SURFACE_POSTED,
    COUNCIL_ESCALATED_CODE,
    COUNCIL_READY_FOR_SYNTHESIS_CODE,
    TECH_LEAD_SIGNOFF_BLOCKED,
    collect_council_signals,
)
from yule_engineering.agents.lifecycle.council_substage import (
    COUNCIL_ESCALATION_AGGREGATE_EXTRA_KEY,
    GATEWAY_SURFACE_PAYLOAD_EXTRA_KEY,
    LIFECYCLE_SUBSTAGE_EXTRA_KEY,
    ROLE_COUNCILS_EXTRA_KEY,
    SUBSTAGE_APPROVAL_PACKET_DRAFTED,
    SUBSTAGE_APPROVAL_SURFACE_POSTED,
    SUBSTAGE_COUNCIL_READY_FOR_SYNTHESIS,
    SUBSTAGE_TECH_LEAD_SYNTHESIS,
    TECH_LEAD_SIGNOFF_EXTRA_KEY,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class _FakeSession:
    session_id: str
    extra: dict = field(default_factory=dict)
    role_sequence: tuple = ()


def _make_settled_session_extra(*roles: str) -> dict:
    b = bootstrap_council(
        session_id="settled",
        canonical_prompt="P",
        active_roles=list(roles) or ["backend-engineer"],
    )
    return dict(b.extras_update)


def _make_escalated_session_extra(role: str = "backend-engineer") -> dict:
    brief = build_task_brief(session_id="esc", title="X", purpose="P")
    orders = build_role_work_orders(brief, [role])
    r2 = build_deterministic_role_council(
        work_order=orders[0],
        brief=brief,
        round_index=2,
        consensus_status=CouncilConsensusStatus.ESCALATED,
        disagreement_summary=f"{role} 합의 실패",
    )
    from yule_engineering.agents.council import role_councils_to_extra

    return {
        TASK_BRIEF_EXTRA_KEY: dict(task_brief_to_payload(brief)),
        ROLE_WORK_ORDERS_EXTRA_KEY: [
            dict(role_work_order_to_payload(o)) for o in orders
        ],
        ROLE_COUNCILS_EXTRA_KEY: {
            role_id: [dict(p) for p in payloads]
            for role_id, payloads in role_councils_to_extra([r2]).items()
        },
    }


# ---------------------------------------------------------------------------
# REQ 1 — settled council + signoff → packet
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# REQ 2 — escalated council blocks packet creation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# REQ 3 — CONDITIONAL signoff → CONDITIONAL packet
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# REQ 4 — packet READY forbidden without signoff
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# REQ 5 — multi-role escalation aggregator
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# REQ 6 — gateway surface payload split
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# REQ 7 — canonical role storage on the new persistence path
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# REQ 8 — provider availability metadata
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# REQ 9 — C1-C3 regression: settled bootstrap unchanged behaviour
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Status surface — packet-flow info + signoff-blocked failure
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Router glue smoke tests
# ---------------------------------------------------------------------------


class CouncilApprovalTests(unittest.TestCase):
    def test_settled_council_and_signoff_yield_packet(self) -> None:
        extra = _make_settled_session_extra("backend-engineer", "qa-engineer")
        signoff = TechLeadSignoff(
            status=TechLeadSignoffStatus.SIGNED_OFF,
            rationale="council 합의 OK",
        )
        outcome = draft_packet_from_session_extra(extra, signoff=signoff)
        assert outcome.created
        assert outcome.block_reason is None
        assert outcome.packet.status is ApprovalPacketStatus.READY
        # Packet refers to two role councils (latest round only).
        assert len(outcome.packet.role_council_results) == 2
        # Executor defaults to a non-tech-lead settled role.
        assert outcome.packet.executor_role in (
            "engineering-agent/backend-engineer",
            "engineering-agent/qa-engineer",
        )


    def test_packet_payload_round_trips_through_extras(self) -> None:
        extra = _make_settled_session_extra("backend-engineer")
        signoff = TechLeadSignoff(
            status=TechLeadSignoffStatus.SIGNED_OFF, rationale="OK"
        )
        outcome = draft_packet_from_session_extra(extra, signoff=signoff)
        assert outcome.packet is not None
        payload = approval_packet_to_payload(outcome.packet)
        rebuilt = approval_packet_from_payload(payload)
        assert rebuilt.session_id == outcome.packet.session_id
        assert rebuilt.status is outcome.packet.status
        assert rebuilt.tech_lead_signoff.status is TechLeadSignoffStatus.SIGNED_OFF


    def test_escalated_council_blocks_packet_creation(self) -> None:
        extra = _make_escalated_session_extra("backend-engineer")
        signoff = TechLeadSignoff(
            status=TechLeadSignoffStatus.SIGNED_OFF,
            rationale="this signoff should be ignored",
        )
        outcome = draft_packet_from_session_extra(extra, signoff=signoff)
        assert not outcome.created
        assert outcome.block_reason is not None
        assert outcome.block_reason.startswith("council_not_settled")


    def test_packet_blocked_when_no_council_history(self) -> None:
        extra = {TASK_BRIEF_EXTRA_KEY: dict(task_brief_to_payload(
            build_task_brief(session_id="x", title="T", purpose="P")
        ))}
        signoff = TechLeadSignoff(
            status=TechLeadSignoffStatus.SIGNED_OFF, rationale="OK"
        )
        outcome = draft_packet_from_session_extra(extra, signoff=signoff)
        assert outcome.block_reason == "role_councils_missing"


    def test_conditional_signoff_yields_conditional_packet(self) -> None:
        extra = _make_settled_session_extra("backend-engineer")
        signoff = TechLeadSignoff(
            status=TechLeadSignoffStatus.CONDITIONAL,
            rationale="feature flag off + 회귀 ≥3",
            conditions=("feature flag off", "회귀 ≥3 케이스"),
        )
        outcome = draft_packet_from_session_extra(extra, signoff=signoff)
        assert outcome.created
        assert outcome.packet.status is ApprovalPacketStatus.CONDITIONAL
        # The conditions list flows through to the packet's signoff.
        assert outcome.packet.tech_lead_signoff.conditions == (
            "feature flag off",
            "회귀 ≥3 케이스",
        )


    def test_packet_blocked_when_signoff_missing(self) -> None:
        extra = _make_settled_session_extra("backend-engineer")
        outcome = draft_packet_from_session_extra(extra)  # signoff=None
        assert not outcome.created
        assert outcome.block_reason == "tech_lead_signoff_missing"


    def test_packet_blocked_when_signoff_status_blocked(self) -> None:
        extra = _make_settled_session_extra("backend-engineer")
        signoff = TechLeadSignoff(
            status=TechLeadSignoffStatus.BLOCKED, rationale="역할 충돌"
        )
        outcome = draft_packet_from_session_extra(extra, signoff=signoff)
        assert not outcome.created
        assert outcome.block_reason == "tech_lead_signoff_blocked"


    def test_packet_blocked_when_signoff_status_escalated(self) -> None:
        extra = _make_settled_session_extra("backend-engineer")
        signoff = TechLeadSignoff(
            status=TechLeadSignoffStatus.ESCALATED, rationale="user input needed"
        )
        outcome = draft_packet_from_session_extra(extra, signoff=signoff)
        assert not outcome.created
        assert outcome.block_reason == "tech_lead_signoff_escalated"


    def test_apply_signoff_only_advances_substage_for_settled_statuses(self) -> None:
        extra = _make_settled_session_extra("backend-engineer")
        # CONDITIONAL → advance
        cond = apply_tech_lead_signoff(
            extra,
            TechLeadSignoff(
                status=TechLeadSignoffStatus.CONDITIONAL, rationale="x"
            ),
        )
        assert cond[LIFECYCLE_SUBSTAGE_EXTRA_KEY] == SUBSTAGE_TECH_LEAD_SYNTHESIS
        # BLOCKED → no substage advance (audit only).
        blocked = apply_tech_lead_signoff(
            extra,
            TechLeadSignoff(
                status=TechLeadSignoffStatus.BLOCKED, rationale="x"
            ),
        )
        assert LIFECYCLE_SUBSTAGE_EXTRA_KEY not in blocked


    def test_aggregate_escalations_returns_per_role_summary(self) -> None:
        brief = build_task_brief(session_id="x", title="T", purpose="P")
        orders = build_role_work_orders(brief, ["backend-engineer", "frontend-engineer"])
        a = build_deterministic_role_council(
            work_order=orders[0],
            brief=brief,
            round_index=2,
            consensus_status=CouncilConsensusStatus.ESCALATED,
            disagreement_summary="auth 흐름 미합의",
        )
        b = build_deterministic_role_council(
            work_order=orders[1],
            brief=brief,
            round_index=2,
            consensus_status=CouncilConsensusStatus.ESCALATED,
            disagreement_summary="state model 미합의",
        )
        aggregate = aggregate_escalations([a, b])
        assert aggregate.escalated_roles == (
            "engineering-agent/backend-engineer",
            "engineering-agent/frontend-engineer",
        )
        assert aggregate.per_role_summary[
            "engineering-agent/backend-engineer"
        ] == "auth 흐름 미합의"
        assert aggregate.per_role_summary[
            "engineering-agent/frontend-engineer"
        ] == "state model 미합의"
        assert aggregate.highest_round_index == 2
        assert aggregate.recommended_next_owner == "engineering-agent/tech-lead"


    def test_aggregate_uses_latest_round_per_role(self) -> None:
        # Round 1 escalated, round 2 settled → latest wins, no escalation.
        brief = build_task_brief(session_id="x", title="T", purpose="P")
        orders = build_role_work_orders(brief, ["backend-engineer"])
        r1 = build_deterministic_role_council(
            work_order=orders[0],
            brief=brief,
            round_index=1,
            consensus_status=CouncilConsensusStatus.ESCALATED,
        )
        r2 = build_deterministic_role_council(
            work_order=orders[0],
            brief=brief,
            round_index=2,
            consensus_status=CouncilConsensusStatus.AGREED,
        )
        aggregate = aggregate_escalations([r1, r2])
        assert aggregate.is_empty()


    def test_advance_council_round_stamps_multi_role_aggregate(self) -> None:
        # Drive two roles into round-2 escalation, then check the aggregate
        # appears on session.extra (router-level reader uses this).
        brief = build_task_brief(session_id="x", title="T", purpose="P")
        orders = build_role_work_orders(brief, ["backend-engineer", "frontend-engineer"])
        from yule_engineering.agents.council import role_councils_to_extra

        r1_a = build_deterministic_role_council(
            work_order=orders[0],
            brief=brief,
            round_index=1,
            consensus_status=CouncilConsensusStatus.NEEDS_ANOTHER_ROUND,
        )
        r1_b = build_deterministic_role_council(
            work_order=orders[1],
            brief=brief,
            round_index=1,
            consensus_status=CouncilConsensusStatus.NEEDS_ANOTHER_ROUND,
        )
        extra = {
            TASK_BRIEF_EXTRA_KEY: dict(task_brief_to_payload(brief)),
            ROLE_WORK_ORDERS_EXTRA_KEY: [
                dict(role_work_order_to_payload(o)) for o in orders
            ],
            ROLE_COUNCILS_EXTRA_KEY: {
                r: [dict(p) for p in payloads]
                for r, payloads in role_councils_to_extra([r1_a, r1_b]).items()
            },
        }
        # Advance backend → round 2 (cap hit → ESCALATED).
        advanced = advance_council_round(extra, role="backend-engineer")
        assert advanced is not None
        merged = {**extra, **dict(advanced.extras_update)}
        # Advance frontend → round 2 (also cap hit).
        advanced2 = advance_council_round(merged, role="frontend-engineer")
        assert advanced2 is not None
        final = {**merged, **dict(advanced2.extras_update)}
        aggregate_payload = final[COUNCIL_ESCALATION_AGGREGATE_EXTRA_KEY]
        assert sorted(aggregate_payload["escalated_roles"]) == [
            "engineering-agent/backend-engineer",
            "engineering-agent/frontend-engineer",
        ]
        assert aggregate_payload["highest_round_index"] == 2
        assert (
            aggregate_payload["recommended_next_owner"]
            == "engineering-agent/tech-lead"
        )


    def test_refresh_escalation_aggregate_silent_when_no_escalation(self) -> None:
        extra = _make_settled_session_extra("backend-engineer")
        # No role escalated → aggregate refresh returns empty.
        assert refresh_escalation_aggregate(extra) == {}


    def test_gateway_surface_payload_starts_with_technical_then_operator(self) -> None:
        extra = _make_settled_session_extra("backend-engineer")
        signoff = TechLeadSignoff(
            status=TechLeadSignoffStatus.CONDITIONAL,
            rationale="회귀 ≥3 추가",
            conditions=("회귀 ≥3",),
        )
        outcome = draft_packet_from_session_extra(extra, signoff=signoff)
        surface = build_gateway_surface_payload(outcome.packet)
        assert surface.technical_signoff_summary.startswith("[기술]")
        # Conditional signoff always surfaces the operator line.
        assert surface.operator_approval_request is not None
        assert surface.operator_approval_request.startswith("[운영]")
        # Conditions copied through.
        assert surface.technical_conditions == ("회귀 ≥3",)


    def test_signed_off_packet_has_no_operator_line_when_no_external_actions(self) -> None:
        extra = _make_settled_session_extra("backend-engineer")
        signoff = TechLeadSignoff(
            status=TechLeadSignoffStatus.SIGNED_OFF, rationale="OK"
        )
        outcome = draft_packet_from_session_extra(extra, signoff=signoff)
        surface = build_gateway_surface_payload(outcome.packet)
        # No operator_requests + signoff = SIGNED_OFF → operator line omitted
        # (the technical signoff itself is the authority).
        assert surface.operator_approval_request is None


    def test_post_gateway_surface_advances_substage_and_stamps_payload(self) -> None:
        extra = _make_settled_session_extra("backend-engineer")
        signoff = TechLeadSignoff(
            status=TechLeadSignoffStatus.SIGNED_OFF, rationale="OK"
        )
        outcome = draft_packet_from_session_extra(extra, signoff=signoff)
        updates = post_gateway_surface(outcome.packet)
        assert updates[LIFECYCLE_SUBSTAGE_EXTRA_KEY] == SUBSTAGE_APPROVAL_SURFACE_POSTED
        surface_payload = updates[GATEWAY_SURFACE_PAYLOAD_EXTRA_KEY]
        assert surface_payload["technical_signoff_summary"].startswith("[기술]")


    def test_persist_role_selection_normalises_to_canonical_form(self) -> None:
        # _persist_role_selection lives in the router's session_persistence
        # module. We invoke it directly with a stub session to verify the
        # canonicalisation hook lands.
        from yule_engineering.discord.engineering_channel_router.session_persistence import (
            _persist_role_selection,
        )

        session = _FakeSession(session_id="s", extra={})
        # The prompt deliberately matches a role-selection rule so the
        # selector picks at least one role. "기능 구현" + "API" hits the
        # backend rule. Worst case the fallback team is picked — either way
        # the stored form must be canonical.
        _persist_role_selection(
            session,
            "backend API 새 endpoint 추가",
        )
        active = session.extra.get("active_research_roles") or []
        assert active, "expected role_selection to pick at least one role"
        for role in active:
            # Either canonical form, or empty / unknown — but no bare short
            # form like 'backend-engineer'.
            assert role.startswith("engineering-agent/") or "/" in role, (
                f"role {role!r} not in canonical form"
            )


    def test_normalize_roles_strips_duplicates_across_forms(self) -> None:
        out = normalize_roles(
            ["backend-engineer", "engineering-agent/backend-engineer", "qa-engineer"]
        )
        assert out == (
            "engineering-agent/backend-engineer",
            "engineering-agent/qa-engineer",
        )


    def test_build_provider_availability_marks_unavailable_candidates(self) -> None:
        matrix = build_provider_seat_matrix(["backend-engineer"])
        availability = build_provider_availability(
            matrix, available_providers=["claude"]
        )
        backend = availability["engineering-agent/backend-engineer"]
        assert backend["available"] == ["claude"]
        assert "codex" in backend["unavailable"]
        assert "gemini" in backend["unavailable"]
        assert backend["by_seat"]["owner"]["primary"] == "claude"
        assert backend["by_seat"]["owner"]["available"] is True
        assert backend["by_seat"]["challenger"]["available"] is False


    def test_bootstrap_stamps_availability_when_available_providers_supplied(self) -> None:
        bootstrap = bootstrap_council(
            session_id="s",
            canonical_prompt="P",
            active_roles=["backend-engineer"],
            available_providers=["claude", "ollama"],
        )
        availability = bootstrap.extras_update[PROVIDER_AVAILABILITY_EXTRA_KEY]
        backend = availability["engineering-agent/backend-engineer"]
        assert backend["available"] == ["claude"]
        assert "codex" in backend["unavailable"]


    def test_bootstrap_omits_availability_when_not_supplied(self) -> None:
        bootstrap = bootstrap_council(
            session_id="s",
            canonical_prompt="P",
            active_roles=["backend-engineer"],
        )
        # No available_providers → no availability key (C3 behaviour preserved).
        assert PROVIDER_AVAILABILITY_EXTRA_KEY not in bootstrap.extras_update


    def test_settled_bootstrap_still_lands_on_ready_for_synthesis_substage(self) -> None:
        result = bootstrap_council(
            session_id="legacy",
            canonical_prompt="P",
            active_roles=["backend-engineer", "qa-engineer"],
        )
        assert result.substage == SUBSTAGE_COUNCIL_READY_FOR_SYNTHESIS


    def test_status_signals_still_emit_ready_for_settled_bootstrap(self) -> None:
        bootstrap = bootstrap_council(
            session_id="legacy",
            canonical_prompt="P",
            active_roles=["backend-engineer"],
        )
        sigs = collect_council_signals(bootstrap.extras_update)
        codes = [s.code for s in sigs]
        assert COUNCIL_READY_FOR_SYNTHESIS_CODE in codes


    def test_status_signal_packet_drafted_after_signoff_and_packet_draft(self) -> None:
        extra = _make_settled_session_extra("backend-engineer")
        signoff = TechLeadSignoff(
            status=TechLeadSignoffStatus.SIGNED_OFF, rationale="OK"
        )
        extra.update(apply_tech_lead_signoff(extra, signoff))
        outcome = draft_packet_from_session_extra(extra, signoff=signoff)
        extra.update(outcome.extras_update)
        sigs = collect_council_signals(extra)
        codes = [s.code for s in sigs]
        assert APPROVAL_PACKET_DRAFTED in codes


    def test_status_signal_surface_posted_after_post_gateway(self) -> None:
        extra = _make_settled_session_extra("backend-engineer")
        signoff = TechLeadSignoff(
            status=TechLeadSignoffStatus.SIGNED_OFF, rationale="OK"
        )
        extra.update(apply_tech_lead_signoff(extra, signoff))
        outcome = draft_packet_from_session_extra(extra, signoff=signoff)
        extra.update(outcome.extras_update)
        extra.update(post_gateway_surface(outcome.packet))
        sigs = collect_council_signals(extra)
        codes = [s.code for s in sigs]
        assert APPROVAL_SURFACE_POSTED in codes


    def test_status_signal_signoff_blocked_when_signoff_blocked(self) -> None:
        extra = _make_settled_session_extra("backend-engineer")
        blocked = TechLeadSignoff(
            status=TechLeadSignoffStatus.BLOCKED, rationale="역할 충돌 미해결"
        )
        extra.update(apply_tech_lead_signoff(extra, blocked))
        sigs = collect_council_signals(extra)
        codes = [s.code for s in sigs]
        assert TECH_LEAD_SIGNOFF_BLOCKED in codes


    def test_router_glue_full_pipeline_stamps_packet_and_surface(self) -> None:
        from yule_engineering.discord.engineering_channel_router.council_flow import (
            apply_signoff_to_session,
            draft_approval_packet_for_session,
            maybe_bootstrap_council,
            post_approval_surface_for_session,
        )

        session = _FakeSession(
            session_id="router-sess",
            extra={"active_research_roles": ["engineering-agent/backend-engineer"]},
        )
        maybe_bootstrap_council(session, canonical_prompt="회원가입")
        signoff = TechLeadSignoff(
            status=TechLeadSignoffStatus.SIGNED_OFF, rationale="OK"
        )
        apply_signoff_to_session(session, signoff=signoff)
        draft_approval_packet_for_session(session, signoff=signoff)
        post_approval_surface_for_session(session)
        # The lifecycle substage walks: ready_for_synthesis → tech_lead_synthesis
        # → approval_packet_drafted → approval_surface_posted.
        assert (
            session.extra[LIFECYCLE_SUBSTAGE_EXTRA_KEY]
            == SUBSTAGE_APPROVAL_SURFACE_POSTED
        )
        # Packet + signoff + surface payload are all stamped.
        assert session.extra[APPROVAL_PACKET_EXTRA_KEY]["status"] == "ready"
        assert session.extra[TECH_LEAD_SIGNOFF_EXTRA_KEY]["status"] == "signed_off"
        assert session.extra[GATEWAY_SURFACE_PAYLOAD_EXTRA_KEY][
            "technical_signoff_summary"
        ].startswith("[기술]")


    def test_router_draft_packet_stamps_bootstrap_error_when_signoff_missing(self) -> None:
        from yule_engineering.discord.engineering_channel_router.council_flow import (
            draft_approval_packet_for_session,
            maybe_bootstrap_council,
        )
        from yule_engineering.agents.lifecycle.council_substage import (
            COUNCIL_BOOTSTRAP_ERROR_EXTRA_KEY,
        )

        session = _FakeSession(
            session_id="router-sess-2",
            extra={"active_research_roles": ["engineering-agent/backend-engineer"]},
        )
        maybe_bootstrap_council(session, canonical_prompt="회원가입")
        # No signoff applied → draft refuses with a clear reason.
        draft_approval_packet_for_session(session)
        err = session.extra.get(COUNCIL_BOOTSTRAP_ERROR_EXTRA_KEY)
        assert err and "tech_lead_signoff_missing" in err


if __name__ == "__main__":
    unittest.main()
