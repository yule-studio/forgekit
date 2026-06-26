"""Ponytail (simpler-path) consult artifact — PM/Tech-Lead chain regression (issue #423).

Pins the governance rule that a design must record a *simpler-path* review before a new
dependency / abstraction is approved:
- a new-dependency/abstraction signoff with NO ponytail consult is INCOMPLETE → rejected,
  never signed off (requirement 2 + 5);
- a required consult that was *consulted* needs a verdict (in PONYTAIL_VERDICTS) + notes;
- a required consult that was *refused/ignored* must log the rejected simpler alternative
  AND why the more complex path is needed (requirement 3) — else incomplete;
- the consult is carried into the handoff + specialist briefing packet (requirement 4);
- ponytail is NOT the final approver: a *complete* consult (even verdict='keep') lets the
  tech-lead sign off normally.

Hermetic + pure; role identities resolve through the registry SSoT.
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
        topic="refresh-token 회전", problem="세션 만료 처리 미흡",
        user_value="탈취 위험 감소", acceptance_criteria=("토큰 회전",),
        success_metrics=("탈취 0건",))


def _meeting() -> L.MeetingRecord:
    return L.MeetingRecord(
        meeting_id="m-rt-1", topic="토큰 스택", agenda=("스택 비교",),
        participants=(
            L.ParticipantPosition("tech-lead", "support", "회전 권장"),
            L.ParticipantPosition("be", "conditional", "blacklist 선행", concerns=("재사용",)),
        ),
        decisions=("jwt rotation 채택",))


def _stack(ponytail=None) -> L.StackComparison:
    return L.StackComparison(
        decision_topic="토큰 스택",
        options=(
            L.StackOption("자체 구현", pros=("의존성 0",), cons=("보안 검증 부담",)),
            L.StackOption("외부 lib X", pros=("빠른 도입",), cons=("새 dependency",)),
        ),
        recommended="외부 lib X", rationale="검증된 라이브러리",
        tradeoffs=("의존성 증가",), ponytail=ponytail)


def _decide(*, introduces_dependency=False, ponytail=None, stack=None) -> L.TechLeadDecision:
    return L.tech_lead_decide(
        _brief(), _meeting(), stack or _stack(),
        design_system="ds-token", coding_convention="cc-token",
        rationale="lib 채택", introduces_dependency=introduces_dependency, ponytail=ponytail)


class PonytailValidatorTests(unittest.TestCase):
    def test_not_required_is_complete(self):
        self.assertEqual(L.validate_ponytail(L.PonytailConsult(required=False)), ())

    def test_consulted_needs_verdict_and_notes(self):
        bad = L.PonytailConsult(required=True, consulted=True, verdict="", notes="")
        self.assertTrue(L.validate_ponytail(bad))
        ok = L.PonytailConsult(required=True, consulted=True, verdict="keep", notes="자체구현 부담")
        self.assertEqual(L.validate_ponytail(ok), ())

    def test_verdict_must_be_in_vocabulary(self):
        bad = L.PonytailConsult(required=True, consulted=True, verdict="meh", notes="x")
        self.assertTrue(L.validate_ponytail(bad))
        for verdict in L.PONYTAIL_VERDICTS:
            ok = L.PonytailConsult(required=True, consulted=True, verdict=verdict, notes="근거")
            self.assertEqual(L.validate_ponytail(ok), (), verdict)

    def test_refused_consult_needs_rejection_trace(self):
        # required but NOT consulted → must log rejected_alternative + why_more_complex.
        bare = L.PonytailConsult(required=True, consulted=False)
        self.assertTrue(L.validate_ponytail(bare))
        traced = L.PonytailConsult(required=True, consulted=False,
                                   rejected_alternative="자체 구현", why_more_complex="검증 리소스 부족")
        self.assertEqual(L.validate_ponytail(traced), ())


class TechLeadGateTests(unittest.TestCase):
    def test_new_dependency_without_ponytail_is_rejected(self):
        d = _decide(introduces_dependency=True)   # no ponytail attached
        self.assertTrue(L.validate_tech_lead_decision(d))
        # no fake signoff: the incomplete artifact is downgraded, never signed off.
        self.assertNotIn(d.status, (L.SIGNED_OFF, L.CONDITIONAL))

    def test_new_dependency_with_complete_ponytail_signs_off(self):
        pony = L.PonytailConsult(required=True, consulted=True, verdict="keep",
                                 notes="자체 구현은 보안 검증 부담이 큼")
        d = _decide(introduces_dependency=True, ponytail=pony)
        self.assertEqual(L.validate_tech_lead_decision(d), ())
        self.assertEqual(d.status, L.SIGNED_OFF)

    def test_ponytail_not_required_when_no_new_dep(self):
        d = _decide(introduces_dependency=False)
        self.assertEqual(L.validate_tech_lead_decision(d), ())

    def test_required_but_empty_is_incomplete_schema(self):
        # explicitly-required consult with an empty body → incomplete (requirement 5).
        pony = L.PonytailConsult(required=True, consulted=False)
        d = _decide(ponytail=pony)
        self.assertTrue(any("ponytail" in v for v in L.validate_tech_lead_decision(d)))

    def test_consult_inherited_from_stack_comparison(self):
        pony = L.PonytailConsult(required=True, consulted=True, verdict="use-native", notes="표준 crypto")
        d = _decide(introduces_dependency=True, stack=_stack(ponytail=pony))
        # the decision picked up the stack-stage consult.
        self.assertIsNotNone(d.ponytail)
        self.assertEqual(d.ponytail.verdict, "use-native")
        self.assertEqual(L.validate_tech_lead_decision(d), ())


class CarryThroughTests(unittest.TestCase):
    def _signed(self):
        pony = L.PonytailConsult(required=True, consulted=False,
                                 rejected_alternative="자체 구현", why_more_complex="검증 리소스 부족")
        return _decide(introduces_dependency=True, ponytail=pony)

    def test_handoff_and_briefing_carry_consult(self):
        d = self._signed()
        h = L.handoff_to_engineer(d, "backend-engineer", scope=("API",), test_strategy="unit",
                                  acceptance_criteria=("회전",))
        self.assertIsNotNone(h.ponytail)
        b = L.build_specialist_briefing(_brief(), d, h)
        self.assertIsNotNone(b.ponytail)
        # the work order surfaces the consult to the specialist.
        self.assertTrue(any("ponytail" in ln for ln in b.lines()))
        self.assertTrue(L.can_specialist_start(_brief(), d, h))

    def test_decision_log_trail_surfaces_consult(self):
        d = self._signed()
        ev = L.GovernanceEvent(session_id="s", kind=L.KIND_DECISION, actor="tech-lead",
                               summary="signoff", valid=True, ref=d.decision_id,
                               payload=d.to_dict())
        trail = L.decision_trail_from_log((ev,))
        self.assertTrue(any("ponytail" in ln for ln in trail))

    def test_serialisation_roundtrip(self):
        pony = L.PonytailConsult(required=True, consulted=True, verdict="simplify", notes="줄여라")
        d = pony.to_dict()
        self.assertEqual(d["ponytail_required"], True)
        self.assertEqual(d["ponytail_verdict"], "simplify")
        self.assertEqual(d["ponytail_notes"], "줄여라")
        back = L.PonytailConsult.from_dict(d)
        self.assertEqual(back, pony)


if __name__ == "__main__":
    unittest.main()
