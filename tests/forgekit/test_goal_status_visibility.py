"""Goal-continuity visibility in the always-on runtime status surface (#372). Pure / stdlib.

Proves the operator can SEE, from `forgekit runtime status` / `/daemon`, what the serve loop is
doing to long-term goals: how many are ACTIVE (auto-advanced), how many are awaiting_approval
(action-needed), blocked/done, and what was last executed — read from the REAL goal store, honest
(no store → honest "없음", never fake progress).
"""

from __future__ import annotations

import tempfile
import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_goal import Goal, GoalStatus, GoalStore, transitions
from forgekit_runtime.runtime import surface
from forgekit_runtime.runtime.goal_status import goal_continuity_lines, goal_continuity_status


def _clock():
    n = {"i": 0}

    def now() -> str:
        n["i"] += 1
        return f"2026-06-22T07:00:{n['i']:02d}+00:00"

    return now


class GoalContinuityStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.env = {"FORGEKIT_HOME": self._home.name}
        self.store = GoalStore(env=self.env)
        self.now = _clock()

    def _active(self, title: str) -> Goal:
        g = transitions.apply(Goal.create(title, now=self.now), GoalStatus.ACTIVE, now=self.now)
        self.store.save(g)
        return g

    def test_no_goals_is_honest_empty(self) -> None:
        st = goal_continuity_status(env=self.env)
        self.assertTrue(st.available)
        self.assertEqual(st.total, 0)
        self.assertIn("활성 goal 없음", "\n".join(goal_continuity_lines(env=self.env)))

    def test_counts_active_awaiting_and_last_work(self) -> None:
        g = self._active("ship feature")
        g = g.add_evidence("execution", "safe packet 실행 — 콘솔 문구 개선", ref="p1", now=self.now)
        self.store.save(g)
        # a second goal parked at awaiting_approval (risky proposal → operator decision)
        gw = self._active("harden auth")
        gw = transitions.apply(gw, GoalStatus.AWAITING_APPROVAL, now=self.now)
        self.store.save(gw)

        st = goal_continuity_status(env=self.env)
        self.assertEqual(st.active, 1)
        self.assertEqual(st.awaiting_approval, 1)
        self.assertIn("콘솔 문구 개선", st.last_work)      # real last execution, not a projection
        self.assertEqual(st.last_work_goal, g.id)

    def test_lines_surface_action_needed_for_awaiting(self) -> None:
        gw = self._active("harden auth")
        gw = transitions.apply(gw, GoalStatus.AWAITING_APPROVAL, now=self.now)
        self.store.save(gw)
        text = "\n".join(goal_continuity_lines(env=self.env))
        self.assertIn("awaiting 1", text)
        self.assertIn("action-needed", text)               # operator told what to do
        self.assertIn("/goal approve", text)

    def test_no_store_is_honest_not_fake(self) -> None:
        # an unreadable/absent store must report honestly, never invent progress.
        class _BadStore:
            def load_all(self):
                raise OSError("boom")
        st = goal_continuity_status(store=_BadStore())
        self.assertFalse(st.available)
        self.assertIn("goal store", "\n".join(goal_continuity_lines(store=_BadStore())))


class DaemonStatusWiringTests(unittest.TestCase):
    def test_daemon_status_includes_goal_loop_line(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            env = {"FORGEKIT_HOME": home}
            now = _clock()
            g = transitions.apply(Goal.create("ship feature", now=now), GoalStatus.ACTIVE, now=now)
            GoalStore(env=env).save(g)
            text = "\n".join(surface.daemon_status_lines(env=env))
            self.assertIn("goal-loop", text)               # always-on status now surfaces goals
            self.assertIn("active 1", text)


if __name__ == "__main__":
    unittest.main()
