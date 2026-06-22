"""GW-EXEC — goal planning (decomposition + progress + continuation) regression.

Proves the pure execution-core brain that turns a big goal into a closable plan:
- ``decompose`` creates ordered child goals + a ``plan`` evidence record on the
  parent, and executes NOTHING (creates records only);
- ``tally_packets`` / ``is_goal_complete`` derive completion from append-only
  evidence — a goal with a pending or gate-blocked packet is NOT complete
  (no fake-green), and a goal with zero packets is NOT complete;
- ``progress`` is child-based for a decomposed parent and packet-based for a leaf;
- ``continuation_action`` returns exactly one legal next move (advance the next
  draft child / complete the parent / replan a blocked child / wait), never an
  auto-advance past a blocked step.

Pure + hermetic: builds Goal objects in memory with a deterministic clock; no
store, no repo, no execution.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_goal import Goal, GoalStatus, planning, transitions


def _clock():
    n = {"i": 0}

    def now() -> str:
        n["i"] += 1
        return f"2026-06-22T05:00:{n['i']:02d}+00:00"

    return now


def _ids():
    n = {"i": 0}

    def nid() -> str:
        n["i"] += 1
        return f"goal-child{n['i']:02d}"

    return nid


def _active(title: str, now) -> Goal:
    return transitions.apply(Goal.create(title, now=now), GoalStatus.ACTIVE, now=now)


class DecomposeTest(unittest.TestCase):
    def test_decompose_creates_children_and_plan_evidence(self) -> None:
        now, nid = _clock(), _ids()
        parent = _active("DB 마이그레이션", now)
        steps = [planning.PlanStep("스키마 설계"), planning.PlanStep("마이그레이션", "데이터 이전")]
        parent2, children = planning.decompose(parent, steps, now=now, new_id=nid)

        self.assertEqual(len(children), 2)
        self.assertEqual([c.title for c in children], ["스키마 설계", "마이그레이션"])
        # each child links back to the parent and starts DRAFT (executes nothing)
        self.assertTrue(all(c.parent_id == parent.id for c in children))
        self.assertTrue(all(c.status == GoalStatus.DRAFT for c in children))
        # parent links every child and gets exactly one plan evidence record
        self.assertEqual(parent2.children, tuple(c.id for c in children))
        plan_ev = [e for e in parent2.evidence if e.kind == planning.EV_PLAN]
        self.assertEqual(len(plan_ev), 1)
        self.assertIn("2 step", plan_ev[0].summary)

    def test_decompose_rejects_empty_steps(self) -> None:
        now = _clock()
        parent = _active("goal", now)
        with self.assertRaises(ValueError):
            planning.decompose(parent, [planning.PlanStep("   ")], now=now)


class CompletionAccountingTest(unittest.TestCase):
    def _leaf_with_packet(self, now) -> Goal:
        g = _active("leaf", now)
        g = g.link_packet("packet-aaa", now=now)
        return g.add_evidence(planning.EV_PROPOSAL, "[safe] x -> route", ref="packet-aaa", now=now)

    def test_pending_packet_is_not_complete(self) -> None:
        now = _clock()
        g = self._leaf_with_packet(now)
        t = planning.tally_packets(g)
        self.assertEqual((t.total, t.executed, t.pending, t.blocked), (1, 0, 1, 0))
        self.assertFalse(planning.is_goal_complete(g))

    def test_executed_and_verified_packet_is_complete(self) -> None:
        now = _clock()
        g = self._leaf_with_packet(now)
        g = g.add_evidence(planning.EV_EXECUTION, "applied", ref="packet-aaa", now=now)
        g = g.add_evidence(planning.EV_VERIFICATION, "verified", ref="packet-aaa", now=now)
        t = planning.tally_packets(g)
        self.assertEqual((t.total, t.executed, t.pending, t.blocked), (1, 1, 0, 0))
        self.assertTrue(planning.is_goal_complete(g))

    def test_blocked_packet_is_not_complete(self) -> None:
        now = _clock()
        g = self._leaf_with_packet(now)
        g = g.add_evidence(planning.EV_DECISION, "gate refused", ref="packet-aaa", now=now)
        t = planning.tally_packets(g)
        self.assertEqual((t.blocked, t.pending), (1, 0))
        self.assertFalse(planning.is_goal_complete(g))

    def test_zero_packets_is_not_complete_no_fake_green(self) -> None:
        now = _clock()
        g = _active("empty", now)
        self.assertFalse(planning.is_goal_complete(g))


class ProgressTest(unittest.TestCase):
    def test_parent_progress_is_child_based(self) -> None:
        now, nid = _clock(), _ids()
        parent = _active("plan", now)
        parent, kids = planning.decompose(
            parent, [planning.PlanStep("a"), planning.PlanStep("b")], now=now, new_id=nid)
        done_kid = transitions.apply(
            kids[0].add_evidence("verification", "done", now=now), GoalStatus.ACTIVE, now=now)
        done_kid = transitions.apply(done_kid, GoalStatus.DONE, now=now)
        prog = planning.progress(parent, [done_kid, kids[1]])
        self.assertTrue(prog.decomposed)
        self.assertEqual((prog.total_steps, prog.done_steps), (2, 1))
        self.assertEqual(prog.ratio, 0.5)
        self.assertEqual(prog.next_step_id, kids[1].id)
        self.assertFalse(prog.complete)

    def test_leaf_progress_is_packet_based(self) -> None:
        now = _clock()
        g = _active("leaf", now)
        g = g.link_packet("p1", now=now).add_evidence(planning.EV_PROPOSAL, "x", ref="p1", now=now)
        g = g.link_packet("p2", now=now).add_evidence(planning.EV_PROPOSAL, "y", ref="p2", now=now)
        g = g.add_evidence(planning.EV_EXECUTION, "applied", ref="p1", now=now)
        prog = planning.progress(g)
        self.assertFalse(prog.decomposed)
        self.assertEqual((prog.total_steps, prog.done_steps), (2, 1))
        self.assertEqual(prog.next_step_id, "p2")  # first un-executed packet


class ContinuationTest(unittest.TestCase):
    def _parent_with_children(self, now, nid):
        parent = _active("plan", now)
        return planning.decompose(
            parent, [planning.PlanStep("a"), planning.PlanStep("b")], now=now, new_id=nid)

    def test_leaf_goal_is_noop(self) -> None:
        now = _clock()
        act = planning.continuation_action(_active("leaf", now), [])
        self.assertEqual(act.kind, planning.NOOP)

    def test_first_draft_child_advances(self) -> None:
        now, nid = _clock(), _ids()
        parent, kids = self._parent_with_children(now, nid)
        act = planning.continuation_action(parent, list(kids))
        self.assertEqual(act.kind, planning.ADVANCE)
        self.assertEqual(act.target_id, kids[0].id)  # the FIRST step, in order

    def test_blocked_child_replans_never_advances(self) -> None:
        now, nid = _clock(), _ids()
        parent, kids = self._parent_with_children(now, nid)
        active0 = transitions.apply(kids[0], GoalStatus.ACTIVE, now=now)
        blocked = transitions.apply(active0, GoalStatus.BLOCKED, now=now)
        act = planning.continuation_action(parent, [blocked, kids[1]])
        self.assertEqual(act.kind, planning.REPLAN)
        self.assertEqual(act.target_id, blocked.id)

    def test_active_child_waits(self) -> None:
        now, nid = _clock(), _ids()
        parent, kids = self._parent_with_children(now, nid)
        active = transitions.apply(kids[0], GoalStatus.ACTIVE, now=now)
        act = planning.continuation_action(parent, [active, kids[1]])
        self.assertEqual(act.kind, planning.WAIT)

    def test_all_children_done_completes_parent(self) -> None:
        now, nid = _clock(), _ids()
        parent, kids = self._parent_with_children(now, nid)
        done = []
        for k in kids:
            k = transitions.apply(k, GoalStatus.ACTIVE, now=now)
            k = k.add_evidence("verification", "done", now=now)
            done.append(transitions.apply(k, GoalStatus.DONE, now=now))
        act = planning.continuation_action(parent, done)
        self.assertEqual(act.kind, planning.COMPLETE)
        self.assertEqual(act.target_id, parent.id)

    def test_sequential_second_child_waits_until_first_done(self) -> None:
        now, nid = _clock(), _ids()
        parent, kids = self._parent_with_children(now, nid)
        # first child still draft → cursor is the first child, not the second
        act = planning.continuation_action(parent, list(kids))
        self.assertEqual(act.target_id, kids[0].id)


if __name__ == "__main__":
    unittest.main()
