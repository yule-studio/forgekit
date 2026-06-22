"""Goal execution tick (G1) — the always-on serve loop physically advances ACTIVE goals.

Proves goal-driven execution **continuity** (not host uptime): a tick drives a goal's next
pending **safe-class** packet through the REAL gated bridge (``apply_approved_packet`` →
BoundedMutator + git commit + evidence), while risky/unapproved/awaiting goals are NOT executed.

Hermetic: a tempfile git repo is mutated; ``$FORGEKIT_HOME`` is a tempdir goal store. The real
repo is never touched. The autopilot observe path is neutralised (empty collector) so the test
isolates the goal-execution wiring.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_goal import Goal, GoalStatus, GoalStore, transitions
from forgekit_runtime.autopilot.runner import BoundedMutator
from forgekit_runtime.runtime.autopilot_tick import AutopilotTicker
from forgekit_runtime.runtime.goal_exec_tick import GoalExecReport, execute_active_goals
from forgekit_runtime.selfimprove import goal_tick


class _Signal:
    def __init__(self, text: str) -> None:
        self.text = text


def _clock():
    n = {"i": 0}

    def now() -> str:
        n["i"] += 1
        return f"2026-06-22T05:00:{n['i']:02d}+00:00"

    return now


def _git(repo: str, *args: str):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True)


def _init_repo(path: str) -> None:
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "seed@forgekit.local")
    _git(path, "config", "user.name", "seed")
    (Path(path) / "README.md").write_text("# seed\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "seed")


class _EmptyCollector:
    def collect(self, limit=0):
        return []


class GoalExecTickTest(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self._git = tempfile.TemporaryDirectory()
        _init_repo(self._git.name)
        self.env = {"FORGEKIT_HOME": self._home.name}
        self.repo = self._git.name

    def tearDown(self) -> None:
        self._home.cleanup()
        self._git.cleanup()

    def _mutator(self) -> BoundedMutator:
        return BoundedMutator(repo_root=Path(self.repo))

    def _active_goal(self, signal_text: str) -> Goal:
        now = _clock()
        g = Goal.create("self-manage ForgeKit", mode="auto", now=now)
        g = transitions.apply(g, GoalStatus.ACTIVE, now=now)
        res = goal_tick.tick_goal(g, self.repo, signals=[_Signal(signal_text)], now=now)
        GoalStore(env=self.env).save(res.goal)
        return res.goal

    # --- the core continuity: serve tick physically executes a safe goal packet -----------
    def test_active_safe_goal_is_physically_executed(self) -> None:
        goal = self._active_goal("콘솔 도움말 문구 개선")          # safe-class
        rep = execute_active_goals(self.repo, self._mutator(), env=self.env)
        self.assertEqual(rep.executed, 1)
        self.assertIn(goal.id, rep.goals_touched)
        # a REAL commit landed + execution/verification evidence persisted to the goal store.
        head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self.assertTrue(head)
        kinds = [e.kind for e in GoalStore(env=self.env).get(goal.id).evidence]
        self.assertIn("execution", kinds)
        self.assertIn("verification", kinds)

    def test_risky_goal_awaits_operator_not_executed(self) -> None:
        # a risky proposal moves the goal to awaiting_approval (goal_tick) → NOT auto-executed;
        # surfaced for the operator (/goal approve). honest: never auto-runs a risky step.
        self._active_goal("auth 권한 흐름 대규모 변경")            # risky
        rep = execute_active_goals(self.repo, self._mutator(), env=self.env)
        self.assertEqual(rep.executed, 0)
        self.assertGreaterEqual(rep.awaiting, 1)

    def test_forced_active_risky_packet_is_gate_blocked(self) -> None:
        # defense-in-depth: even if an ACTIVE goal carried a risky pending packet, the bridge
        # self-gates it off (OUTCOME_BLOCKED) — no mutation, counted as blocked, not executed.
        g = self._active_goal("auth 권한 흐름 대규모 변경")        # risky → goal now awaiting_approval
        g = transitions.apply(GoalStore(env=self.env).get(g.id), GoalStatus.ACTIVE, now=_clock())
        GoalStore(env=self.env).save(g)                            # force back to ACTIVE
        rep = execute_active_goals(self.repo, self._mutator(), env=self.env)
        self.assertEqual(rep.executed, 0)
        self.assertGreaterEqual(rep.blocked, 1)                    # gate refused, no fake execution

    def test_awaiting_approval_goal_is_surfaced_not_executed(self) -> None:
        g = self._active_goal("콘솔 도움말 문구 개선")
        g = transitions.apply(GoalStore(env=self.env).get(g.id), GoalStatus.AWAITING_APPROVAL,
                              now=_clock())
        GoalStore(env=self.env).save(g)
        rep = execute_active_goals(self.repo, self._mutator(), env=self.env)
        self.assertEqual(rep.executed, 0)
        self.assertEqual(rep.awaiting, 1)                          # operator decides via /goal approve

    def test_draft_goal_not_advanced(self) -> None:
        now = _clock()
        g = Goal.create("draft goal", now=now)                    # DRAFT (not active)
        GoalStore(env=self.env).save(g)
        rep = execute_active_goals(self.repo, self._mutator(), env=self.env)
        self.assertEqual(rep.executed, 0)
        self.assertEqual(rep.goals_touched, ())

    def test_dedupe_executed_packet_not_rerun(self) -> None:
        self._active_goal("콘솔 도움말 문구 개선")
        first = execute_active_goals(self.repo, self._mutator(), env=self.env)
        self.assertEqual(first.executed, 1)
        second = execute_active_goals(self.repo, self._mutator(), env=self.env)
        self.assertEqual(second.executed, 0)                       # already executed → skipped

    def test_no_store_is_empty_report(self) -> None:
        rep = execute_active_goals(self.repo, self._mutator(), env={"FORGEKIT_HOME": self._home.name})
        self.assertIsInstance(rep, GoalExecReport)                 # no active goals → empty, no crash

    # --- the wiring: the always-on AutopilotTicker DRIVES goal execution each tick ---------
    def test_autopilot_ticker_drives_goal_execution(self) -> None:
        goal = self._active_goal("콘솔 도움말 문구 개선")
        ticker = AutopilotTicker(repo_root=Path(self.repo), env=self.env,
                                 collector=_EmptyCollector(), execute_goals=True)
        outcome = ticker.tick(1)
        self.assertGreaterEqual(outcome.executed, 1)               # goal step executed in the tick
        self.assertIn("goal", outcome.summary)                     # surfaced honestly
        kinds = [e.kind for e in GoalStore(env=self.env).get(goal.id).evidence]
        self.assertIn("execution", kinds)

    def test_ticker_execute_goals_off_is_noop(self) -> None:
        goal = self._active_goal("콘솔 도움말 문구 개선")
        ticker = AutopilotTicker(repo_root=Path(self.repo), env=self.env,
                                 collector=_EmptyCollector(), execute_goals=False)
        ticker.tick(1)
        kinds = [e.kind for e in GoalStore(env=self.env).get(goal.id).evidence]
        self.assertNotIn("execution", kinds)                       # disabled → goal not advanced


if __name__ == "__main__":
    unittest.main()
