"""Goal-scheduler tick (AUTONOMY) — the front of the always-on goal loop.

Before this module the always-on serve loop could *execute* an ACTIVE goal's
already-linked packets (goal-exec tick) and *sequence* an already-decomposed plan
(continuation tick) — but nothing ever **collected** work for a goal or **decided
its shape**. An operator-activated goal with no packets just sat there: the
discovery pass (``selfimprove.goal_tick``) was never wired into serve. This ticker
is that missing front stage. Each ``forgekit runtime serve`` tick it, for ACTIVE
leaf goals that need work:

1. **collect / packetize** — runs the bounded self-improvement discovery
   (``run_self_improvement``) and links the discovered packets to the goal with a
   ``proposal`` evidence each (``goal_tick.link_packets``). This is the loop's
   "collect state → choose packets" stage. If discovery finds nothing (a
   feature/intent goal, not a repo gap), it **seeds the first decision-lane step from
   the goal's own intent** (``goal_intent.intent_packets``) so the goal never sticks at
   ``packets: 0`` — that seed is risky (PM brief / design decision needed) so it parks
   the goal at ``awaiting_approval`` rather than auto-running (honest, not fake-exec).
2. **autonomously decompose if big** — if the discovered work spans ≥2 distinct
   affected areas (``planning.is_big_goal``), the goal is decomposed into one
   **child goal per area** (``planning.decompose``), and each area's packets are
   routed to its child. A big goal is *forced* down into packetized, per-child
   execution rather than run as one blob. Single-area/trivial work stays a leaf.
3. **approval split** — ``link_packets`` parks a goal (or child) at
   ``awaiting_approval`` the moment any risky/blocked-class packet appears, so
   approval-needed work never auto-runs; safe-only work stays ACTIVE for the
   exec tick. This is the autonomous-safe vs approval-needed separation.

Honest, bounded posture (no fake autonomy — see ``docs/forgekit-goal-roadmap.md``
AUTONOMY):

- **Executes nothing.** Discovery + linking + decomposition create *records*
  (proposal/plan evidence, child goals). Physical mutation stays in the gated
  exec tick / bridge — this never touches a file or runs a packet.
- **Bounded + idempotent.** At most ``max_goals`` goals are packetized per tick.
  A goal that already has proposal evidence or children is skipped — discovery
  runs once per goal (no per-tick re-proposal churn). The exception is a
  *replanned* goal (a ``replan`` retry abandoned its dead packet, leaving no
  pending work): it is re-driven once so an alternative can be discovered.
- **Never activates a draft goal, never marks done.** Only ACTIVE goals (the
  operator's go-ahead) are packetized; closing a goal is the continuation tick's
  evidence-gated job.

Owner: ``packages/forgekit-runtime/runtime``. Reuses ``selfimprove.goal_tick``
(discovery + linking) + ``forgekit_goal.planning`` (decompose rules) + ``GoalStore``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .daemon import TickOutcome


@dataclass
class GoalSchedulerTicker:
    """Builds a daemon tick that collects work for ACTIVE goals + decomposes big ones.

    Bounded: at most ``max_goals`` goals packetized per tick. Idempotent: a goal
    with proposal evidence or children is skipped (discovery runs once), except a
    replanned goal with no pending work, which is re-driven once. Executes nothing —
    it only creates proposal/plan records and child goals."""

    repo_root: Path
    env: Optional[dict] = None
    store: Optional[object] = None     # GoalStore (injectable for tests)
    discover: Optional[object] = None  # discover(repo_root)->SelfImprovementResult (injectable)
    max_goals: int = 2                 # bound goals packetized per tick
    big_area_threshold: int = 2        # ≥ this many distinct areas → decompose

    def __post_init__(self) -> None:
        self.repo_root = Path(self.repo_root)

    def _discover(self):
        """Run the bounded self-improvement discovery (injectable for hermetic tests)."""

        if self.discover is not None:
            return self.discover(str(self.repo_root))
        from ..selfimprove.loop import run_self_improvement

        return run_self_improvement(str(self.repo_root))

    def _get_store(self):
        if self.store is not None:
            return self.store
        from forgekit_goal import GoalStore

        self.store = GoalStore(env=self.env)
        return self.store

    @staticmethod
    def _needs_packetizing(goal, planning) -> bool:
        """An ACTIVE leaf goal that has no runnable work yet (or was just replanned).

        True when: (a) the goal has never been packetized (no proposal evidence and
        no children), OR (b) a replan abandoned its dead packet so it now has a
        ``replan`` record but no pending/blocked packet — re-drive it once to find an
        alternative. False for decomposed parents (children are scheduled on their own)
        and for goals already carrying pending work."""

        if goal.children:
            return False
        proposals = [e for e in goal.evidence if e.kind == planning.EV_PROPOSAL]
        if not proposals:
            return True  # never packetized
        t = planning.tally_packets(goal)
        replanned = any(e.kind == planning.EV_REPLAN for e in goal.evidence)
        return replanned and t.pending == 0 and t.blocked == 0 and not planning.is_goal_complete(goal)

    def tick(self, n: int) -> TickOutcome:
        from forgekit_goal import GoalStatus, planning
        from ..selfimprove import goal_tick

        store = self._get_store()
        try:
            goals = store.load_all()
        except Exception:  # noqa: BLE001 — a store read must never crash the loop
            return TickOutcome(summary=f"tick {n}: goal-scheduler (store 읽기 실패)", waiting=False)

        candidates = [g for g in goals
                      if g.status == GoalStatus.ACTIVE and self._needs_packetizing(g, planning)]
        if not candidates:
            return TickOutcome(
                summary=f"tick {n}: goal-scheduler — 수집 대상 goal 없음", waiting=False)

        packetized = 0
        decomposed = 0
        awaiting = 0
        for goal in candidates[: self.max_goals]:
            si = self._discover()
            if not si.packets:
                # No repo-discoverable gap for this goal (e.g. a feature/intent goal). Seed
                # the FIRST decision-lane step from the goal's own intent so it never sticks
                # at packets:0 (autopilot exec core). The seed is risky → awaiting_approval
                # (operator/PM design input needed) — honest, not a fake executable packet.
                from .goal_intent import intent_packets
                from ..selfimprove.loop import SelfImprovementResult

                seeded = intent_packets(goal.title)
                if not seeded:
                    continue
                si = SelfImprovementResult(packets=seeded)
            items = [(p.affected_area, p.finding) for p in si.packets]

            if planning.is_big_goal(items) and len(
                {(a or "").strip() or "general" for a, _ in items}
            ) >= self.big_area_threshold:
                g2, made = self._decompose_and_route(goal, si, planning, goal_tick)
                store.save(g2)
                for child in made:
                    store.save(child)
                    if child.status == GoalStatus.AWAITING_APPROVAL:
                        awaiting += 1
                decomposed += 1
            else:
                g2, _routes, waiting = goal_tick.link_packets(goal, si.packets)
                store.save(g2)
                packetized += 1
                if waiting > 0:
                    awaiting += 1

        if packetized == 0 and decomposed == 0:
            return TickOutcome(
                summary=f"tick {n}: goal-scheduler — 신규 packet 없음", waiting=False)

        bits: List[str] = []
        if packetized:
            bits.append(f"{packetized} goal packetize")
        if decomposed:
            bits.append(f"{decomposed} goal 자동분해")
        if awaiting:
            bits.append(f"{awaiting} 승인대기")
        summary = f"tick {n}: goal-scheduler " + " / ".join(bits)
        # awaiting_approval is operator-actionable → surface as a wait condition.
        return TickOutcome(summary=summary, waiting=awaiting > 0)

    def _decompose_and_route(self, goal, si, planning, goal_tick):
        """Decompose a big goal into one child per area + route each area's packets.

        Returns ``(parent', children)``. The parent becomes a pure plan node (children
        + ``plan`` evidence, no packets of its own); each child carries its area's
        packets via ``link_packets`` (so a risky child parks at ``awaiting_approval``
        independently). Executes nothing."""

        steps = planning.derive_plan_steps([(p.affected_area, p.finding) for p in si.packets])
        parent2, children = planning.decompose(goal, steps)

        # route each area's packets to the matching child (steps and children share order)
        routed = []
        for step, child in zip(steps, children):
            area_key = step.title
            area_packets = [p for p in si.packets
                            if ((p.affected_area or "").strip() or "general") == area_key]
            c2, _routes, _waiting = goal_tick.link_packets(child, area_packets)
            routed.append(c2)
        return parent2, routed

    def tick_fn(self):
        """Return a ``tick_fn(n) -> TickOutcome`` bound to this ticker (for the daemon)."""

        return self.tick


__all__ = ("GoalSchedulerTicker",)
