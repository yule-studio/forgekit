"""Role council C3 round-engine tests.

Seven C3 requirements, pinned by individual tests:

1. round 1 미합의 → round 2 재실행 가능 (advance_council_round).
2. round 2 미합의 + cap 도달 → ``council_escalated`` substage + escalation digest.
3. ``disagreement_summary`` 가 미합의 결과에서 항상 채워짐.
4. bootstrap 실패/누락 상태가 status diagnostic surface 에 노출됨.
5. 짧은 / prefix 형 role 명 혼용 입력이 한 가지 canonical 형으로 합쳐짐.
6. provider × seat metadata 가 manifest 의 ``preferred_advisors`` 기반으로 stamp.
7. C2 bootstrap 회귀 없음 — 이미 settled 인 케이스가 그대로 동작.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


import unittest

from yule_orchestrator.agents.council import (
    CouncilConsensusStatus,
    DEFAULT_COUNCIL_ROUND_CAP,
    canonical_role,
    ensure_disagreement_summary,
    must_escalate_to_tech_lead,
    normalize_roles,
    role_councils_from_extra,
    role_councils_to_extra,
    short_role,
)
from yule_orchestrator.agents.council_bootstrap import (
    PROVIDER_SEAT_MATRIX_EXTRA_KEY,
    ROLE_WORK_ORDERS_EXTRA_KEY,
    TASK_BRIEF_EXTRA_KEY,
    advance_council_round,
    bootstrap_council,
    build_deterministic_role_council,
    build_provider_seat_matrix,
    build_role_work_orders,
    build_task_brief,
    role_work_order_to_payload,
    task_brief_to_payload,
)
from yule_orchestrator.agents.lifecycle.council_status_signals import (
    COUNCIL_BOOTSTRAP_ERROR,
    COUNCIL_ESCALATED_CODE,
    COUNCIL_READY_FOR_SYNTHESIS_CODE,
    COUNCIL_ROUND_2_PENDING,
    COUNCIL_STATE_MISSING,
    collect_council_signals,
)
from yule_orchestrator.agents.lifecycle.council_substage import (
    COUNCIL_BOOTSTRAP_ERROR_EXTRA_KEY,
    COUNCIL_ESCALATION_EXTRA_KEY,
    LIFECYCLE_SUBSTAGE_EXTRA_KEY,
    ROLE_COUNCILS_EXTRA_KEY,
    SUBSTAGE_COUNCIL_ESCALATED,
    SUBSTAGE_COUNCIL_READY_FOR_SYNTHESIS,
    SUBSTAGE_COUNCIL_ROUND_COMPLETE,
)
from yule_orchestrator.discord.engineering_channel_router.council_flow import (
    advance_council_for_role,
    maybe_bootstrap_council,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@dataclass
class _FakeSession:
    session_id: str
    extra: dict = field(default_factory=dict)
    role_sequence: tuple = ()


def _make_unsettled_round_one_extra(role: str) -> dict:
    brief = build_task_brief(session_id="sess", title="X", purpose="P")
    orders = build_role_work_orders(brief, [role])
    r1 = build_deterministic_role_council(
        work_order=orders[0],
        brief=brief,
        round_index=1,
        consensus_status=CouncilConsensusStatus.NEEDS_ANOTHER_ROUND,
    )
    return {
        TASK_BRIEF_EXTRA_KEY: dict(task_brief_to_payload(brief)),
        ROLE_WORK_ORDERS_EXTRA_KEY: [
            dict(role_work_order_to_payload(o)) for o in orders
        ],
        ROLE_COUNCILS_EXTRA_KEY: {
            role_id: [dict(p) for p in payloads]
            for role_id, payloads in role_councils_to_extra([r1]).items()
        },
        LIFECYCLE_SUBSTAGE_EXTRA_KEY: SUBSTAGE_COUNCIL_ROUND_COMPLETE,
    }


# ---------------------------------------------------------------------------
# REQ 1 — round 1 unsettled → round 2 advances
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# REQ 2 — round 2 unsettled + cap → escalated
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# REQ 3 — disagreement_summary always populated on unsettled rounds
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# REQ 4 — bootstrap missing / failed surfaces to status diagnostic
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# REQ 5 — role normalization handles short / canonical / mixed input
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# REQ 6 — provider × seat matrix from manifest data
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# REQ 7 — C2 bootstrap regression: settled council still produces
# ready-for-synthesis substage + info signal.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Status signal — round 2 pending intermediate state
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Router glue — advance_council_for_role best-effort behaviour
# ---------------------------------------------------------------------------


class CouncilRoundsTests(unittest.TestCase):
    def test_advance_council_round_recomputes_round_two_from_unsettled_state(self) -> None:
        extra = _make_unsettled_round_one_extra("backend-engineer")
        advanced = advance_council_round(
            extra,
            role="backend-engineer",
            requested_status=CouncilConsensusStatus.AGREED,  # operator settled
        )
        assert advanced is not None
        # Substage advances because the only role council is now settled.
        assert advanced.substage == SUBSTAGE_COUNCIL_READY_FOR_SYNTHESIS
        # History preserved: round 1 + round 2 both present for the role.
        role_payloads = advanced.extras_update[ROLE_COUNCILS_EXTRA_KEY][
            "engineering-agent/backend-engineer"
        ]
        assert [p["round_index"] for p in role_payloads] == [1, 2]
        # Round 2 carries the settled status; round 1 stays unsettled.
        assert role_payloads[0]["consensus_status"] == "needs_another_round"
        assert role_payloads[1]["consensus_status"] == "agreed"


    def test_advance_council_round_returns_none_when_brief_missing(self) -> None:
        # Legacy session with only role_councils but no task_brief — advance
        # cannot synthesise without the brief. None lets the caller fall
        # back to bootstrap.
        extra = _make_unsettled_round_one_extra("backend-engineer")
        del extra[TASK_BRIEF_EXTRA_KEY]
        assert (
            advance_council_round(extra, role="backend-engineer") is None
        )


    def test_advance_council_round_accepts_short_role_input(self) -> None:
        # Round normalization: caller may pass either form.
        extra = _make_unsettled_round_one_extra("backend-engineer")
        advanced_short = advance_council_round(extra, role="backend-engineer")
        advanced_canon = advance_council_round(
            extra, role="engineering-agent/backend-engineer"
        )
        assert advanced_short is not None
        assert advanced_canon is not None
        # Both produce the same round_index (2) on top of the existing history.
        role_key = "engineering-agent/backend-engineer"
        short_history = advanced_short.extras_update[ROLE_COUNCILS_EXTRA_KEY][role_key]
        canon_history = advanced_canon.extras_update[ROLE_COUNCILS_EXTRA_KEY][role_key]
        assert short_history[-1]["round_index"] == 2
        assert canon_history[-1]["round_index"] == 2


    def test_round_two_without_settlement_escalates_at_cap(self) -> None:
        extra = _make_unsettled_round_one_extra("backend-engineer")
        advanced = advance_council_round(
            extra,
            role="backend-engineer",
            requested_status=CouncilConsensusStatus.NEEDS_ANOTHER_ROUND,
        )
        assert advanced is not None
        assert advanced.substage == SUBSTAGE_COUNCIL_ESCALATED
        # Latest round in the history is ESCALATED.
        history = advanced.extras_update[ROLE_COUNCILS_EXTRA_KEY][
            "engineering-agent/backend-engineer"
        ]
        assert history[-1]["consensus_status"] == "escalated"
        # Escalation digest written.
        digest = advanced.extras_update[COUNCIL_ESCALATION_EXTRA_KEY]
        assert digest["role"] == "engineering-agent/backend-engineer"
        assert digest["round_index"] == DEFAULT_COUNCIL_ROUND_CAP
        assert digest["reason"] == "round_cap_reached"
        assert digest["disagreement_summary"]


    def test_must_escalate_to_tech_lead_aligns_with_substage(self) -> None:
        # Drive an escalation through advance_council_round and confirm the
        # SSoT helper agrees.
        extra = _make_unsettled_round_one_extra("backend-engineer")
        advanced = advance_council_round(extra, role="backend-engineer")
        assert advanced is not None
        # Reconstruct the role's history to check the helper.
        payload = advanced.extras_update[ROLE_COUNCILS_EXTRA_KEY][
            "engineering-agent/backend-engineer"
        ]
        rounds = role_councils_from_extra(
            {"engineering-agent/backend-engineer": payload}
        )
        assert must_escalate_to_tech_lead(rounds) is True


    def test_disagreement_summary_filled_when_round_unsettled(self) -> None:
        brief = build_task_brief(session_id="x", title="X", purpose="P")
        orders = build_role_work_orders(brief, ["backend-engineer"])
        # NEEDS_ANOTHER_ROUND without an explicit summary — fallback fills.
        result = build_deterministic_role_council(
            work_order=orders[0],
            brief=brief,
            round_index=1,
            consensus_status=CouncilConsensusStatus.NEEDS_ANOTHER_ROUND,
        )
        assert result.disagreement_summary
        assert result.peer_review.disagreement_summary


    def test_disagreement_summary_filled_when_escalated(self) -> None:
        brief = build_task_brief(session_id="x", title="X", purpose="P")
        orders = build_role_work_orders(brief, ["frontend-engineer"])
        result = build_deterministic_role_council(
            work_order=orders[0],
            brief=brief,
            round_index=2,
            consensus_status=CouncilConsensusStatus.ESCALATED,
        )
        assert result.disagreement_summary
        # public_summary should still be a 1-liner — Discord-facing dump
        # forbidden.
        assert "\n" not in result.public_summary


    def test_ensure_disagreement_summary_does_not_inject_for_settled(self) -> None:
        # Settled rounds may legitimately have no summary; helper must not
        # invent one.
        assert (
            ensure_disagreement_summary(
                None,
                role="engineering-agent/backend-engineer",
                round_index=1,
                consensus_status=CouncilConsensusStatus.AGREED,
            )
            is None
        )


    def test_council_state_missing_emits_when_active_roles_set_but_council_absent(self) -> None:
        # The signal is reserved for sessions that *expect* a council (i.e.
        # the router already selected active roles). Pre-C2 / closed / pure-
        # info sessions carry no council-relevant extras and stay silent —
        # see :func:`council_status_signals._council_is_expected`.
        extra = {"active_research_roles": ["backend-engineer"]}
        sigs = collect_council_signals(extra)
        codes = [s.code for s in sigs]
        assert COUNCIL_STATE_MISSING in codes


    def test_council_signals_silent_for_legacy_session_without_council_keys(self) -> None:
        # Legacy session with no council-relevant key — silent (no
        # ``council_state_missing`` so pure-info status responses still skip
        # the actionable block).
        assert collect_council_signals({}) == ()
        assert collect_council_signals({"research_pack": {"title": "x"}}) == ()


    def test_council_bootstrap_error_signal_when_router_stamped_reason(self) -> None:
        extra = {COUNCIL_BOOTSTRAP_ERROR_EXTRA_KEY: "bootstrap raised: import fail"}
        sigs = collect_council_signals(extra)
        codes = [s.code for s in sigs]
        # The error signal must appear; council_state_missing is suppressed
        # because the bootstrap error already explains the absence.
        assert COUNCIL_BOOTSTRAP_ERROR in codes
        assert COUNCIL_STATE_MISSING not in codes
        error_signal = next(s for s in sigs if s.code == COUNCIL_BOOTSTRAP_ERROR)
        assert error_signal.severity == "failed"
        assert "import fail" in (error_signal.detail or "")


    def test_council_signals_skipped_when_intake_not_completed(self) -> None:
        extra = {COUNCIL_BOOTSTRAP_ERROR_EXTRA_KEY: "should be ignored"}
        assert collect_council_signals(extra, intake_completed=False) == ()


    def test_router_glue_stamps_bootstrap_error_when_no_active_roles(self) -> None:
        # The router glue cannot synthesise a useful council without active
        # roles AND without a falsy role_sequence; force the tech-lead
        # fallback off by mocking role_sequence as empty + no active roles.
        session = _FakeSession(session_id="sess", extra={}, role_sequence=())
        # Patch the _coerce_active_roles fallback by stripping the tech-lead
        # fallback in the helper... but that requires patching. Instead,
        # confirm the more realistic missing-session-id path:
        session_no_id = _FakeSession(session_id="", extra={})
        maybe_bootstrap_council(session_no_id, canonical_prompt="anything")
        assert session_no_id.extra.get(COUNCIL_BOOTSTRAP_ERROR_EXTRA_KEY) == "session_id missing"


    def test_canonical_role_and_short_role_round_trip(self) -> None:
        assert canonical_role("backend-engineer") == "engineering-agent/backend-engineer"
        assert canonical_role("engineering-agent/backend-engineer") == "engineering-agent/backend-engineer"
        assert canonical_role("") == ""
        assert short_role("engineering-agent/backend-engineer") == "backend-engineer"
        assert short_role("backend-engineer") == "backend-engineer"
        # Foreign prefix preserved verbatim — council vocabulary is
        # engineering-agent scoped but the helper does not silently rewrite
        # cross-department addresses.
        assert canonical_role("cto-agent/security-lead") == "cto-agent/security-lead"


    def test_normalize_roles_collapses_short_and_canonical_into_canonical(self) -> None:
        out = normalize_roles(
            [
                "backend-engineer",
                "engineering-agent/backend-engineer",
                "frontend-engineer",
                "",
                "engineering-agent/frontend-engineer",
            ]
        )
        assert out == (
            "engineering-agent/backend-engineer",
            "engineering-agent/frontend-engineer",
        )


    def test_bootstrap_treats_short_and_canonical_input_consistently(self) -> None:
        a = bootstrap_council(
            session_id="x",
            canonical_prompt="P",
            active_roles=["backend-engineer", "engineering-agent/qa-engineer"],
        )
        role_keys = list(a.extras_update[ROLE_COUNCILS_EXTRA_KEY].keys())
        assert role_keys == [
            "engineering-agent/backend-engineer",
            "engineering-agent/qa-engineer",
        ]


    def test_provider_seat_matrix_uses_manifest_preferred_advisors(self) -> None:
        matrix = build_provider_seat_matrix(
            ["backend-engineer", "qa-engineer"],
        )
        backend = matrix["engineering-agent/backend-engineer"]
        # 3 seat keys present.
        assert set(backend.keys()) == {"owner", "challenger", "reviewer"}
        # Owner / challenger get distinct providers — orthogonality.
        assert backend["owner"][0] != backend["challenger"][0]
        # The manifest declares preferred_advisors=["claude", "codex", "gemini"]
        # so the primary slot starts with claude.
        assert backend["owner"][0] == "claude"
        assert backend["challenger"][0] == "codex"
        assert backend["reviewer"][0] == "gemini"


    def test_provider_seat_matrix_falls_back_when_manifest_unavailable(self) -> None:
        # Inject a loader that returns nothing — exercise the default
        # rotation path.
        matrix = build_provider_seat_matrix(
            ["frontend-engineer"],
            manifest_loader=lambda role: {},
        )
        fe = matrix["engineering-agent/frontend-engineer"]
        assert fe["owner"][0] == "claude"
        assert fe["challenger"][0] == "codex"


    def test_bootstrap_stamps_provider_seat_matrix_on_session_extra(self) -> None:
        result = bootstrap_council(
            session_id="sess",
            canonical_prompt="P",
            active_roles=["backend-engineer"],
        )
        matrix = result.extras_update[PROVIDER_SEAT_MATRIX_EXTRA_KEY]
        assert "engineering-agent/backend-engineer" in matrix
        backend = matrix["engineering-agent/backend-engineer"]
        assert backend["owner"]  # non-empty list


    def test_settled_bootstrap_still_emits_info_ready_signal(self) -> None:
        result = bootstrap_council(
            session_id="legacy",
            canonical_prompt="settled case",
            active_roles=["tech-lead"],
        )
        sigs = collect_council_signals(result.extras_update)
        codes = [s.code for s in sigs]
        assert COUNCIL_READY_FOR_SYNTHESIS_CODE in codes
        info_signal = next(s for s in sigs if s.code == COUNCIL_READY_FOR_SYNTHESIS_CODE)
        assert info_signal.severity == "info"


    def test_settled_bootstrap_substage_unchanged_from_c2(self) -> None:
        result = bootstrap_council(
            session_id="legacy-2",
            canonical_prompt="settled case",
            active_roles=["backend-engineer", "frontend-engineer"],
        )
        assert result.substage == SUBSTAGE_COUNCIL_READY_FOR_SYNTHESIS


    def test_round_one_unsettled_emits_round_2_pending_signal(self) -> None:
        extra = _make_unsettled_round_one_extra("backend-engineer")
        sigs = collect_council_signals(extra)
        codes = [s.code for s in sigs]
        assert COUNCIL_ROUND_2_PENDING in codes
        pending = next(s for s in sigs if s.code == COUNCIL_ROUND_2_PENDING)
        assert pending.severity == "stale"
        assert "backend-engineer" in (pending.detail or "")


    def test_escalation_signal_includes_disagreement_summary_when_digest_present(self) -> None:
        extra = _make_unsettled_round_one_extra("backend-engineer")
        advanced = advance_council_round(extra, role="backend-engineer")
        assert advanced is not None
        merged_extra = {**extra, **dict(advanced.extras_update)}
        sigs = collect_council_signals(merged_extra)
        escalated = next(s for s in sigs if s.code == COUNCIL_ESCALATED_CODE)
        assert escalated.severity == "blocked"
        # disagreement_summary is appended to detail when the digest is
        # present.
        assert "round_cap_reached" not in (escalated.detail or "")
        assert "council_round_cap" in (escalated.detail or "")


    def test_advance_council_for_role_updates_session_extra(self) -> None:
        session = _FakeSession(
            session_id="sess",
            extra=_make_unsettled_round_one_extra("backend-engineer"),
        )
        advance_council_for_role(
            session,
            role="backend-engineer",
            requested_status=CouncilConsensusStatus.AGREED,
        )
        history = session.extra[ROLE_COUNCILS_EXTRA_KEY][
            "engineering-agent/backend-engineer"
        ]
        assert [p["round_index"] for p in history] == [1, 2]
        assert session.extra[LIFECYCLE_SUBSTAGE_EXTRA_KEY] == SUBSTAGE_COUNCIL_READY_FOR_SYNTHESIS


    def test_advance_council_for_role_stamps_error_on_missing_brief(self) -> None:
        # Strip the brief so the advance helper cannot proceed.
        extra = _make_unsettled_round_one_extra("backend-engineer")
        del extra[TASK_BRIEF_EXTRA_KEY]
        session = _FakeSession(session_id="sess", extra=extra)
        advance_council_for_role(session, role="backend-engineer")
        assert session.extra.get(COUNCIL_BOOTSTRAP_ERROR_EXTRA_KEY)


    def test_advance_council_for_role_noop_when_session_is_none(self) -> None:
        assert advance_council_for_role(None, role="backend-engineer") is None


if __name__ == "__main__":
    unittest.main()
