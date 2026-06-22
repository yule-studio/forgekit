"""GW-EXEC — goal-continuation serve tick CLOSES the long-term goal loop.

The goal-exec tick (G1) runs an ACTIVE goal's approved safe packets, but it never
advances a *decomposed* plan or closes a goal. This proves the continuation tick
seals that — turning "goal은 있는데 실행 teeth가 약함" into a goal that actually
finishes, without faking and without bypassing the approval chain:

- a decomposed parent (``/goal plan``) sequences its children one at a time: the
  continuation tick activates the next DRAFT child so the exec tick can run it;
- a child whose safe packet really executed + verified rolls up ``active -> done``
  (evidence-gated — a verification record must exist);
- when every child is done the parent closes ``active -> done`` with roll-up
  evidence — the whole long-term goal is CLOSED with real evidence underneath;
- a child whose packet is gate-blocked (risky) is NEVER completed — it surfaces as
  a replan (operator), so the approval chain is not bypassed;
- the composed ``forgekit runtime serve`` tick (``_build_tick_fn``) reaches all of
  this end-to-end (exec pass + continuation pass in one tick).

Hermetic: ``$FORGEKIT_HOME`` tempdir + a fresh ``git init`` TEMP repo (never the
real repo), same fixture shape as ``test_goal_exec_serve``.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_goal import Goal, GoalStatus, GoalStore, planning, transitions
from forgekit_runtime.runtime.goal_continuation_tick import GoalContinuationTicker
from forgekit_runtime.runtime.goal_exec_tick import GoalExecTicker
from forgekit_runtime.selfimprove import goal_tick


class _Signal:
    def __init__(self, text: str) -> None:
        self.text = text


def _clock():
    n = {"i": 0}

    def now() -> str:
        n["i"] += 1
        return f"2026-06-22T06:00:{n['i']:02d}+00:00"

    return now


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


class GoalContinuationServeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self._repo = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.addCleanup(self._repo.cleanup)
        self.env = {"FORGEKIT_HOME": self._home.name}
        self.repo = Path(self._repo.name)
        _git_init(self.repo)
        self.store = GoalStore(env=self.env)

    # --- helpers -----------------------------------------------------------
    def _planned_parent(self, *step_signals: str) -> Goal:
        """An ACTIVE parent decomposed into one child per signal, each child carrying a
        linked packet (proposal evidence) derived from its signal wording. Children stay
        DRAFT — the continuation tick is what activates them."""

        now = _clock()
        parent = transitions.apply(
            Goal.create("self-manage ForgeKit", mode="auto", now=now),
            GoalStatus.ACTIVE, now=now)
        steps = [planning.PlanStep(f"step: {s}") for s in step_signals]
        parent, children = planning.decompose(parent, steps, now=now)
        # link a packet to each child from its signal (goal_tick leaves a safe child DRAFT)
        linked = []
        for child, sig in zip(children, step_signals):
            res = goal_tick.tick_goal(child, str(self.repo), signals=[_Signal(sig)], now=now)
            linked.append(res.goal)
            self.store.save(res.goal)
        self.store.save(parent)
        return parent

    def _exec(self) -> GoalExecTicker:
        return GoalExecTicker(repo_root=self.repo, env=self.env)

    def _cont(self) -> GoalContinuationTicker:
        return GoalContinuationTicker(repo_root=self.repo, env=self.env)

    # --- continuation activates the next draft child -----------------------
    def test_continuation_activates_first_draft_child(self) -> None:
        parent = self._planned_parent("콘솔 도움말 문구 개선", "콘솔 사용법 정리")
        kids = [self.store.get(c) for c in parent.children]
        self.assertTrue(all(k.status == GoalStatus.DRAFT for k in kids))

        self._cont().tick(1)

        kids2 = [self.store.get(c) for c in parent.children]
        self.assertEqual(kids2[0].status, GoalStatus.ACTIVE)   # first step advanced
        self.assertEqual(kids2[1].status, GoalStatus.DRAFT)    # second waits (sequential)

    # --- full lifecycle: decompose → exec → verify → rollup → done ---------
    def test_full_plan_runs_to_done(self) -> None:
        parent = self._planned_parent("콘솔 도움말 문구 개선", "콘솔 사용법 정리")
        exec_t, cont_t = self._exec(), self._cont()

        # Drive the loop: each round = continuation (advance/rollup) then exec (run packet).
        # Bounded: a 2-step plan closes well within a few rounds.
        for n in range(1, 8):
            cont_t.tick(n)
            exec_t.tick(n)
        cont_t.tick(99)  # final roll-up of the last child + parent completion

        reloaded = self.store.get(parent.id)
        self.assertEqual(reloaded.status, GoalStatus.DONE)  # the long-term goal CLOSED
        # both children closed with real evidence (executed + verified)
        for cid in parent.children:
            child = self.store.get(cid)
            self.assertEqual(child.status, GoalStatus.DONE)
            kinds = [e.kind for e in child.evidence]
            self.assertIn("execution", kinds)
            self.assertIn("verification", kinds)
        # parent carries a roll-up verification record (evidence-gated done)
        self.assertIn("verification", [e.kind for e in reloaded.evidence])

    # --- evidence-gated: parent never closes while a step is unexecuted -----
    def test_parent_not_done_before_steps_execute(self) -> None:
        parent = self._planned_parent("콘솔 도움말 문구 개선", "콘솔 사용법 정리")
        # only continuation, no exec → children can be activated but never executed/verified
        for n in range(1, 5):
            self._cont().tick(n)
        reloaded = self.store.get(parent.id)
        self.assertNotEqual(reloaded.status, GoalStatus.DONE)  # no fake-green

    # --- blocked child: bounded replan RETRY abandons the dead packet ------
    def test_blocked_step_replans_not_done(self) -> None:
        parent = self._planned_parent("auth 권한 흐름 대규모 변경")  # risky → gate-blocked
        exec_t, cont_t = self._exec(), self._cont()  # default max_replan_attempts=1
        for n in range(1, 4):
            cont_t.tick(n)          # advance draft child → active
            exec_t.tick(n)          # exec gate-refuses → decision evidence, no execution
            cont_t.tick(100 + n)    # replan RETRY: unlink dead packet + replan evidence
        reloaded = self.store.get(parent.id)
        self.assertNotEqual(reloaded.status, GoalStatus.DONE)  # never auto-completes
        child = self.store.get(parent.children[0])
        self.assertIn("decision", [e.kind for e in child.evidence])    # really gate-refused
        self.assertNotIn("execution", [e.kind for e in child.evidence])
        # replan RETRY ran: a replan record was written and the dead packet was abandoned
        self.assertIn("replan", [e.kind for e in child.evidence])
        self.assertEqual(child.packets, ())  # exhausted packet unlinked (re-drivable)

    # --- blocked child: escalates to operator once retries exhausted -------
    def test_blocked_step_escalates_to_operator(self) -> None:
        parent = self._planned_parent("auth 권한 흐름 대규모 변경")
        exec_t = self._exec()
        # max_replan_attempts=0 → first stuck detection escalates immediately (no retry)
        cont_t = GoalContinuationTicker(repo_root=self.repo, env=self.env, max_replan_attempts=0)
        ever_waiting = False
        for n in range(1, 4):
            cont_t.tick(n)
            exec_t.tick(n)
            ever_waiting = ever_waiting or cont_t.tick(100 + n).waiting
        child = self.store.get(parent.children[0])
        # escalated to operator with the blocked reason persisted (append-only)
        self.assertEqual(child.status, GoalStatus.AWAITING_APPROVAL)
        self.assertIn("blocked", [e.kind for e in child.evidence])
        self.assertIsNotNone(planning.blocked_reason(child))
        self.assertTrue(ever_waiting)  # the escalation tick surfaced operator-actionable
        self.assertNotEqual(self.store.get(parent.id).status, GoalStatus.DONE)

    # --- idempotent: re-ticking a finished plan is a no-op -----------------
    def test_done_plan_is_idempotent(self) -> None:
        parent = self._planned_parent("콘솔 도움말 문구 개선")
        exec_t, cont_t = self._exec(), self._cont()
        for n in range(1, 6):
            cont_t.tick(n)
            exec_t.tick(n)
        cont_t.tick(50)
        reloaded = self.store.get(parent.id)
        self.assertEqual(reloaded.status, GoalStatus.DONE)
        ev_count = len(reloaded.evidence)
        out = cont_t.tick(51)  # nothing left to advance
        again = self.store.get(parent.id)
        self.assertEqual(len(again.evidence), ev_count)  # no churn
        self.assertFalse(out.waiting)

    # --- composed serve tick reaches continuation --------------------------
    def test_build_tick_fn_composes_continuation_pass(self) -> None:
        from forgekit_console.cli.runtime_cmd import _build_tick_fn

        parent = self._planned_parent("콘솔 도움말 문구 개선")
        old = os.environ.get("FORGEKIT_HOME")
        os.environ["FORGEKIT_HOME"] = self._home.name
        try:
            tick_fn = _build_tick_fn(str(self.repo))
            for n in range(1, 6):
                tick_fn(n)
        finally:
            if old is None:
                os.environ.pop("FORGEKIT_HOME", None)
            else:
                os.environ["FORGEKIT_HOME"] = old

        reloaded = self.store.get(parent.id)
        self.assertEqual(reloaded.status, GoalStatus.DONE)  # serve closed the goal end-to-end


if __name__ == "__main__":
    unittest.main()
