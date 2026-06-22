"""Goal-execution serve tick (G1) — always-on runtime physically executes approved goals.

Before G1, ``apply_approved_packet`` had no caller, so an operator-approved ``/goal``
packet never physically ran. This proves the seam is closed: ``forgekit runtime serve``
(via ``GoalExecTicker`` / the composed ``_build_tick_fn``) loads ACTIVE goals and runs
their linked safe-class packets through the REAL gated apply path — without faking:

- ACTIVE goal + safe packet → a serve tick executes it (real bounded write + real commit
  + ``execution`` evidence on the goal);
- awaiting_approval / blocked goal → NOT executed (skipped);
- risky / destructive packet → NOT executed (gated-recorded, decision evidence only);
- already-executed packet → NOT re-executed on the next tick (idempotent);
- a real ``BoundedDaemon.once(tick_fn)`` integration proving the operator entrypoint
  (``forgekit runtime serve``'s composed tick) reaches physical execution.

Hermetic: ``$FORGEKIT_HOME`` is a tempdir (store isolation) and the repo is a fresh
``git init`` TEMP repo — NEVER the real repo.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_goal import Goal, GoalStatus, GoalStore, transitions
from forgekit_runtime.runtime.goal_exec_tick import GoalExecTicker
from forgekit_runtime.selfimprove import goal_tick


class _Signal:
    def __init__(self, text: str) -> None:
        self.text = text


def _clock():
    n = {"i": 0}

    def now() -> str:
        n["i"] += 1
        return f"2026-06-22T04:00:{n['i']:02d}+00:00"

    return now


def _git_init(repo: Path) -> None:
    """A real, minimal git repo so the apply path can stage+commit (never the real repo)."""

    def g(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True, text=True)

    g("init", "-q")
    g("config", "user.email", "test@forgekit.local")
    g("config", "user.name", "forgekit-test")
    (repo / "README.md").write_text("# temp test repo\n", encoding="utf-8")
    g("add", "README.md")
    g("commit", "-q", "-m", "seed")


class GoalExecServeTest(unittest.TestCase):
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
    def _goal(self, signal_text: str, status: GoalStatus = GoalStatus.ACTIVE) -> Goal:
        """An ACTIVE (default) goal with one linked packet (+ proposal evidence)."""

        now = _clock()
        g = Goal.create("self-manage ForgeKit", mode="auto", now=now)
        g = transitions.apply(g, GoalStatus.ACTIVE, now=now)
        res = goal_tick.tick_goal(g, str(self.repo), signals=[_Signal(signal_text)], now=now)
        g = res.goal
        if status != g.status:
            g = transitions.apply(g, status, now=now)
        self.store.save(g)
        return g

    def _ticker(self) -> GoalExecTicker:
        return GoalExecTicker(repo_root=self.repo, env=self.env)

    # --- ACTIVE + safe → executed (real change + commit + evidence) --------
    def test_active_safe_goal_executes_on_serve_tick(self) -> None:
        goal = self._goal("콘솔 도움말 문구 개선")  # clean wording → safe
        self.assertEqual(len(goal.packets), 1)

        out = self._ticker().tick(1)

        self.assertGreaterEqual(out.executed, 1)
        self.assertTrue(out.executed_paths)
        # real bounded write landed under an allowed prefix
        written = self.repo / out.executed_paths[0]
        self.assertTrue(written.exists())
        # a real commit was created (HEAD advanced past the seed)
        log = subprocess.run(["git", "-C", str(self.repo), "log", "--oneline"],
                             capture_output=True, text=True, check=True).stdout
        self.assertIn("forgekit 자가개선 실행", log)
        # execution + verification evidence persisted to the goal
        reloaded = self.store.get(goal.id)
        kinds = [e.kind for e in reloaded.evidence]
        self.assertIn("execution", kinds)
        self.assertIn("verification", kinds)

    # --- awaiting_approval / blocked → NOT executed ------------------------
    def test_awaiting_approval_goal_not_executed(self) -> None:
        goal = self._goal("콘솔 도움말 문구 개선", status=GoalStatus.AWAITING_APPROVAL)
        out = self._ticker().tick(1)
        self.assertEqual(out.executed, 0)
        reloaded = self.store.get(goal.id)
        self.assertNotIn("execution", [e.kind for e in reloaded.evidence])
        self.assertEqual(reloaded.status, GoalStatus.AWAITING_APPROVAL)

    def test_blocked_goal_not_executed(self) -> None:
        goal = self._goal("콘솔 도움말 문구 개선", status=GoalStatus.BLOCKED)
        out = self._ticker().tick(1)
        self.assertEqual(out.executed, 0)
        reloaded = self.store.get(goal.id)
        self.assertNotIn("execution", [e.kind for e in reloaded.evidence])

    # --- risky / destructive packet on an ACTIVE goal → gated-recorded ----
    def test_risky_packet_not_executed_recorded_only(self) -> None:
        goal = self._goal("auth 권한 흐름 대규모 변경")  # risky wording
        ticker = self._ticker()
        out = ticker.tick(1)
        self.assertEqual(out.executed, 0)
        self.assertGreaterEqual(out.blocked_count, 1)
        reloaded = self.store.get(goal.id)
        kinds = [e.kind for e in reloaded.evidence]
        self.assertNotIn("execution", kinds)
        self.assertIn("decision", kinds)  # honest refusal recorded
        # idempotency: the refusal is recorded ONCE, not re-recorded every tick (no churn)
        out2 = ticker.tick(2)
        self.assertEqual(out2.executed, 0)
        reloaded2 = self.store.get(goal.id)
        refusals = [e for e in reloaded2.evidence if e.kind == "decision"]
        self.assertEqual(len(refusals), 1)

    def test_destructive_packet_not_executed(self) -> None:
        goal = self._goal("deploy 시크릿 회전")  # blocked/destructive wording
        out = self._ticker().tick(1)
        self.assertEqual(out.executed, 0)
        reloaded = self.store.get(goal.id)
        self.assertNotIn("execution", [e.kind for e in reloaded.evidence])

    # --- idempotency: not re-executed next tick ---------------------------
    def test_executed_packet_not_reexecuted_next_tick(self) -> None:
        goal = self._goal("콘솔 도움말 문구 개선")
        ticker = self._ticker()
        first = ticker.tick(1)
        self.assertGreaterEqual(first.executed, 1)

        head1 = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"],
                               capture_output=True, text=True, check=True).stdout.strip()

        second = ticker.tick(2)  # same packet already has execution evidence → skip
        self.assertEqual(second.executed, 0)

        head2 = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"],
                               capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(head1, head2)  # no new commit on the second tick

        # exactly one execution evidence record for the packet (not two)
        reloaded = self.store.get(goal.id)
        execs = [e for e in reloaded.evidence if e.kind == "execution"]
        self.assertEqual(len(execs), 1)

    # --- never marks the goal done ----------------------------------------
    def test_serve_tick_never_marks_goal_done(self) -> None:
        goal = self._goal("콘솔 도움말 문구 개선")
        self._ticker().tick(1)
        reloaded = self.store.get(goal.id)
        self.assertNotEqual(reloaded.status, GoalStatus.DONE)

    # --- full serve once() integration: operator entrypoint reaches exec ---
    def test_serve_once_reaches_goal_execution(self) -> None:
        """``BoundedDaemon.once(tick_fn)`` with the composed serve tick_fn physically
        executes an approved goal — proving the operator entrypoint reaches execution."""

        from forgekit_runtime.runtime.daemon import BoundedDaemon, TickOutcome

        goal = self._goal("콘솔 도움말 문구 개선")

        # the composed tick: autopilot pass is irrelevant here; the goal-exec pass must run.
        goal_exec = GoalExecTicker(repo_root=self.repo, env=self.env).tick_fn()

        def tick_fn(n: int) -> TickOutcome:
            return goal_exec(n)

        hb = self.repo / "hb.json"
        daemon = BoundedDaemon(heartbeat_path=hb, kill_switch_path=self.repo / "k",
                               sleep_fn=lambda s: None, pid=7)
        out = daemon.once(tick_fn)
        self.assertGreaterEqual(out.executed, 1)

        reloaded = self.store.get(goal.id)
        self.assertIn("execution", [e.kind for e in reloaded.evidence])

    def test_build_tick_fn_composes_goal_exec_pass(self) -> None:
        """The operator entrypoint ``_build_tick_fn`` (forgekit runtime serve) composes the
        goal-exec pass so an approved goal is physically executed through that exact path."""

        from forgekit_console.cli.runtime_cmd import _build_tick_fn

        goal = self._goal("콘솔 도움말 문구 개선")
        # _build_tick_fn's GoalExecTicker resolves its store from $FORGEKIT_HOME; set it.
        import os
        old = os.environ.get("FORGEKIT_HOME")
        os.environ["FORGEKIT_HOME"] = self._home.name
        try:
            tick_fn = _build_tick_fn(str(self.repo))
            out = tick_fn(1)
        finally:
            if old is None:
                os.environ.pop("FORGEKIT_HOME", None)
            else:
                os.environ["FORGEKIT_HOME"] = old

        self.assertGreaterEqual(out.executed, 1)
        reloaded = self.store.get(goal.id)
        self.assertIn("execution", [e.kind for e in reloaded.evidence])


if __name__ == "__main__":
    unittest.main()
