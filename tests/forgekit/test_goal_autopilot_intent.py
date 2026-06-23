"""Goal autopilot exec core — an intent goal seeds its FIRST packet (not stuck at 0).

Before this, the scheduler packetized ONLY from repo self-improvement discovery, so an
ACTIVE goal whose work isn't a repo gap ("build feature X") discovered nothing and stuck
at ``packets: 0 / evidence: 0``. This proves the intent seed closes that — honestly:

- an ACTIVE intent goal (empty discovery) gets a real first packet linked + ``proposal``
  evidence, derived from the goal's own title via the decision-lane next step;
- that seed is risky (PM brief / design decision needed) → the goal parks at
  ``awaiting_approval`` (operator/PM input), never auto-executed (no fake exec);
- ``goal show`` / ``awaiting`` / ``evidence`` stay consistent (the same store);
- the existing safe-discovery path is unchanged (seed only fires when discovery is empty);
- idempotent: a second tick does not re-seed.

Hermetic: ``$FORGEKIT_HOME`` tempdir; discovery is INJECTED so the loop is deterministic.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_goal import Goal, GoalStatus, GoalStore, transitions
from forgekit_runtime.runtime.goal_intent import intent_packets
from forgekit_runtime.runtime.goal_scheduler_tick import GoalSchedulerTicker
from forgekit_runtime.selfimprove import packet as P
from forgekit_runtime.selfimprove.loop import SelfImprovementResult


def _discover(*packets):
    def d(_repo_root):
        return SelfImprovementResult(packets=list(packets))
    return d


class IntentPacketTests(unittest.TestCase):
    def test_empty_title_no_packet(self) -> None:
        self.assertEqual(intent_packets("  "), [])

    def test_intent_packet_is_risky_planning_step(self) -> None:
        pkts = intent_packets("결제 연동 기능 설계·구현")
        self.assertEqual(len(pkts), 1)
        p = pkts[0]
        self.assertEqual(p.risk, P.RISK_RISKY)          # design decision → approval-wait
        self.assertTrue(p.approval_needed)
        self.assertEqual(p.source_origin, "goal-intent")
        self.assertEqual(p.recommended_owner, "product-manager")
        self.assertIn("PM brief", p.finding)
        self.assertIn("결제", p.finding)                 # carries the goal's own intent


class SchedulerIntentSeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.env = {"FORGEKIT_HOME": self._home.name}
        self.store = GoalStore(env=self.env)

    def _active(self, title) -> Goal:
        g = transitions.apply(Goal.create(title, mode="auto"), GoalStatus.ACTIVE)
        self.store.save(g)
        return g

    def _sched(self, *packets) -> GoalSchedulerTicker:
        # repo_root is irrelevant when discovery is injected
        return GoalSchedulerTicker(repo_root=Path(self._home.name), env=self.env,
                                   store=self.store, discover=_discover(*packets))

    def test_intent_goal_no_longer_stuck_at_zero(self) -> None:
        goal = self._active("외부 결제 연동 기능을 설계하고 구현한다")
        before = self.store.get(goal.id)
        self.assertEqual((len(before.packets), len(before.evidence)), (0, 0))  # the stuck case

        out = self._sched().tick(1)  # EMPTY discovery → intent seed fires

        after = self.store.get(goal.id)
        self.assertTrue(after.packets, "intent goal still stuck at packets:0")
        self.assertIn("proposal", [e.kind for e in after.evidence])  # real evidence record
        self.assertEqual(after.status, GoalStatus.AWAITING_APPROVAL)  # operator input needed
        self.assertTrue(out.waiting)

    def test_evidence_ref_links_the_packet(self) -> None:
        goal = self._active("기능 X 구현")
        self._sched().tick(1)
        g = self.store.get(goal.id)
        proposal = next(e for e in g.evidence if e.kind == "proposal")
        self.assertIn(proposal.ref, g.packets)  # show / evidence / packets stay consistent

    def test_safe_discovery_path_unchanged(self) -> None:
        # when discovery DOES find a safe gap, the intent seed must NOT fire (stays active)
        goal = self._active("self-manage")
        self._sched(P.make_packet("콘솔 도움말 문구 개선", area="docs")).tick(1)
        g = self.store.get(goal.id)
        self.assertEqual(g.status, GoalStatus.ACTIVE)  # safe → stays active (no risky seed)
        self.assertTrue(g.packets)
        # the linked packet is the discovered docs one, not the goal-intent seed
        proposal = next(e for e in g.evidence if e.kind == "proposal")
        self.assertIn("도움말", proposal.summary)

    def test_idempotent_no_reseed(self) -> None:
        goal = self._active("기능 Y 구현")
        self._sched().tick(1)
        g1 = self.store.get(goal.id)
        n_ev = len(g1.evidence)
        # goal is now awaiting_approval → scheduler only looks at ACTIVE; even if re-activated,
        # it already has proposal evidence so it is not re-seeded.
        reactivated = transitions.apply(g1, GoalStatus.ACTIVE)
        self.store.save(reactivated)
        self._sched().tick(2)
        g2 = self.store.get(goal.id)
        self.assertEqual(len(g2.evidence), n_ev)  # no new proposal (idempotent)


if __name__ == "__main__":
    unittest.main()
