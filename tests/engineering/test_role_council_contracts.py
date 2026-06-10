"""Role council contract scaffolding tests.

본 회귀는 [docs/engineering-role-council-runtime.md](../../docs/
engineering-role-council-runtime.md) §3 / §4 / §5 의 hard rail 4 개를
잡는다 — C1 phase 의 *contract 만* 검증한다. router / runner / approval
surface wiring 은 C2 이후의 별 PR scope.

검증 대상:

1. **lifecycle substage 전이** — vocabulary 가 13 stage 다 등록되고,
   council / synthesis / execution_review 묶음이 끊기지 않음.
2. **same-role peer review 미완료 → synthesis 진입 차단** —
   ``ready_for_synthesis`` 가 escalated / needs_another_round 에서 False.
3. **tech-lead signoff 없으면 ApprovalPacket 생성 불가** —
   ``can_create_approval_packet`` 가 차단 사유 반환.
4. **execution_review decision → reopen_for_rework / reroute_to_review_loop
   둘 다 vocabulary 의 일원**.

또한 vocabulary level 의 안정성을 잡는다:

- ``RequestedAction`` 에 PEER_REVIEW / COUNCIL_SYNTHESIS /
  TECH_LEAD_SIGNOFF 가 모두 request side 로 등록됨.
- ``DEFAULT_SEATS`` 가 owner / challenger / reviewer 3 종.
"""

from __future__ import annotations

from datetime import datetime


import unittest

