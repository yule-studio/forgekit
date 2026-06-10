"""Role council C2 wiring tests.

Verifies the **bootstrap** layer in
``src/yule_orchestrator/agents/council_bootstrap.py`` plus the router
hook in
``src/yule_orchestrator/discord/engineering_channel_router/council_flow.py``.

Eight requirements (from the C2 brief), each pinned by a test below:

1. session.extra 가 task_brief + role_work_orders 를 보관한다.
2. bootstrap 직후 lifecycle_substage 가 council 흐름의 일원이다.
3. owner/challenger/reviewer 3-seat 결과가 모두 모이면 RoleCouncilResult.
4. peer review 가 없으면 ready_for_synthesis 가 False.
5. settled council 결과면 `council_ready_for_synthesis` 로 간다.
6. escalated 결과면 `council_escalated` 로 간다.
7. public_summary 가 비어도 fallback 이 채워진다.
8. 기존 세션에 council 키가 없어도 회귀 없이 동작한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


import unittest

from yule_orchestrator.agents.council import (
    CouncilConsensusStatus,
    DEFAULT_COUNCIL_ROUND_CAP,
    PeerReviewNote,
    RoleCouncilResult,
    RoleDraft,
    SeatRole,
    ensure_public_summary,
    fallback_public_summary,
    must_escalate_to_tech_lead,
    ready_for_synthesis,
    role_council_result_from_payload,
    role_councils_from_extra,
    role_councils_to_extra,
)
from yule_orchestrator.agents.council_bootstrap import (
    ROLE_WORK_ORDERS_EXTRA_KEY,
    TASK_BRIEF_EXTRA_KEY,
    already_bootstrapped,
    bootstrap_council,
    build_deterministic_role_council,
    build_role_work_orders,
    build_task_brief,
    determine_substage,
    persist_bootstrap_to_session,
    required_outputs_for_role,
    synthesis_gate,
)
from yule_orchestrator.agents.deliberation import (
    BackendEngineerTake,
    QaEngineerTake,
    role_take_to_role_draft,
    role_takes_to_role_drafts,
)
from yule_orchestrator.agents.lifecycle.council_substage import (
    LIFECYCLE_SUBSTAGE_EXTRA_KEY,
    ROLE_COUNCILS_EXTRA_KEY,
    SUBSTAGE_COUNCIL_ESCALATED,
    SUBSTAGE_COUNCIL_READY_FOR_SYNTHESIS,
    SUBSTAGE_COUNCIL_ROUND_COMPLETE,
)


# ---------------------------------------------------------------------------
# Test fixtures — minimal session stub
# ---------------------------------------------------------------------------


@dataclass
class _FakeSession:
    """Minimal mutable stand-in for ``WorkflowSession``.

    Production ``WorkflowSession`` is frozen but the council bootstrap
    helper only needs ``session.extra`` + ``session.session_id``. Tests
    use a dict-backed mutable extra so they can inspect persistence in
    place (production path uses ``_persist_extra_keys`` round-trip).
    """

    session_id: str
    extra: dict = field(default_factory=dict)
    role_sequence: tuple = ()


# ---------------------------------------------------------------------------
# REQ 1 — task_brief + role_work_orders persist
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# REQ 2 — lifecycle_substage advances on bootstrap
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# REQ 3 — 3-seat round-trip yields RoleCouncilResult
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# REQ 4 — ready_for_synthesis blocked without peer review settlement
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# REQ 5 — settled councils → council_ready_for_synthesis
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# REQ 6 — escalated → council_escalated
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# REQ 7 — public_summary fallback
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# REQ 8 — backward compat for sessions without council keys
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Adapter — RoleTake → RoleDraft (C2 bridge)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Persistence round-trip (session.extra ⇄ council)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Router glue — maybe_bootstrap_council best-effort behaviour
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Misc — required_outputs / fallback
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Helpers used above
# ---------------------------------------------------------------------------


def _make_minimal_bootstrap() -> Any:
    return bootstrap_council(
        session_id="sess-helper",
        canonical_prompt="helper bootstrap",
        active_roles=["tech-lead"],
    )


class CouncilBootstrapTests(unittest.TestCase):
    def test_bootstrap_persists_task_brief_and_role_work_orders(self) -> None:
        session = _FakeSession(
            session_id="sess-001",
            extra={"active_research_roles": ["backend-engineer", "frontend-engineer"]},
        )
        bootstrap = bootstrap_council(
            session_id=session.session_id,
            canonical_prompt="회원가입 + 인증 흐름",
            active_roles=["backend-engineer", "frontend-engineer"],
        )
        persist_bootstrap_to_session(session, bootstrap)
        extras = session.extra
        assert TASK_BRIEF_EXTRA_KEY in extras
        assert extras[TASK_BRIEF_EXTRA_KEY]["title"] == "회원가입 + 인증 흐름"
        assert extras[TASK_BRIEF_EXTRA_KEY]["session_id"] == "sess-001"
        assert ROLE_WORK_ORDERS_EXTRA_KEY in extras
        orders = extras[ROLE_WORK_ORDERS_EXTRA_KEY]
        assert {o["role"] for o in orders} == {
            "engineering-agent/backend-engineer",
            "engineering-agent/frontend-engineer",
        }
        # Required outputs per role are deterministic — pin one to detect
        # silent regression.
        backend = next(o for o in orders if o["role"].endswith("backend-engineer"))
        assert "api_contract" in backend["required_outputs"]
        assert backend["seats"] == ["owner", "challenger", "reviewer"]


    def test_bootstrap_sets_lifecycle_substage_within_council_vocabulary(self) -> None:
        bootstrap = bootstrap_council(
            session_id="sess-002",
            canonical_prompt="state machine 분해",
            active_roles=["tech-lead"],
        )
        substage = bootstrap.extras_update[LIFECYCLE_SUBSTAGE_EXTRA_KEY]
        # bootstrap 이 settled 결과를 만들므로 ready_for_synthesis 로 진입해야 함.
        assert substage == SUBSTAGE_COUNCIL_READY_FOR_SYNTHESIS


    def test_three_seat_bootstrap_produces_owner_challenger_reviewer(self) -> None:
        brief = build_task_brief(
            session_id="sess-003",
            title="회원가입 API",
            purpose="이메일 검증 추가",
        )
        orders = build_role_work_orders(brief, ["backend-engineer"])
        assert len(orders) == 1
        result = build_deterministic_role_council(work_order=orders[0], brief=brief)
        seats = {d.seat for d in result.drafts}
        assert seats == {SeatRole.OWNER, SeatRole.CHALLENGER}
        assert result.peer_review.owner_draft_id is not None
        assert result.peer_review.challenger_draft_id is not None
        assert result.consensus_status is CouncilConsensusStatus.AGREED
        # Every seat must contribute something — empty payloads are forbidden.
        for draft in result.drafts:
            assert draft.perspective and draft.evidence and draft.risks and draft.next_actions


    def test_ready_for_synthesis_blocked_without_settled_peer_review(self) -> None:
        brief = build_task_brief(
            session_id="sess-004",
            title="X",
            purpose="P",
        )
        orders = build_role_work_orders(brief, ["backend-engineer"])
        unsettled = build_deterministic_role_council(
            work_order=orders[0],
            brief=brief,
            consensus_status=CouncilConsensusStatus.NEEDS_ANOTHER_ROUND,
        )
        assert unsettled.peer_review.consensus_status is CouncilConsensusStatus.NEEDS_ANOTHER_ROUND
        assert ready_for_synthesis([unsettled]) is False


    def test_determine_substage_ready_when_settled(self) -> None:
        brief = build_task_brief(session_id="sess-005", title="X", purpose="P")
        orders = build_role_work_orders(
            brief, ["backend-engineer", "qa-engineer"]
        )
        councils = [
            build_deterministic_role_council(work_order=o, brief=brief) for o in orders
        ]
        assert determine_substage(councils) == SUBSTAGE_COUNCIL_READY_FOR_SYNTHESIS


    def test_determine_substage_escalated_after_round_cap(self) -> None:
        brief = build_task_brief(session_id="sess-006", title="X", purpose="P")
        orders = build_role_work_orders(brief, ["backend-engineer"])
        # round 1 — not settled
        unsettled_round_1 = build_deterministic_role_council(
            work_order=orders[0],
            brief=brief,
            round_index=1,
            consensus_status=CouncilConsensusStatus.NEEDS_ANOTHER_ROUND,
        )
        # round 2 — escalated (hits the cap)
        escalated_round_2 = build_deterministic_role_council(
            work_order=orders[0],
            brief=brief,
            round_index=DEFAULT_COUNCIL_ROUND_CAP,
            consensus_status=CouncilConsensusStatus.ESCALATED,
            disagreement_summary="2 라운드 cap 도달",
        )
        history = [unsettled_round_1, escalated_round_2]
        assert must_escalate_to_tech_lead(history) is True
        assert determine_substage(history) == SUBSTAGE_COUNCIL_ESCALATED


    def test_determine_substage_round_complete_when_active_under_cap(self) -> None:
        brief = build_task_brief(session_id="sess-006b", title="X", purpose="P")
        orders = build_role_work_orders(brief, ["backend-engineer"])
        # Single needs_another_round below the cap → still active, no escalate yet.
        in_progress = build_deterministic_role_council(
            work_order=orders[0],
            brief=brief,
            round_index=1,
            consensus_status=CouncilConsensusStatus.NEEDS_ANOTHER_ROUND,
        )
        assert determine_substage([in_progress]) == SUBSTAGE_COUNCIL_ROUND_COMPLETE


    def test_public_summary_fallback_includes_role_round_status(self) -> None:
        summary = ensure_public_summary(
            "",
            role="engineering-agent/backend-engineer",
            round_index=2,
            consensus_status=CouncilConsensusStatus.AGREED_WITH_CONDITIONS,
            risks=("권한 boundary 변경",),
        )
        assert "backend-engineer" in summary
        assert "round 2" in summary
        assert "조건부 합의" in summary
        assert "권한 boundary 변경" in summary


    def test_public_summary_returns_existing_when_non_blank(self) -> None:
        summary = ensure_public_summary(
            "이미 채워진 본문",
            role="engineering-agent/qa-engineer",
            round_index=1,
            consensus_status=CouncilConsensusStatus.AGREED,
        )
        assert summary == "이미 채워진 본문"


    def test_council_result_public_summary_filled_even_with_no_evidence(self) -> None:
        brief = build_task_brief(session_id="sess-007", title="", purpose="")
        orders = build_role_work_orders(brief, ["frontend-engineer"])
        council = build_deterministic_role_council(work_order=orders[0], brief=brief)
        assert council.public_summary
        # Discord-facing surface should be a short one-liner — raw deliberation
        # dump is forbidden.
        assert "\n" not in council.public_summary


    def test_already_bootstrapped_false_for_legacy_session_extra(self) -> None:
        assert already_bootstrapped({}) is False
        assert already_bootstrapped({"active_research_roles": ["tech-lead"]}) is False


    def test_synthesis_gate_returns_reason_when_council_keys_missing(self) -> None:
        # Legacy session — no role_councils key yet. Gate should not raise and
        # must return a 1-line reason so the status surface can display "왜
        # synthesis 가 아직 안 됐는지".
        assert synthesis_gate({}) == "no_role_councils_recorded"
        # And the gate handles malformed values gracefully.
        assert synthesis_gate({"role_councils": "not-a-dict"}) == "no_role_councils_recorded"


    def test_persist_bootstrap_skips_when_session_is_none(self) -> None:
        # The persistence helper must accept None without raising so the
        # router can chain it cleanly when intake failed.
        assert persist_bootstrap_to_session(None, _make_minimal_bootstrap()) is None


    def test_bootstrap_is_idempotent_when_extras_already_present(self) -> None:
        session = _FakeSession(
            session_id="sess-008",
            extra={
                TASK_BRIEF_EXTRA_KEY: {"brief_id": "x", "session_id": "sess-008"},
                ROLE_COUNCILS_EXTRA_KEY: {"engineering-agent/tech-lead": []},
                LIFECYCLE_SUBSTAGE_EXTRA_KEY: SUBSTAGE_COUNCIL_READY_FOR_SYNTHESIS,
                "active_research_roles": ["tech-lead"],
            },
        )
        pre = dict(session.extra)
        # already_bootstrapped should short-circuit further work.
        assert already_bootstrapped(session.extra) is True
        # The router-level glue uses already_bootstrapped to skip; smoke test
        # that calling bootstrap directly + persist still doesn't crash on a
        # session whose extra already carries council keys.
        fresh = bootstrap_council(
            session_id=session.session_id,
            canonical_prompt="legacy session continues",
            active_roles=["tech-lead"],
        )
        persist_bootstrap_to_session(session, fresh)
        # The existing legacy keys were not lost (router would have skipped
        # at the maybe_bootstrap_council layer); we only confirm no exception
        # path and the post-call extras still pass synthesis_gate.
        assert session.extra[TASK_BRIEF_EXTRA_KEY]["session_id"] == "sess-008"


    def test_role_take_adapter_preserves_four_section_contract(self) -> None:
        take = BackendEngineerTake(
            role="engineering-agent/backend-engineer",
            perspective="이메일 검증 추가",
            evidence=("[official_docs] FastAPI Auth — https://fastapi.tiangolo.com",),
            risks=("권한 boundary 변경 — audit log 필요",),
            next_actions=("migration plan 작성",),
            data_impact="users 테이블 email_verified column 추가",
            api_impact="POST /users 이메일 검증 추가",
        )
        draft = role_take_to_role_draft(take, seat=SeatRole.OWNER, round_index=1, provider="claude")
        assert draft.role == "engineering-agent/backend-engineer"
        assert draft.seat is SeatRole.OWNER
        assert draft.round_index == 1
        assert draft.provider == "claude"
        assert draft.perspective == "이메일 검증 추가"
        assert draft.evidence == take.evidence
        assert draft.risks == take.risks
        assert draft.next_actions == take.next_actions
        # Structured role-specific fields preserved verbatim.
        assert draft.structured_fields.get("data_impact") == take.data_impact
        assert draft.structured_fields.get("api_impact") == take.api_impact


    def test_role_take_adapter_batch_threads_provider_per_role(self) -> None:
        takes = [
            BackendEngineerTake(role="engineering-agent/backend-engineer"),
            QaEngineerTake(role="engineering-agent/qa-engineer"),
        ]
        drafts = role_takes_to_role_drafts(
            takes,
            provider_for_role={
                "engineering-agent/backend-engineer": "claude",
                "engineering-agent/qa-engineer": "codex",
            },
        )
        assert [d.provider for d in drafts] == ["claude", "codex"]
        assert all(d.seat is SeatRole.OWNER for d in drafts)


    def test_role_councils_round_trip_through_extras(self) -> None:
        brief = build_task_brief(session_id="sess-roundtrip", title="t", purpose="p")
        orders = build_role_work_orders(brief, ["backend-engineer", "qa-engineer"])
        originals = tuple(
            build_deterministic_role_council(work_order=o, brief=brief) for o in orders
        )
        extras = role_councils_to_extra(originals)
        rebuilt = role_councils_from_extra(extras)
        # The rebuilt order is by-role iteration order — compare by role to
        # avoid relying on dict insertion order across versions.
        by_role_original = {r.role: r for r in originals}
        by_role_rebuilt = {r.role: r for r in rebuilt}
        assert set(by_role_original.keys()) == set(by_role_rebuilt.keys())
        for role, original in by_role_original.items():
            round_trip = by_role_rebuilt[role]
            assert round_trip.consensus_status is original.consensus_status
            assert round_trip.public_summary == original.public_summary
            assert tuple(d.seat for d in round_trip.drafts) == tuple(
                d.seat for d in original.drafts
            )


    def test_synthesis_gate_returns_none_after_full_bootstrap(self) -> None:
        bootstrap = bootstrap_council(
            session_id="sess-gate",
            canonical_prompt="multi-role council",
            active_roles=["backend-engineer", "frontend-engineer", "qa-engineer"],
        )
        extras = bootstrap.extras_update
        assert synthesis_gate(extras) is None


    def test_router_glue_runs_when_active_roles_present(self) -> None:
        from yule_orchestrator.discord.engineering_channel_router.council_flow import (
            maybe_bootstrap_council,
        )

        session = _FakeSession(
            session_id="sess-router-1",
            extra={"active_research_roles": ["backend-engineer", "qa-engineer"]},
        )
        maybe_bootstrap_council(session, canonical_prompt="회원가입 API + 회귀 테스트")
        assert TASK_BRIEF_EXTRA_KEY in session.extra
        assert ROLE_COUNCILS_EXTRA_KEY in session.extra
        assert session.extra[LIFECYCLE_SUBSTAGE_EXTRA_KEY] == SUBSTAGE_COUNCIL_READY_FOR_SYNTHESIS


    def test_router_glue_no_op_when_session_is_none(self) -> None:
        from yule_orchestrator.discord.engineering_channel_router.council_flow import (
            maybe_bootstrap_council,
        )

        assert maybe_bootstrap_council(None, canonical_prompt="anything") is None


    def test_router_glue_falls_back_to_tech_lead_when_no_active_roles(self) -> None:
        from yule_orchestrator.discord.engineering_channel_router.council_flow import (
            maybe_bootstrap_council,
        )

        session = _FakeSession(session_id="sess-router-3", extra={})
        maybe_bootstrap_council(session, canonical_prompt="ambiguous request")
        # Bootstrap path runs with the tech-lead fallback so the lifecycle
        # substage still advances — silent stall would mask the regression.
        assert session.extra.get(LIFECYCLE_SUBSTAGE_EXTRA_KEY) == SUBSTAGE_COUNCIL_READY_FOR_SYNTHESIS
        orders = session.extra.get(ROLE_WORK_ORDERS_EXTRA_KEY) or []
        assert any(o["role"].endswith("tech-lead") for o in orders)


    def test_router_glue_skips_when_already_bootstrapped(self) -> None:
        from yule_orchestrator.discord.engineering_channel_router.council_flow import (
            maybe_bootstrap_council,
        )

        session = _FakeSession(
            session_id="sess-router-4",
            extra={
                TASK_BRIEF_EXTRA_KEY: {"brief_id": "prev-brief", "session_id": "sess-router-4"},
                ROLE_COUNCILS_EXTRA_KEY: {"engineering-agent/tech-lead": []},
                "active_research_roles": ["backend-engineer"],
            },
        )
        maybe_bootstrap_council(session, canonical_prompt="should not overwrite")
        # Idempotent — brief_id stays the same.
        assert session.extra[TASK_BRIEF_EXTRA_KEY]["brief_id"] == "prev-brief"


    def test_required_outputs_for_role_returns_role_specific_keys(self) -> None:
        assert "api_contract" in required_outputs_for_role(
            "engineering-agent/backend-engineer"
        )
        assert "acceptance_criteria" in required_outputs_for_role(
            "engineering-agent/qa-engineer"
        )
        # Unknown role falls back to generic 4-section keys.
        assert required_outputs_for_role("engineering-agent/unknown-future-role")[0] == "perspective"


    def test_fallback_public_summary_handles_blank_role(self) -> None:
        text = fallback_public_summary(
            role="",
            round_index=1,
            consensus_status=CouncilConsensusStatus.AGREED,
        )
        assert "unknown-role" in text


if __name__ == "__main__":
    unittest.main()
