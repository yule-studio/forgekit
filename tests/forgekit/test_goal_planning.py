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


class ApprovalDispositionTest(unittest.TestCase):
    def _leaf(self, now, risk: str, pid: str = "p1") -> Goal:
        g = _active("leaf", now).link_packet(pid, now=now)
        return g.add_evidence(planning.EV_PROPOSAL, f"[{risk}] x -> route", ref=pid, now=now)

    def test_safe_pending_is_autonomous_safe(self) -> None:
        now = _clock()
        self.assertEqual(planning.approval_disposition(self._leaf(now, "safe")),
                         planning.AUTONOMOUS_SAFE)

    def test_risky_pending_needs_approval(self) -> None:
        now = _clock()
        self.assertEqual(planning.approval_disposition(self._leaf(now, "risky")),
                         planning.NEEDS_APPROVAL)

    def test_blocked_pending_needs_approval(self) -> None:
        now = _clock()
        self.assertEqual(planning.approval_disposition(self._leaf(now, "blocked")),
                         planning.NEEDS_APPROVAL)

    def test_no_pending_is_none(self) -> None:
        now = _clock()
        g = self._leaf(now, "safe")
        g = g.add_evidence(planning.EV_EXECUTION, "applied", ref="p1", now=now)
        self.assertEqual(planning.approval_disposition(g), planning.DISPO_NONE)

    def test_unknown_risk_tag_treated_as_needs_approval(self) -> None:
        # safe-by-rejection: an unreadable risk tag is never auto-run
        now = _clock()
        g = _active("leaf", now).link_packet("p1", now=now)
        g = g.add_evidence(planning.EV_PROPOSAL, "no tag here", ref="p1", now=now)
        self.assertEqual(planning.approval_disposition(g), planning.NEEDS_APPROVAL)


class ReplanPolicyTest(unittest.TestCase):
    def _stuck_leaf(self, now, pid: str = "p1") -> Goal:
        """An ACTIVE leaf whose only packet was gate-refused (stuck)."""
        g = _active("leaf", now).link_packet(pid, now=now)
        g = g.add_evidence(planning.EV_PROPOSAL, f"[safe] x -> route", ref=pid, now=now)
        return g.add_evidence(planning.EV_DECISION, "gate refused: scope creep", ref=pid, now=now)

    def test_not_stuck_returns_none(self) -> None:
        now = _clock()
        g = _active("leaf", now).link_packet("p1", now=now)
        g = g.add_evidence(planning.EV_PROPOSAL, "[safe] x", ref="p1", now=now)  # pending, not stuck
        self.assertFalse(planning.is_stuck(g))
        self.assertEqual(planning.replan(g).action, planning.REPLAN_NONE)

    def test_first_stuck_retries_and_unlinks_dead_packet(self) -> None:
        now = _clock()
        g = self._stuck_leaf(now)
        self.assertTrue(planning.is_stuck(g))
        d = planning.replan(g, max_attempts=1)
        self.assertEqual(d.action, planning.REPLAN_RETRY)
        self.assertEqual(d.unlink, ("p1",))
        self.assertIn("scope creep", d.reason)  # persisted blocked reason carried

    def test_unlinking_dead_packet_clears_stuck(self) -> None:
        # applying RETRY (unlink) makes the goal no longer stuck (tally respects packets)
        now = _clock()
        g = self._stuck_leaf(now)
        g2 = g.unlink_packet("p1", now=now)
        self.assertFalse(planning.is_stuck(g2))
        self.assertEqual(planning.tally_packets(g2).total, 0)  # dead packet dropped

    def test_attempts_exhausted_escalates(self) -> None:
        now = _clock()
        g = self._stuck_leaf(now)
        g = g.add_evidence(planning.EV_REPLAN, "retry 1/1", now=now)  # one prior attempt
        d = planning.replan(g, max_attempts=1)
        self.assertEqual(d.action, planning.REPLAN_ESCALATE)
        self.assertIn("escalate", d.reason)

    def test_blocked_reason_is_latest_refusal(self) -> None:
        now = _clock()
        g = self._stuck_leaf(now)
        self.assertEqual(planning.blocked_reason(g), "gate refused: scope creep")
        g2 = _active("clean", now)
        self.assertIsNone(planning.blocked_reason(g2))


class DerivePlanStepsTest(unittest.TestCase):
    def test_groups_by_area_first_seen_order(self) -> None:
        items = [("docs", "fix readme"), ("tests", "add case"), ("docs", "typo")]
        steps = planning.derive_plan_steps(items)
        self.assertEqual([s.title for s in steps], ["docs", "tests"])  # first-seen order
        self.assertIn("2 finding", steps[0].intent)  # docs had 2

    def test_blank_area_bucketed_general(self) -> None:
        steps = planning.derive_plan_steps([("", "x")])
        self.assertEqual(steps[0].title, "general")

    def test_is_big_goal_threshold(self) -> None:
        self.assertTrue(planning.is_big_goal([("docs", "a"), ("tests", "b")]))
        self.assertFalse(planning.is_big_goal([("docs", "a"), ("docs", "b")]))  # one area
        self.assertFalse(planning.is_big_goal([]))


if __name__ == "__main__":
    unittest.main()
