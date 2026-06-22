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

    # --- full autonomous loop: activate big goal → ... → done --------------
    def test_full_autonomous_loop_closes_goal(self) -> None:
        from forgekit_runtime.runtime.goal_exec_tick import GoalExecTicker
        from forgekit_runtime.runtime.goal_continuation_tick import GoalContinuationTicker

        goal = self._active_goal()
        sched = self._sched(_safe("docs", "콘솔 도움말 문구 개선"),
                            _safe("tests", "회귀 케이스 추가"))
        exec_t = GoalExecTicker(repo_root=self.repo, env=self.env)
        cont_t = GoalContinuationTicker(repo_root=self.repo, env=self.env)

        for n in range(1, 10):
            sched.tick(n)       # collect + autonomous decompose (once)
            cont_t.tick(n)      # advance draft children → active
            exec_t.tick(n)      # run each active child's safe packet → execution+verification
            cont_t.tick(100 + n)  # roll up finished children, close parent

        parent = self.store.get(goal.id)
        self.assertTrue(parent.children)                     # was decomposed autonomously
        self.assertEqual(parent.status, GoalStatus.DONE)     # long-term goal CLOSED
        for cid in parent.children:
            child = self.store.get(cid)
            self.assertEqual(child.status, GoalStatus.DONE)
            self.assertIn("execution", [e.kind for e in child.evidence])
            self.assertIn("verification", [e.kind for e in child.evidence])


if __name__ == "__main__":
    unittest.main()