from yule_engineering.agents.council import (
    ALL_SUBSTAGES,
    DELIBERATION_SUBSTAGES,
    EXECUTION_REVIEW_SUBSTAGES,
    SYNTHESIS_SUBSTAGES,
    DEFAULT_COUNCIL_ROUND_CAP,
    DEFAULT_SEATS,
    ApprovalPacketStatus,
    CIBucketStatus,
    CouncilConsensusStatus,
    ExecutionReviewDecision,
    PeerReviewNote,
    RoleCouncilResult,
    RoleDraft,
    SeatRole,
    TechLeadSignoff,
    TechLeadSignoffStatus,
    can_create_approval_packet,
    is_valid_substage,
    must_escalate_to_tech_lead,
    ready_for_synthesis,
    synthesis_block_reason,
)
from yule_engineering.agents.lifecycle.council_substage import (
    LIFECYCLE_SUBSTAGE_EXTRA_KEY,
)
from yule_engineering.agents.messaging.message import (
    REQUEST_ACTIONS,
    RequestedAction,
)
from yule_engineering.agents.review_loop import (
    ExecutionReviewDecision as ReviewLoopERD,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_council_result(
    *,
    role: str,
    round_index: int,
    status: CouncilConsensusStatus,
    disagreement_summary: str | None = None,
) -> RoleCouncilResult:
    owner_draft = RoleDraft(
        role=role,
        seat=SeatRole.OWNER,
        round_index=round_index,
        provider="claude",
        perspective="owner perspective",
    )
    challenger_draft = RoleDraft(
        role=role,
        seat=SeatRole.CHALLENGER,
        round_index=round_index,
        provider="codex",
        perspective="challenger perspective",
    )
    peer = PeerReviewNote(
        role=role,
        round_index=round_index,
        reviewer_provider="gemini",
        owner_draft_id=owner_draft.draft_id,
        challenger_draft_id=challenger_draft.draft_id,
        consensus_status=status,
        agreed_points=("test ok",) if status is CouncilConsensusStatus.AGREED else (),
        disagreement_summary=disagreement_summary,
        public_summary="public-only summary",
    )
    return RoleCouncilResult(
        role=role,
        work_order_id=f"wo-{role}",
        round_index=round_index,
        drafts=(owner_draft, challenger_draft),
        peer_review=peer,
        consensus_status=status,
        public_summary="public-only summary",
        disagreement_summary=disagreement_summary,
    )


# ---------------------------------------------------------------------------
# Lifecycle substage vocabulary
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Same-role peer review gate
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 2-round cap → tech-lead escalation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# tech-lead signoff gate for ApprovalPacket
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Review feedback → reopen / review_loop reroute vocabulary
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Council-internal RequestedAction vocabulary
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CIBucketStatus / ApprovalPacketStatus enums (sanity)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Future-work pins (TODO test stubs — turn into asserts as runtime arrives)
# ---------------------------------------------------------------------------


class RoleCouncilContractsTests(unittest.TestCase):
    def test_council_substage_vocabulary_has_expected_buckets(self) -> None:
        # 13 substages 총합 (6 deliberation + 3 synthesis + 4 execution_review)
        assert len(DELIBERATION_SUBSTAGES) == 6
        assert len(SYNTHESIS_SUBSTAGES) == 3
        assert len(EXECUTION_REVIEW_SUBSTAGES) == 4
        assert len(ALL_SUBSTAGES) == 13
        for substage in ALL_SUBSTAGES:
            assert is_valid_substage(substage)
        assert not is_valid_substage("imaginary_substage")


    def test_lifecycle_substage_extra_key_is_stable(self) -> None:
        # 본 키는 session.extra round-trip SSoT. 변경되면 lifecycle persistence
        # 회귀가 따라와야 하므로 hard pin.
        assert LIFECYCLE_SUBSTAGE_EXTRA_KEY == "lifecycle_substage"


    def test_synthesis_blocked_when_no_council_results(self) -> None:
        assert ready_for_synthesis([]) is False
        assert synthesis_block_reason([]) == "no_role_councils_recorded"


    def test_synthesis_blocked_when_any_role_escalated(self) -> None:
        a = _make_council_result(
            role="engineering-agent/backend-engineer",
            round_index=1,
            status=CouncilConsensusStatus.AGREED,
        )
        b = _make_council_result(
            role="engineering-agent/frontend-engineer",
            round_index=2,
            status=CouncilConsensusStatus.ESCALATED,
            disagreement_summary="state model 합의 안됨",
        )
        assert ready_for_synthesis([a, b]) is False
        reason = synthesis_block_reason([a, b]) or ""
        assert reason.startswith("escalated:")
        assert "frontend-engineer" in reason


    def test_synthesis_allowed_when_all_settled_even_with_conditions(self) -> None:
        a = _make_council_result(
            role="engineering-agent/backend-engineer",
            round_index=1,
            status=CouncilConsensusStatus.AGREED,
        )
        b = _make_council_result(
            role="engineering-agent/frontend-engineer",
            round_index=1,
            status=CouncilConsensusStatus.AGREED_WITH_CONDITIONS,
        )
        assert ready_for_synthesis([a, b]) is True
        assert synthesis_block_reason([a, b]) is None


    def test_synthesis_uses_latest_round_per_role(self) -> None:
        # 같은 role 이 round 1 에서 escalated 였더라도 round 2 에서 agreed 면
        # synthesis 진입 가능. 이전 round 결과는 보존되지만 게이트는 최신 라운드
        # 기준.
        earlier = _make_council_result(
            role="engineering-agent/backend-engineer",
            round_index=1,
            status=CouncilConsensusStatus.ESCALATED,
            disagreement_summary="initial split",
        )
        later = _make_council_result(
            role="engineering-agent/backend-engineer",
            round_index=2,
            status=CouncilConsensusStatus.AGREED,
        )
        assert ready_for_synthesis([earlier, later]) is True
        assert synthesis_block_reason([earlier, later]) is None


    def test_escalation_triggers_after_round_cap_without_agreement(self) -> None:
        history = [
            _make_council_result(
                role="engineering-agent/backend-engineer",
                round_index=1,
                status=CouncilConsensusStatus.NEEDS_ANOTHER_ROUND,
            ),
            _make_council_result(
                role="engineering-agent/backend-engineer",
                round_index=2,
                status=CouncilConsensusStatus.ESCALATED,
                disagreement_summary="still split after round 2",
            ),
        ]
        assert DEFAULT_COUNCIL_ROUND_CAP == 2
        assert must_escalate_to_tech_lead(history) is True


    def test_no_escalation_when_latest_round_settled(self) -> None:
        history = [
            _make_council_result(
                role="engineering-agent/backend-engineer",
                round_index=1,
                status=CouncilConsensusStatus.NEEDS_ANOTHER_ROUND,
            ),
            _make_council_result(
                role="engineering-agent/backend-engineer",
                round_index=2,
                status=CouncilConsensusStatus.AGREED_WITH_CONDITIONS,
            ),
        ]
        assert must_escalate_to_tech_lead(history) is False


    def test_no_escalation_for_empty_history(self) -> None:
        assert must_escalate_to_tech_lead([]) is False


    def test_packet_blocked_without_signoff(self) -> None:
        council = [
            _make_council_result(
                role="engineering-agent/backend-engineer",
                round_index=1,
                status=CouncilConsensusStatus.AGREED,
            )
        ]
        assert (
            can_create_approval_packet(results=council, tech_lead_signoff=None)
            == "tech_lead_signoff_missing"
        )


    def test_packet_blocked_when_signoff_is_blocked(self) -> None:
        council = [
            _make_council_result(
                role="engineering-agent/backend-engineer",
                round_index=1,
                status=CouncilConsensusStatus.AGREED,
            )
        ]
        signoff = TechLeadSignoff(
            status=TechLeadSignoffStatus.BLOCKED,
            rationale="역할 충돌 미해결",
        )
        assert (
            can_create_approval_packet(results=council, tech_lead_signoff=signoff)
            == "tech_lead_signoff_blocked"
        )


    def test_packet_blocked_when_council_still_escalated(self) -> None:
        council = [
            _make_council_result(
                role="engineering-agent/backend-engineer",
                round_index=2,
                status=CouncilConsensusStatus.ESCALATED,
                disagreement_summary="unresolved",
            )
        ]
        signoff = TechLeadSignoff(
            status=TechLeadSignoffStatus.SIGNED_OFF,
            rationale="ignored — should still block on council",
        )
        block = can_create_approval_packet(results=council, tech_lead_signoff=signoff)
        assert block is not None and block.startswith("council_not_settled")


    def test_packet_creatable_when_signoff_and_council_settled(self) -> None:
        council = [
            _make_council_result(
                role="engineering-agent/backend-engineer",
                round_index=1,
                status=CouncilConsensusStatus.AGREED,
            ),
            _make_council_result(
                role="engineering-agent/frontend-engineer",
                round_index=1,
                status=CouncilConsensusStatus.AGREED_WITH_CONDITIONS,
            ),
        ]
        signoff = TechLeadSignoff(
            status=TechLeadSignoffStatus.SIGNED_OFF,
            rationale="ok",
        )
        assert can_create_approval_packet(results=council, tech_lead_signoff=signoff) is None


    def test_packet_creatable_when_signoff_conditional(self) -> None:
        # `approve_with_conditions` 는 packet 생성 가능 — 조건은 packet 안에서
        # 실행 시점 hard gate 로 surface 된다.
        council = [
            _make_council_result(
                role="engineering-agent/backend-engineer",
                round_index=1,
                status=CouncilConsensusStatus.AGREED,
            )
        ]
        signoff = TechLeadSignoff(
            status=TechLeadSignoffStatus.CONDITIONAL,
            rationale="qa 회귀 N개 추가 후 land",
            conditions=("회귀 ≥ 3 케이스",),
        )
        assert can_create_approval_packet(results=council, tech_lead_signoff=signoff) is None


    def test_review_loop_re_exports_execution_review_decision(self) -> None:
        # review_loop.py 가 council.ExecutionReviewDecision 을 그대로 surface.
        # 두 alias 가 동일 객체여야 함 — 분기 코드가 두 import 양쪽에서 모두
        # 안전.
        assert ReviewLoopERD is ExecutionReviewDecision


    def test_execution_review_decision_vocabulary_is_stable(self) -> None:
        # 4 가지 가지 (close / followup / reopen / reroute) 가 모두 있어야 함.
        # 새 분기가 늘어나면 lifecycle / review_loop / docs 가 따라와야 하므로
        # hard pin.
        assert {d.value for d in ExecutionReviewDecision} == {
            "accept_and_close",
            "accept_with_followups",
            "reopen_for_rework",
            "reroute_to_review_loop",
        }


    def test_council_internal_actions_are_request_side(self) -> None:
        for action in (
            RequestedAction.PEER_REVIEW,
            RequestedAction.COUNCIL_SYNTHESIS,
            RequestedAction.TECH_LEAD_SIGNOFF,
        ):
            assert action in REQUEST_ACTIONS


    def test_default_seats_are_three(self) -> None:
        assert tuple(DEFAULT_SEATS) == (
            SeatRole.OWNER,
            SeatRole.CHALLENGER,
            SeatRole.REVIEWER,
        )


    def test_ci_bucket_status_values(self) -> None:
        assert CIBucketStatus.GREEN.value == "green"
        assert CIBucketStatus.NOT_APPLICABLE.value == "not_applicable"


    def test_approval_packet_status_includes_conditional(self) -> None:
        # `approve_with_conditions` 자리에 대응하는 packet status 가 있어야 함.
        assert ApprovalPacketStatus.CONDITIONAL.value == "conditional"


    @unittest.skip("C2 phase: substage transition runner not implemented yet")
    def test_TODO_lifecycle_substage_transition_runner(self) -> None:
        """C2 PR — `role_brief_distributed` → `role_drafts_in_progress` →
        `peer_review_pending` → `council_round_complete` 가 한 round 안에서
        monotonic 으로 진행. router 측 runner 가 land 되면 본 skip 해제."""


    @unittest.skip("C4 phase: gateway operator surface separation runner not landed")
    def test_TODO_gateway_does_not_post_card_until_tech_lead_signoff(self) -> None:
        """C4 PR — `tech_lead_signoff.status != SIGNED_OFF / CONDITIONAL` 인
        동안에는 gateway 가 `#승인-대기` 카드를 게시하지 않는다."""


    @unittest.skip("C5 phase: execution_review reopen wiring not landed")
    def test_TODO_review_feedback_routes_to_reopen_or_review_loop(self) -> None:
        """C5 PR — `decision = reopen_for_rework` 면 `role_councils[role]` 에
        새 `round_index` 의 결과가 누적되고, `decision =
        reroute_to_review_loop` 이면 `review_loop.record_review_feedback` 가
        1 번 호출된다."""


if __name__ == "__main__":
    unittest.main()
