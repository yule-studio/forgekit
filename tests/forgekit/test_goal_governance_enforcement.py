"""Goal governance enforcement (issue #450) — the design chain is a real execution rule.

Proves goal ≠ execution queue: a big/design-requiring goal binds the decision-lane chain
(PM brief → meeting → tech-lead decision with a ≥2-option stack → handoff) to its ``goal.id``
governance session, and:
- the PM brief is the FIRST artifact recorded at decomposition (acceptance #1);
- a governance-required goal's specialist execution is BLOCKED until the chain is executable
  (acceptance #2 — 설계 없는 구현 금지);
- the runtime never auto-fakes a tech-lead decision to pass its own gate (the seeded PM brief
  is honestly incomplete → readiness stays pending until a human completes it);
- non-governance goals are unaffected (backward compatible).

Hermetic / stdlib (tempdir FORGEKIT_HOME + temp git repo).
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

import forgekit_runtime.decision_lane as L
from forgekit_goal import Goal, GoalStatus, GoalStore, transitions
from forgekit_runtime.runtime import goal_governance as gov
from forgekit_runtime.runtime.goal_exec_tick import GoalExecTicker
from forgekit_runtime.runtime.goal_scheduler_tick import GoalSchedulerTicker
from forgekit_runtime.selfimprove import packet as P
from forgekit_runtime.selfimprove.loop import SelfImprovementResult


def _valid_chain(topic="ship feature"):
    brief = L.PMBrief(topic=topic, problem="문제", user_value="가치",
                      acceptance_criteria=("동작",), success_metrics=("green",))
    meeting = L.MeetingRecord("m1", topic, agenda=("스택",), participants=(
        L.ParticipantPosition("tech-lead", "support", "선택"),
        L.ParticipantPosition("backend-engineer", "conditional", "우려", concerns=("x",))),
        decisions=("채택",))
    stack = L.StackComparison(topic, options=(
        L.StackOption("A", pros=("단순",), cons=("제약",)),
        L.StackOption("B", pros=("확장",), cons=("복잡",))),
        recommended="A", rationale="단순 우선", tradeoffs=("확장성",))
    dec = L.tech_lead_decide(brief, meeting, stack, design_system="ds", coding_convention="cc",
                             rationale="단순 우선")
    ho = L.handoff_to_engineer(dec, "backend-engineer", scope=("구현",), test_strategy="unit",
                               acceptance_criteria=("동작",))
    br = L.build_specialist_briefing(brief, dec, ho)
    return dict(brief=brief, meeting=meeting, decision=dec, handoff=ho, briefing=br)


class BindingTests(unittest.TestCase):
    def setUp(self):
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.env = {"FORGEKIT_HOME": self._home.name}
        self.store = GoalStore(env=self.env)

    def _goal(self, status=GoalStatus.ACTIVE):
        g = Goal(id="g1", title="ship auth", intent="인증 강화", status=status)
        self.store.save(g)
        return g

    def test_frame_pm_brief_is_honest_goal_framing(self):
        g = self._goal()
        b = gov.frame_pm_brief(g)
        self.assertEqual(b.topic, "ship auth")
        self.assertEqual(b.problem, "인증 강화")
        # human-judgement fields are intentionally empty → not yet a valid brief (anti-fake).
        self.assertEqual(b.user_value, "")
        self.assertTrue(L.validate_pm_brief(b))

    def test_seeded_pm_brief_is_first_artifact_but_pending(self):
        g = gov.mark_governance_required(self._goal())
        gov.record_pm_brief(g, env=self.env)
        r = gov.governance_readiness(g, env=self.env)
        self.assertEqual(r.stage, L.STAGE_NO_PM_BRIEF)     # incomplete → pending, not faked
        self.assertFalse(r.executable)

    def test_full_chain_reaches_executable(self):
        g = gov.mark_governance_required(self._goal())
        gov.record_artifacts("g1", env=self.env, **_valid_chain("ship auth"))
        self.assertTrue(gov.design_ready(g, env=self.env))
        self.assertEqual(gov.governance_readiness(g, env=self.env).stage, L.STAGE_EXECUTABLE)

    def test_chain_order_enforced_decision_before_handoff(self):
        # recording a handoff/decision WITHOUT a valid brief never reaches executable.
        g = gov.mark_governance_required(self._goal())
        c = _valid_chain("ship auth")
        gov.record_artifacts("g1", env=self.env, decision=c["decision"], handoff=c["handoff"],
                             briefing=c["briefing"])  # no brief/meeting
        self.assertFalse(gov.design_ready(g, env=self.env))


class GateTests(BindingTests):
    def test_non_governance_goal_always_allowed(self):
        g = self._goal()
        allowed, stage, _ = gov.design_gate(g, {"g1": g}, env=self.env)
        self.assertTrue(allowed)
        self.assertEqual(stage, "not_required")

    def test_required_goal_blocked_until_ready(self):
        g = gov.mark_governance_required(self._goal())
        self.store.save(g)
        allowed, _, reason = gov.design_gate(g, {"g1": g}, env=self.env)
        self.assertFalse(allowed)
        self.assertTrue(reason)
        gov.record_artifacts("g1", env=self.env, **_valid_chain("ship auth"))
        allowed, stage, _ = gov.design_gate(g, {"g1": g}, env=self.env)
        self.assertTrue(allowed)

    def test_child_gates_on_governance_required_parent(self):
        parent = gov.mark_governance_required(
            Goal(id="p", title="feature", intent="i", status=GoalStatus.ACTIVE))
        child = Goal(id="c", title="area", intent="i", status=GoalStatus.ACTIVE, parent_id="p")
        by_id = {"p": parent, "c": child}
        # child inherits the parent's design gate.
        self.assertFalse(gov.design_gate(child, by_id, env=self.env)[0])
        gov.record_artifacts("p", env=self.env, **_valid_chain("feature"))
        self.assertTrue(gov.design_gate(child, by_id, env=self.env)[0])


class TickEnforcementTests(unittest.TestCase):
    def setUp(self):
        self._home = tempfile.TemporaryDirectory()
        self._repo = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.addCleanup(self._repo.cleanup)
        self.env = {"FORGEKIT_HOME": self._home.name}
        self.repo = Path(self._repo.name)
        subprocess.run(["git", "-C", str(self.repo), "init", "-q"], check=True)
        self.store = GoalStore(env=self.env)

    def _big_goal_decomposed(self):
        g = transitions.apply(Goal.create("self-manage", mode="auto"), GoalStatus.ACTIVE)
        self.store.save(g)
        pkts = [P.make_packet("clarify docs", area="docs"),
                P.make_packet("add test", area="tests")]
        sched = GoalSchedulerTicker(repo_root=self.repo, env=self.env,
                                    discover=lambda _r: SelfImprovementResult(packets=pkts))
        sched.tick(1)
        return self.store.get(g.id)

    def test_scheduler_records_pm_brief_first_on_big_goal(self):
        parent = self._big_goal_decomposed()
        self.assertTrue(gov.is_governance_required(parent))
        # the FIRST governance event for the goal is the PM brief (acceptance #1).
        events = L.replay_governance_log(parent.id, env=self.env)
        self.assertTrue(events)
        self.assertEqual(events[0].kind, L.KIND_BRIEF)

    def test_exec_tick_blocks_governed_goal_without_design(self):
        parent = self._big_goal_decomposed()
        # activate a child and try to exec — must be blocked (no design chain yet).
        cid = parent.children[0]
        child = transitions.apply(self.store.get(cid), GoalStatus.ACTIVE)
        self.store.save(child)
        out = GoalExecTicker(repo_root=self.repo, env=self.env).tick(1)
        self.assertEqual(out.executed, 0)
        self.assertIn("설계미완차단", out.summary)
        # a refusal record exists; nothing was physically executed.
        child = self.store.get(cid)
        self.assertNotIn("execution", [e.kind for e in child.evidence])


if __name__ == "__main__":
    unittest.main()
