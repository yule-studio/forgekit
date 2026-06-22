"""PM / Tech-Lead lane — schema + validator + handoff regression.

Proves the design-decision lane's contract:
- a REAL brief + meeting + 2-option stack comparison clears gateway → tech-lead →
  engineer and an engineer MAY start (safe → no operator);
- a RISKY design is signed off but ``operator_required`` (L3, operator must approve);
- a RESTRICTED design is BLOCKED (L4, no handoff);
- **no fake**: a rubber-stamp meeting (all support, no concern), a one-sided stack
  comparison, a non-tech-lead signer, and a non-engineer executor are ALL rejected and
  can NEVER produce a startable handoff (``can_engineer_start`` stays False).

Hermetic + pure: no I/O, role identities resolved through the registry SSoT.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _rel in (
    "packages/forgekit-runtime/src",
    "packages/forgekit-config/src",
    "packages/forgekit-provider/src",
    "packages/forgekit-contracts/src",
    "packages/forgekit-goal/src",
    "packages/nexus/src",
):
    _p = str(_ROOT / _rel)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from forgekit_runtime import decision_lane as L


def _brief() -> L.PMBrief:
    return L.PMBrief(
        topic="운영 콘솔 실패 알림", problem="운영자가 실패를 늦게 인지",
        user_value="평균 인지 시간(MTTA) 단축", target_users=("operator",),
        acceptance_criteria=("실패 1건당 1개 알림", "5초 내 발송"),
        success_metrics=("MTTA 50% 감소",))


def _meeting(escalated: bool = False) -> L.MeetingRecord:
    return L.MeetingRecord(
        meeting_id="m-notify-1", topic="알림 전달 스택", agenda=("스택 비교", "운영 리스크"),
        participants=(
            L.ParticipantPosition("tech-lead", "support", "웹훅+큐가 단순"),
            L.ParticipantPosition("be", "conditional", "rate-limit 선행 필요",
                                  concerns=("폭주 시 스팸",)),
        ),
        decisions=() if escalated else ("webhook+queue 채택",),
        escalated=escalated)


def _stack() -> L.StackComparison:
    return L.StackComparison(
        decision_topic="알림 전달 메커니즘", recommended="webhook+queue",
        options=(
            L.StackOption("webhook+queue", summary="자체 큐", pros=("단순", "관측 쉬움"),
                          cons=("재시도 직접 구현",), fit=80),
            L.StackOption("saas-bus", summary="관리형 버스", pros=("관리형 재시도",),
                          cons=("비용", "vendor lock-in"), fit=55),
        ),
        rationale="운영 단순성과 관측 가능성 우선", tradeoffs=("재시도 로직 직접 구현 부담",),
        assumptions=("일 알림량 < 10k",))


def _run(risk_class: str = "safe", **kw) -> L.LaneResult:
    return L.run_lane(
        _brief(), kw.pop("meeting", _meeting()), _stack(),
        design_system="forgekit tokens v2", coding_convention="ruff+black, 한글 gitmoji commit",
        executor_role=kw.pop("executor_role", "be"), scope=("notify/webhook.py",),
        test_strategy="unit + integration", risk_class=risk_class, **kw)


class SchemaTests(unittest.TestCase):
    def test_artifacts_serialise(self) -> None:
        for art in (_brief(), _stack(), _meeting()):
            d = art.to_dict()
            self.assertIsInstance(d, dict)
        self.assertEqual(_stack().recommended_option().name, "webhook+queue")
        self.assertEqual(set(_meeting().roles()), {"tech-lead", "be"})


class ValidatorTests(unittest.TestCase):
    def test_real_artifacts_pass(self) -> None:
        self.assertEqual(L.validate_pm_brief(_brief()), ())
        self.assertEqual(L.validate_stack_comparison(_stack()), ())
        self.assertEqual(L.validate_meeting(_meeting()), ())

    def test_pm_brief_requires_acceptance_and_metrics(self) -> None:
        bad = L.PMBrief(topic="t", problem="p", user_value="v")
        viol = L.validate_pm_brief(bad)
        self.assertTrue(any("acceptance_criteria" in x for x in viol))
        self.assertTrue(any("success_metrics" in x for x in viol))

    def test_one_sided_stack_is_rejected(self) -> None:
        # an option with pros but no cons → fake comparison
        cmp = L.StackComparison(
            decision_topic="x", recommended="a",
            options=(L.StackOption("a", pros=("좋음",)), L.StackOption("b", pros=("좋음",), cons=("나쁨",))),
            rationale="r", tradeoffs=("t",))
        self.assertTrue(any("단점" in x for x in L.validate_stack_comparison(cmp)))

    def test_single_option_is_not_a_comparison(self) -> None:
        cmp = L.StackComparison(decision_topic="x", recommended="a",
                                options=(L.StackOption("a", pros=("p",), cons=("c",)),),
                                rationale="r", tradeoffs=("t",))
        self.assertTrue(any("2개 미만" in x for x in L.validate_stack_comparison(cmp)))


class FakeMeetingTests(unittest.TestCase):
    def test_rubber_stamp_consensus_rejected(self) -> None:
        # everyone "support", no concern, no dissent → fake consensus
        m = L.MeetingRecord(
            meeting_id="m", topic="t", agenda=("a",),
            participants=(L.ParticipantPosition("tech-lead", "support", "ok"),
                          L.ParticipantPosition("be", "support", "ok")),
            decisions=("go",))
        self.assertTrue(any("rubber-stamp" in x for x in L.validate_meeting(m)))

    def test_single_role_meeting_rejected(self) -> None:
        m = L.MeetingRecord(
            meeting_id="m", topic="t", agenda=("a",),
            participants=(L.ParticipantPosition("tech-lead", "support", "ok"),
                          L.ParticipantPosition("tech-lead", "conditional", "음", concerns=("x",))),
            decisions=("go",))
        self.assertTrue(any("2개 미만" in x for x in L.validate_meeting(m)))

    def test_empty_position_rejected(self) -> None:
        m = L.MeetingRecord(
            meeting_id="m", topic="t", agenda=("a",),
            participants=(L.ParticipantPosition("tech-lead", "support", ""),
                          L.ParticipantPosition("be", "oppose", "반대")),
            decisions=("go",))
        self.assertTrue(any("fake 참석" in x for x in L.validate_meeting(m)))


class TechLeadDecisionTests(unittest.TestCase):
    def test_safe_signed_off(self) -> None:
        res = _run("safe")
        self.assertTrue(res.routing.forwarded)
        self.assertEqual(res.decision.status, L.SIGNED_OFF)
        self.assertEqual(res.decision.approval_level, "L2_internal_approve")
        self.assertTrue(res.engineer_may_start)
        self.assertFalse(res.operator_required)

    def test_risky_signed_but_operator_required(self) -> None:
        res = _run("risky")
        self.assertEqual(res.decision.approval_level, "L3_user_approve")
        self.assertEqual(res.decision.risk_class, "risky")
        self.assertTrue(res.operator_required)          # L3 → operator must approve
        self.assertTrue(res.handoff.operator_required)

    def test_restricted_blocked_no_handoff(self) -> None:
        res = _run("blocked")
        self.assertEqual(res.decision.status, L.BLOCKED)
        self.assertEqual(res.decision.approval_level, "L4_restricted")
        self.assertIsNone(res.handoff)
        self.assertFalse(res.engineer_may_start)

    def test_escalated_meeting_no_signoff(self) -> None:
        res = _run("safe", meeting=_meeting(escalated=True))
        self.assertEqual(res.decision.status, L.ESCALATED)
        self.assertFalse(res.engineer_may_start)

    def test_non_techlead_signer_cannot_sign(self) -> None:
        d = L.tech_lead_decide(_brief(), _meeting(), _stack(),
                               design_system="ds", coding_convention="cc", signoff_by="be")
        self.assertEqual(d.status, L.ESCALATED)         # downgraded — no fake signoff
        self.assertTrue(any("tech-lead 가 아님" in x for x in L.validate_tech_lead_decision(d)))

    def test_missing_design_doc_fields_rejected(self) -> None:
        d = L.tech_lead_decide(_brief(), _meeting(), _stack(),
                               design_system="", coding_convention="")
        viol = L.validate_tech_lead_decision(d)
        self.assertTrue(any("design_system" in x for x in viol))
        self.assertTrue(any("coding_convention" in x for x in viol))


class HandoffGateTests(unittest.TestCase):
    def test_no_decision_no_start(self) -> None:
        self.assertFalse(L.can_engineer_start(None, None))

    def test_unsigned_decision_no_start(self) -> None:
        res = _run("blocked")  # BLOCKED decision, no handoff
        self.assertFalse(L.can_engineer_start(res.decision, None))

    def test_non_engineer_executor_rejected(self) -> None:
        # gateway/tech-lead/pm may NOT be the single executor
        for role in ("tech-lead", "gateway", "pm"):
            res = _run("safe", executor_role=role)
            self.assertFalse(res.engineer_may_start, f"{role} 가 executor 로 통과하면 안 됨")
            self.assertTrue(any("단일 executor" in x for x in res.violations))

    def test_handoff_carries_acceptance(self) -> None:
        res = _run("safe")
        self.assertEqual(res.handoff.acceptance_criteria, _brief().acceptance_criteria)
        self.assertEqual(res.handoff.executor_role, "be")

    def test_full_trace_recorded(self) -> None:
        res = _run("safe")
        self.assertEqual(res.trace[0], "gateway:route→tech-lead")
        self.assertEqual(res.trace[-1], "engineer:start")


if __name__ == "__main__":
    unittest.main()
