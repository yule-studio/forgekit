"""GW4-A — goal-tick self-improvement loop regression.

Proves the loop that makes ForgeKit self-managing, in its bounded posture:
- a tick turns signals into risk-classified packets, LINKS them to the goal, and
  appends one ``proposal`` evidence record per packet (append-only);
- packet ids are stable/content-derived → re-running a tick dedups the link but
  still records fresh evidence;
- a RISKY/BLOCKED packet moves an ACTIVE goal to ``awaiting_approval`` (operator
  decision); a SAFE-only tick leaves it ACTIVE;
- a tick NEVER marks a goal ``done`` and NEVER executes anything (bounded — no
  fake autonomy; execution stays behind the autopilot approval chain).

Hermetic: signals drive the packets; repo_root is an empty tmp dir so the
repo-local collector yields nothing whether or not ``nexus`` is importable.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _rel in (
    "packages/forgekit-runtime/src",
    "packages/forgekit-goal/src",
    "packages/forgekit-config/src",
    "packages/nexus/src",
):
    _p = str(_ROOT / _rel)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from forgekit_goal import Goal, GoalStatus, transitions
from forgekit_runtime.selfimprove import goal_tick
from forgekit_runtime.selfimprove import packet as P


class _Signal:
    def __init__(self, text: str) -> None:
        self.text = text


def _clock():
    n = {"i": 0}

    def now() -> str:
        n["i"] += 1
        return f"2026-06-22T01:00:{n['i']:02d}+00:00"

    return now


def _active_goal(now) -> Goal:
    g = Goal.create("self-manage ForgeKit", mode="auto", now=now)
    return transitions.apply(g, GoalStatus.ACTIVE, now=now)


class GoalTickTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_safe_signal_links_packet_records_evidence_stays_active(self) -> None:
        now = _clock()
        g = _active_goal(now)
        res = goal_tick.tick_goal(
            g, self.repo, signals=[_Signal("콘솔 도움말 문구 개선")], now=now
        )
        self.assertGreaterEqual(res.proposed, 1)
        self.assertEqual(res.approval_waiting, 0)
        self.assertEqual(res.goal.status, GoalStatus.ACTIVE)  # safe-only stays active
        self.assertEqual(len(res.goal.packets), res.proposed)  # linked
        self.assertEqual(len(res.goal.evidence), res.proposed)  # one proposal each
        self.assertTrue(all(e.kind == "proposal" for e in res.goal.evidence))
        self.assertNotEqual(res.goal.status, GoalStatus.DONE)

    def test_risky_signal_moves_goal_to_awaiting_approval(self) -> None:
        now = _clock()
        g = _active_goal(now)
        res = goal_tick.tick_goal(
            g, self.repo, signals=[_Signal("auth 권한 흐름 대규모 변경")], now=now
        )
        self.assertGreaterEqual(res.approval_waiting, 1)
        self.assertEqual(res.goal.status, GoalStatus.AWAITING_APPROVAL)
        # risk really classified risky (not safe)
        self.assertTrue(any(r[1] == P.RISK_RISKY for r in res.routes))

    def test_packet_id_stable_and_link_dedups_evidence_appends(self) -> None:
        now = _clock()
        g = _active_goal(now)
        sig = [_Signal("콘솔 도움말 문구 개선")]
        res1 = goal_tick.tick_goal(g, self.repo, signals=sig, now=now)
        res2 = goal_tick.tick_goal(res1.goal, self.repo, signals=sig, now=now)
        # same content → same packet id → linked once across two ticks
        self.assertEqual(len(res2.goal.packets), len(res1.goal.packets))
        # but evidence is append-only → grows each tick
        self.assertEqual(len(res2.goal.evidence), 2 * len(res1.goal.evidence))

    def test_tick_executes_nothing_and_never_done(self) -> None:
        now = _clock()
        g = _active_goal(now)
        res = goal_tick.tick_goal(
            g, self.repo, signals=[_Signal("deploy 시크릿 회전")], now=now
        )
        # blocked area → still no execution, goal not done
        self.assertNotEqual(res.goal.status, GoalStatus.DONE)
        self.assertGreaterEqual(res.approval_waiting, 1)  # blocked counts as waiting
        d = res.to_dict()
        self.assertEqual(d["goal_id"], res.goal.id)
        self.assertEqual(d["proposed"], res.proposed)

    def test_packet_id_is_deterministic(self) -> None:
        pkt = P.make_packet("X", area="docs")
        self.assertEqual(goal_tick.packet_id(pkt), goal_tick.packet_id(pkt))
        self.assertTrue(goal_tick.packet_id(pkt).startswith("packet-"))


if __name__ == "__main__":
    unittest.main()
