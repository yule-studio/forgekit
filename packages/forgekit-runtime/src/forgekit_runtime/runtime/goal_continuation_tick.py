"""Goal-continuation tick (GW-EXEC) — the always-on runtime CLOSES the goal loop.

The goal-exec tick (``goal_exec_tick.GoalExecTicker``) physically runs a goal's
approved safe-class packets and writes ``execution``/``verification`` evidence.
But before this module nothing ever *advanced the plan*: a decomposed parent's
child steps were never sequenced, a child whose packets were all verified was
never marked ``done``, and a parent whose every step finished was never closed.
The loop had teeth (it could execute) but no continuation (it never finished).

This ticker is that continuation. Each ``forgekit runtime serve`` tick it:

1. **rolls up finished children** — an ACTIVE child goal whose every linked
   packet carries verified ``execution`` evidence (``planning.is_goal_complete``)
   is advanced ``active -> done`` with a ``verification`` roll-up evidence record;
2. **advances the plan** — for each decomposed parent, it asks
   ``planning.continuation_action`` for the single next legal move and applies it:
   - ``ADVANCE`` → activate the next DRAFT child (``draft -> active``) so the
     exec tick can run *its* packets next tick;
   - ``COMPLETE`` → every child done → close the parent ``active -> done`` with a
     roll-up evidence record;
3. **replans stuck goals** — a stuck ACTIVE leaf goal (gate-blocked packet, nothing
   pending) gets ``planning.replan``: a bounded retry abandons (unlinks) the dead
   packet + records a ``replan`` attempt so the scheduler can re-drive it for an
   alternative; once retries are exhausted it escalates ``active -> awaiting_approval``
   and persists the ``blocked`` reason. It NEVER re-runs the gate-refused packet.

Honest, bounded posture (no fake autonomy — see ``docs/forgekit-goal-roadmap.md``
GW-EXEC):

- **Executes nothing itself.** It only sequences goal *status* + writes evidence.
  Physical mutation stays in the gated exec tick / bridge — this never touches a
  file or a packet. Decomposition (parent → children) is operator/lane work via
  the ``/goal plan`` surface; this tick only drives an already-decomposed plan.
- **Evidence-gated ``done``.** A goal is closed only when ``is_goal_complete`` is
  true (every packet executed + at least one verification record) — the same
  "no fake-green" rule ``transitions`` enforces. A child with a *blocked* packet
  is never completed (it surfaces as ``REPLAN`` for the operator), so the
  approval chain is never bypassed.
- **Never auto-decomposes, never activates a draft *parent*.** Only DRAFT
  *children* of an ACTIVE parent are advanced (the parent being ACTIVE is the
  operator's approval that the plan may proceed). A draft/blocked parent is left
  for the operator.
- **Bounded + idempotent.** At most ``max_goals`` parents are advanced per tick;
  re-running over an already-done child/parent is a no-op (``transitions.apply``
  treats same-state as a no-op, and a completed goal is skipped).

Owner: ``packages/forgekit-runtime/runtime``. It reuses ``forgekit_goal.planning``
(pure rules) + ``GoalStore`` (persistence); it re-implements no rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .daemon import TickOutcome


@dataclass
class GoalContinuationTicker:
    """Builds a daemon tick that sequences decomposed goal plans to completion.

    Bounded: at most ``max_goals`` decomposed parents are advanced per tick.
    Idempotent: completing an already-done child/parent is a no-op. Executes
    nothing — it only advances goal *status* and writes roll-up evidence,
    evidence-gated by ``planning.is_goal_complete``."""

    repo_root: Path
    env: Optional[dict] = None
    store: Optional[object] = None     # GoalStore (injectable for tests)
    max_goals: int = 4                 # bound parents advanced per tick
    max_replan_attempts: int = 1       # bounded auto-retry before escalating to operator

    def __post_init__(self) -> None:
        self.repo_root = Path(self.repo_root)

    def _get_store(self):
        if self.store is not None:
            return self.store
        from forgekit_goal import GoalStore

        self.store = GoalStore(env=self.env)
        return self.store

    # --- one tick ----------------------------------------------------------
    def tick(self, n: int) -> TickOutcome:
        from forgekit_goal import GoalStatus, planning, transitions

        store = self._get_store()
        try:
            goals = store.load_all()
        except Exception:  # noqa: BLE001 — a store read must never crash the loop
            return TickOutcome(summary=f"tick {n}: goal-continuation (store 읽기 실패)", waiting=False)

        by_id: Dict[str, object] = {g.id: g for g in goals}

        completed_children = 0
        advanced = 0
        completed_parents = 0
        replans = 0
        retries = 0
        escalations = 0
        waits = 0

        # 1) Roll up finished ACTIVE children to done (evidence-gated). Done first so a
        #    parent whose last child just finished can COMPLETE in the same tick.
        for g in goals:
            if g.status != GoalStatus.ACTIVE or g.children:
                continue  # only leaf children, only active ones
            if not planning.is_goal_complete(g):
                continue
            t = planning.tally_packets(g)
            g2 = g.add_evidence(
                "verification",
                f"plan step complete — {t.executed}/{t.total} packet verified",
            )
            try:
                g2 = transitions.apply(g2, GoalStatus.DONE)
            except transitions.InvalidTransition:
                continue  # never force an illegal move
            store.save(g2)
            by_id[g2.id] = g2
            completed_children += 1

        # 1.5) REPLAN stuck ACTIVE leaf goals (standalone or child). A stuck goal has a
        #      gate-blocked packet and nothing pending — the exec tick won't retry it, so
        #      we either abandon the dead packet for one bounded retry (so the scheduler
        #      re-drives it for an alternative) or escalate to the operator with the reason
        #      persisted. Never re-runs the gate-refused packet (no fake progress).
        for g in goals:
            g = by_id.get(g.id, g)
            if g.status != GoalStatus.ACTIVE or g.children:
                continue
            if not planning.is_stuck(g):
                continue
            d = planning.replan(g, max_attempts=self.max_replan_attempts)
            if d.action == planning.REPLAN_RETRY:
                g2 = g
                for pid in d.unlink:
                    g2 = g2.unlink_packet(pid)
                g2 = g2.add_evidence(planning.EV_REPLAN, d.reason)
                store.save(g2)
                by_id[g2.id] = g2
                retries += 1
            elif d.action == planning.REPLAN_ESCALATE:
                g2 = g.add_evidence(planning.EV_BLOCKED, d.reason)
                try:
                    g2 = transitions.apply(g2, GoalStatus.AWAITING_APPROVAL)
                except transitions.InvalidTransition:
                    continue
                store.save(g2)
                by_id[g2.id] = g2
                escalations += 1

        # 2) Advance decomposed parents: activate next draft child, or close the parent.
        parents = [g for g in goals if g.children and g.status == GoalStatus.ACTIVE]
        for parent in parents[: self.max_goals]:
            parent = by_id.get(parent.id, parent)  # pick up the child roll-up just saved
            children = [by_id[c] for c in parent.children if c in by_id]
            action = planning.continuation_action(parent, children)

            if action.kind == planning.ADVANCE and action.target_id in by_id:
                child = by_id[action.target_id]
                if child.status == GoalStatus.DRAFT:
                    try:
                        c2 = transitions.apply(child, GoalStatus.ACTIVE)
                    except transitions.InvalidTransition:
                        continue
                    store.save(c2)
                    by_id[c2.id] = c2
                    advanced += 1
            elif action.kind == planning.COMPLETE:
                prog = planning.progress(parent, children)
                p2 = parent.add_evidence(
                    "verification",
                    f"all {prog.total_steps} plan step done — goal complete",
                )
                try:
                    p2 = transitions.apply(p2, GoalStatus.DONE)
                except transitions.InvalidTransition:
                    continue
                store.save(p2)
                by_id[p2.id] = p2
                completed_parents += 1
            elif action.kind == planning.REPLAN:
                replans += 1
            elif action.kind == planning.WAIT:
                waits += 1

        moved = completed_children + advanced + completed_parents + retries + escalations
        if moved == 0 and replans == 0 and waits == 0:
            return TickOutcome(
                summary=f"tick {n}: goal-continuation — 진행할 plan 없음", waiting=False)

        bits: List[str] = []
        if advanced:
            bits.append(f"{advanced} step 진행")
        if completed_children:
            bits.append(f"{completed_children} step 완료")
        if completed_parents:
            bits.append(f"{completed_parents} goal 완료")
        if retries:
            bits.append(f"{retries} replan 재시도")
        if escalations:
            bits.append(f"{escalations} 막힘→승인대기")
        if replans:
            bits.append(f"{replans} replan 필요")
        if waits:
            bits.append(f"{waits} 대기")
        summary = f"tick {n}: goal-continuation " + " / ".join(bits)
        # escalation / replan needs operator attention — surface it as a wait condition.
        waiting = (escalations + replans) > 0
        return TickOutcome(summary=summary, waiting=waiting,
                           blocked_count=escalations + replans)

    def tick_fn(self):
        """Return a ``tick_fn(n) -> TickOutcome`` bound to this ticker (for the daemon)."""

        return self.tick


__all__ = ("GoalContinuationTicker",)
