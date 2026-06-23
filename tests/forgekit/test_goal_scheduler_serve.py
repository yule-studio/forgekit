"""AUTONOMY — goal-scheduler serve tick collects work + autonomously decomposes.

The scheduler is the FRONT of the always-on goal loop — the stage that was missing
before: an operator activates a goal and the runtime, on its own, discovers work,
links packets, and decides the goal's shape. This proves it, honestly + bounded:

- an ACTIVE goal with no packets gets discovery run for it → packets linked with
  ``proposal`` evidence (the "collect state → choose packets" stage wired into serve);
- a "big" goal (discovery spanning ≥2 areas) is AUTONOMOUSLY decomposed into one
  child goal per area, each carrying its area's packets — forced down into packetized
  per-child execution, never run as one blob;
- a small (single-area) goal stays a leaf;
- a risky packet parks the goal/child at ``awaiting_approval`` (approval-needed split) —
  safe work stays ACTIVE and is left for the exec tick;
- the scheduler is idempotent (a goal already packetized/decomposed is skipped — no
  per-tick re-proposal churn) and executes NOTHING;
- end-to-end through the composed ``forgekit runtime serve`` tick: activate a big safe
  goal → scheduler decomposes + packetizes → exec runs each child's safe packet → verify
  → continuation rolls up → the long-term goal closes ``done`` with real evidence.

Hermetic: ``$FORGEKIT_HOME`` tempdir + a fresh ``git init`` TEMP repo; discovery is
INJECTED (a fake returning fixed packets) so the loop is deterministic and never depends
on real repo-local findings.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_goal import Goal, GoalStatus, GoalStore, planning, transitions
from forgekit_runtime.runtime.goal_scheduler_tick import GoalSchedulerTicker
from forgekit_runtime.selfimprove import packet as P
from forgekit_runtime.selfimprove.loop import SelfImprovementResult


def _git_init(repo: Path) -> None:
    def g(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True, text=True)

    g("init", "-q")
    g("config", "user.email", "test@forgekit.local")
    g("config", "user.name", "forgekit-test")
    (repo / "README.md").write_text("# temp test repo\n", encoding="utf-8")
    g("add", "README.md")
    g("commit", "-q", "-m", "seed")


def _fake_discover(*packets):
    """A discover() that always returns the given packets (deterministic)."""

    def discover(_repo_root):
        return SelfImprovementResult(packets=list(packets))

    return discover


def _safe(area, finding):
    return P.make_packet(finding, area=area)  # clean wording → safe


class GoalSchedulerServeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self._repo = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.addCleanup(self._repo.cleanup)
        self.env = {"FORGEKIT_HOME": self._home.name}
        self.repo = Path(self._repo.name)
        _git_init(self.repo)
        self.store = GoalStore(env=self.env)

    def _active_goal(self, title="self-manage ForgeKit") -> Goal:
        g = transitions.apply(Goal.create(title, mode="auto"), GoalStatus.ACTIVE)
        self.store.save(g)
        return g

    def _sched(self, *packets) -> GoalSchedulerTicker:
        return GoalSchedulerTicker(repo_root=self.repo, env=self.env,
                                   discover=_fake_discover(*packets))

    # --- collect/packetize a leaf (single area) ----------------------------
    def test_single_area_goal_packetized_as_leaf(self) -> None:
        goal = self._active_goal()
        out = self._sched(_safe("docs", "콘솔 도움말 문구 개선"),
                          _safe("docs", "사용법 정리")).tick(1)
        reloaded = self.store.get(goal.id)
        self.assertFalse(reloaded.children)  # one area → stays a leaf
        self.assertTrue(reloaded.packets)    # packets linked
        self.assertIn("proposal", [e.kind for e in reloaded.evidence])
        self.assertEqual(reloaded.status, GoalStatus.ACTIVE)  # safe → stays active

    # --- autonomous decomposition (≥2 areas) -------------------------------
    def test_big_goal_autonomously_decomposed_by_area(self) -> None:
        goal = self._active_goal()
        self._sched(_safe("docs", "문서 개선"),
                    _safe("tests", "회귀 추가"),
                    _safe("docs", "오타 수정")).tick(1)
        parent = self.store.get(goal.id)
        # decomposed into one child per distinct area (docs, tests)
        self.assertEqual(len(parent.children), 2)
        self.assertIn("plan", [e.kind for e in parent.evidence])
        self.assertFalse(parent.packets)  # parent is a pure plan node
        children = [self.store.get(c) for c in parent.children]
        titles = sorted(c.title for c in children)
        self.assertEqual(titles, ["docs", "tests"])
        # each child carries its own area's packets (proposal evidence)
        for c in children:
            self.assertTrue(c.packets)
            self.assertIn("proposal", [e.kind for e in c.evidence])

    # --- approval-needed split: risky parks at awaiting --------------------
    def test_risky_packet_parks_leaf_at_awaiting(self) -> None:
        goal = self._active_goal()
        out = self._sched(_safe("auth", "auth 권한 흐름 대규모 변경")).tick(1)  # risky wording
        reloaded = self.store.get(goal.id)
        self.assertEqual(reloaded.status, GoalStatus.AWAITING_APPROVAL)
        self.assertEqual(planning.approval_disposition(reloaded), planning.NEEDS_APPROVAL)
        self.assertTrue(out.waiting)

    # --- idempotent: a packetized goal is not re-packetized ----------------
    def test_packetized_goal_skipped_next_tick(self) -> None:
        goal = self._active_goal()
        sched = self._sched(_safe("docs", "문서 개선"))
        sched.tick(1)
        ev1 = len(self.store.get(goal.id).evidence)
        out = sched.tick(2)  # already has proposal evidence → skipped (no churn)
        self.assertEqual(len(self.store.get(goal.id).evidence), ev1)
        self.assertIn("수집 대상 goal 없음", out.summary)

    # --- draft goal is never packetized (only ACTIVE) ----------------------
    def test_draft_goal_not_packetized(self) -> None:
        g = Goal.create("draft goal")
        self.store.save(g)
        out = self._sched(_safe("docs", "x")).tick(1)
        self.assertFalse(self.store.get(g.id).packets)
        self.assertIn("수집 대상 goal 없음", out.summary)

    def _complete_design_chain(self, goal_id: str) -> None:
        """Record a valid PM→meeting→tech-lead(stack ≥2)→handoff chain on the goal's
        governance session, so the design gate becomes executable (the operator/tech-lead
        step the enforcement requires — never auto-faked by the runtime)."""

        from forgekit_runtime.runtime import goal_governance as gov
        import forgekit_runtime.decision_lane as L

        parent = self.store.get(goal_id)
        brief = L.PMBrief(topic=parent.title, problem="self-manage", user_value="운영 자동화",
                          acceptance_criteria=("동작 확인",), success_metrics=("회귀 green",))
        meeting = L.MeetingRecord("m-gov", "stack", agenda=("스택 비교",), participants=(
            L.ParticipantPosition("tech-lead", "support", "선택안"),
            L.ParticipantPosition("backend-engineer", "conditional", "우려", concerns=("x",))),
            decisions=("채택",))
        stack = L.StackComparison("stack", options=(
            L.StackOption("A", pros=("단순",), cons=("제약",)),
            L.StackOption("B", pros=("확장",), cons=("복잡",))),
            recommended="A", rationale="단순 우선", tradeoffs=("확장성 포기",))
        dec = L.tech_lead_decide(brief, meeting, stack, design_system="ds", coding_convention="cc",
                                 rationale="단순 우선")
        ho = L.handoff_to_engineer(dec, "backend-engineer", scope=("구현",), test_strategy="unit",
                                   acceptance_criteria=("동작 확인",))
        br = L.build_specialist_briefing(brief, dec, ho)
        gov.record_artifacts(goal_id, brief=brief, meeting=meeting, decision=dec, handoff=ho,
                             briefing=br, env=self.env)

    # --- full loop WITH governance: big goal → PM brief first → design chain → done ----
    def test_full_autonomous_loop_closes_goal(self) -> None:
        from forgekit_runtime.runtime.goal_exec_tick import GoalExecTicker
        from forgekit_runtime.runtime.goal_continuation_tick import GoalContinuationTicker
        from forgekit_runtime.runtime import goal_governance as gov

        goal = self._active_goal()
        sched = self._sched(_safe("docs", "콘솔 도움말 문구 개선"),
                            _safe("tests", "회귀 케이스 추가"))
        exec_t = GoalExecTicker(repo_root=self.repo, env=self.env)
        cont_t = GoalContinuationTicker(repo_root=self.repo, env=self.env)

        # tick 1: scheduler decomposes a big goal AND records the PM brief FIRST + marks
        # the parent governance-required (설계 강제). The design chain is NOT yet complete.
        sched.tick(1)
        parent = self.store.get(goal.id)
        self.assertTrue(gov.is_governance_required(parent))
        self.assertFalse(gov.design_ready(parent, env=self.env))   # 설계 미완 — exec 금지

        # specialist execution stays blocked until the design artifacts exist.
        cont_t.tick(1); exec_t.tick(1)
        for cid in parent.children:
            child = self.store.get(cid)
            self.assertNotIn("execution", [e.kind for e in child.evidence])   # 차단됨

        # operator/tech-lead completes the design chain → gate opens.
        self._complete_design_chain(goal.id)
        self.assertTrue(gov.design_ready(goal.id, env=self.env))

        for n in range(2, 12):
            sched.tick(n)
            cont_t.tick(n)
            exec_t.tick(n)        # now permitted — design chain is executable
            cont_t.tick(100 + n)

        parent = self.store.get(goal.id)
        self.assertTrue(parent.children)
        self.assertEqual(parent.status, GoalStatus.DONE)     # long-term goal CLOSED (설계 후)
        for cid in parent.children:
            child = self.store.get(cid)
            self.assertEqual(child.status, GoalStatus.DONE)
            self.assertIn("execution", [e.kind for e in child.evidence])
            self.assertIn("verification", [e.kind for e in child.evidence])


if __name__ == "__main__":
    unittest.main()
