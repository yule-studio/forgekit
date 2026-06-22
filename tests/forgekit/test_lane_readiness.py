"""PM→gateway→tech-lead→specialist readiness gate + replay-able decision log + /council.

Proves the governance chain's preconditions are ENFORCED, REPLAY-ABLE, and OPERATOR-VISIBLE
— the operator's must-verify items:

- **no PM artifact → the tech-lead lane is not executable** (readiness.executable False,
  stage names the missing PM brief);
- **no signed tech-lead decision → specialist execution is impossible**;
- a full valid chain → executable, and that agrees with ``can_engineer_start``;
- the decision log is replay-able: ``record_lane_artifacts`` → ``replay_governance_log`` →
  ``readiness_from_log`` reconstructs the same gate, and a rubber-stamp meeting is logged
  ``valid=False`` so the replay refuses to call it ready (anti-fake);
- ``/council <session>`` surfaces the readiness ladder from the persisted log.

Hermetic: a tmp FORGEKIT_HOME isolates the log; identities via the registry SSoT.
"""

from __future__ import annotations

import sys
import tempfile
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
    "apps/forgekit-console/src",
):
    _p = str(_ROOT / _rel)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from forgekit_runtime import decision_lane as D


def _brief():
    return D.PMBrief(topic="알림", problem="p", user_value="v",
                     acceptance_criteria=("a",), success_metrics=("m",))


def _meeting(dissent=True):
    parts = (D.ParticipantPosition("tech-lead", "support", "ok"),
             D.ParticipantPosition("be", "oppose" if dissent else "support", "음",
                                   concerns=("c",) if dissent else ()))
    return D.MeetingRecord(meeting_id="m1", topic="t", agenda=("a",),
                           participants=parts, decisions=("go",))


def _stack():
    return D.StackComparison(decision_topic="t", recommended="a",
                             options=(D.StackOption("a", pros=("p",), cons=("c",)),
                                      D.StackOption("b", pros=("p",), cons=("c",))),
                             rationale="r", tradeoffs=("t",))


def _full_lane():
    res = D.run_lane(_brief(), _meeting(), _stack(), design_system="ds",
                     coding_convention="cc", executor_role="be", scope=("x",),
                     test_strategy="unit", risk_class="safe")
    return res.decision, res.handoff


class ReadinessGateTests(unittest.TestCase):
    def test_no_pm_brief_not_executable(self) -> None:
        r = D.assess_lane_readiness()
        self.assertEqual(r.stage, D.STAGE_NO_PM_BRIEF)
        self.assertFalse(r.executable)
        self.assertTrue(any("PM brief" in m for m in r.missing))

    def test_no_decision_specialist_blocked(self) -> None:
        r = D.assess_lane_readiness(brief=_brief(), meeting=_meeting())
        self.assertEqual(r.stage, D.STAGE_DECISION_PENDING)
        self.assertFalse(r.executable)
        self.assertTrue(any("decision" in m for m in r.missing))

    def test_invalid_brief_blocks(self) -> None:
        bad = D.PMBrief(topic="t", problem="p", user_value="v")  # no acceptance/metrics
        r = D.assess_lane_readiness(brief=bad)
        self.assertFalse(r.executable)
        self.assertTrue(r.blocking)

    def test_full_chain_executable_agrees_with_can_start(self) -> None:
        decision, handoff = _full_lane()
        r = D.assess_lane_readiness(brief=_brief(), meeting=_meeting(),
                                    decision=decision, handoff=handoff)
        self.assertEqual(r.stage, D.STAGE_EXECUTABLE)
        self.assertTrue(r.executable)
        self.assertEqual(r.executable, D.can_engineer_start(decision, handoff))

    def test_handoff_pending_when_no_handoff(self) -> None:
        decision, _ = _full_lane()
        r = D.assess_lane_readiness(brief=_brief(), meeting=_meeting(), decision=decision)
        self.assertEqual(r.stage, D.STAGE_HANDOFF_PENDING)
        self.assertFalse(r.executable)


class DecisionLogReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = {"FORGEKIT_HOME": tempfile.mkdtemp()}

    def test_replay_reconstructs_executable(self) -> None:
        decision, handoff = _full_lane()
        D.record_lane_artifacts("s1", brief=_brief(), meeting=_meeting(),
                                decision=decision, handoff=handoff, env=self.env, at="2026-06-22")
        events = D.replay_governance_log("s1", env=self.env)
        self.assertEqual(len(events), 4)
        self.assertEqual(events[0].kind, D.KIND_BRIEF)
        r = D.readiness_from_log(events)
        self.assertEqual(r.stage, D.STAGE_EXECUTABLE)
        self.assertTrue(r.executable)

    def test_replay_partial_is_blocked(self) -> None:
        D.record_lane_artifacts("s2", brief=_brief(), meeting=_meeting(), env=self.env)
        r = D.readiness_from_log(D.replay_governance_log("s2", env=self.env))
        self.assertEqual(r.stage, D.STAGE_DECISION_PENDING)
        self.assertFalse(r.executable)

    def test_fake_meeting_logged_invalid_blocks_replay(self) -> None:
        decision, handoff = _full_lane()
        D.record_lane_artifacts("s3", brief=_brief(), meeting=_meeting(dissent=False),  # rubber-stamp
                                decision=decision, handoff=handoff, env=self.env)
        events = D.replay_governance_log("s3", env=self.env)
        meeting_ev = [e for e in events if e.kind == D.KIND_MEETING][0]
        self.assertFalse(meeting_ev.valid)                    # validator caught it at record time
        r = D.readiness_from_log(events)
        self.assertEqual(r.stage, D.STAGE_MEETING_PENDING)    # replay refuses to advance
        self.assertFalse(r.executable)

    def test_unknown_event_kind_refused(self) -> None:
        with self.assertRaises(ValueError):
            D.record_governance_event(D.GovernanceEvent("s", "bogus"), env=self.env)


class CouncilSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.mkdtemp()

    def _route(self, line):
        from forgekit_console.commands.parser import parse_input
        from forgekit_console.commands.router import build_default_context, route
        return route(parse_input(line), build_default_context(Path(".")))

    def test_council_renders_readiness_from_log(self) -> None:
        env = {"FORGEKIT_HOME": self.home}
        D.record_lane_artifacts("sx", brief=_brief(), meeting=_meeting(), env=env)
        import os
        os.environ["FORGEKIT_HOME"] = self.home
        r = self._route("/council sx")
        joined = "\n".join(r.lines)
        self.assertIn("lane readiness", joined)
        self.assertIn("실행 불가", joined)                    # no decision yet
        self.assertIn("tech-lead", joined)

    def test_council_usage_without_session(self) -> None:
        r = self._route("/council")
        self.assertIn("readiness", "\n".join(r.lines))

    def test_council_surfaces_decision_trail(self) -> None:
        # operator can trace "누가 무엇을 결정했는지" — a full lane's design decision
        # facts (design system / stack) appear in the /council trail block.
        env = {"FORGEKIT_HOME": self.home}
        decision, handoff = _full_lane()
        D.record_lane_artifacts("st", brief=_brief(), meeting=_meeting(),
                                decision=decision, handoff=handoff, env=env)
        import os
        os.environ["FORGEKIT_HOME"] = self.home
        joined = "\n".join(self._route("/council st").lines)
        self.assertIn("결정 트레일", joined)
        self.assertIn("design=", joined)


if __name__ == "__main__":
    unittest.main()
