"""Goal-execution tick (G1) — the always-on runtime physically EXECUTES approved goals.

Before this module, ``apply_approved_packet`` existed but had **no caller**, so an
operator-approved ``/goal`` packet never physically ran. This ticker closes that seam:
each ``forgekit runtime serve`` tick loads ACTIVE (operator-approved) goals from the
``GoalStore`` and runs ``apply_approved_packet`` on their linked packets through a
``BoundedMutator(repo_root)`` — the SAME gated apply path the bridge already enforces.

Honest, bounded posture (no fake autonomy — see ``docs/forgekit-goal-roadmap.md`` GW4-B):

- **Only ACTIVE goals.** ``awaiting_approval`` / ``blocked`` / ``draft`` / ``done`` goals
  are skipped (an ACTIVE goal *is* the operator's approval that work may proceed; risky
  work parks a goal at ``awaiting_approval`` and is never auto-executed here).
- **safe-class only — enforced downstream.** ``apply_approved_packet`` runs the full
  3-gate authorization (``run_internal_chain`` + ``can_specialist_execute`` +
  ``authorize_runtime_execution`` + ``validate_execution``). risky / destructive /
  unauthorized packets are *recorded* (decision evidence), never physically executed.
- **Bounded per tick.** At most ``max_goals`` goals and ``max_packets_per_goal`` packets
  are touched each tick so the pass stays bounded (no unbounded scan/churn).
- **Idempotent.** A packet that already has an ``execution`` evidence record on the goal
  is skipped — we never re-run an executed packet on the next tick.
- **Never push, never destructive, never auto-``done``.** Physical mutation is a verified
  ``BoundedMutator`` bounded write + a real ``git -C`` commit (NO push); rollback on fail.
  This module never advances a goal to ``done`` (that stays a separate gated decision).

Owner: ``packages/forgekit-runtime/runtime`` (caller wiring) + ``selfimprove`` (the gated
apply path it reuses). It re-implements no gate; it only schedules + persists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .daemon import TickOutcome


@dataclass
class GoalExecTicker:
    """Builds a daemon tick that physically executes approved safe-class goal packets (G1).

    Bounded: at most ``max_goals`` ACTIVE goals and ``max_packets_per_goal`` linked
    packets are attempted per tick. Idempotent: a packet with an existing ``execution``
    evidence record is not re-run. Everything risky/unauthorized is gated downstream by
    ``apply_approved_packet`` (recorded, not executed)."""

    repo_root: Path
    env: Optional[dict] = None
    mutator: Optional[object] = None        # BoundedMutator (injectable for tests)
    store: Optional[object] = None          # GoalStore (injectable for tests)
    max_goals: int = 2                      # bound goals touched per tick
    max_packets_per_goal: int = 2           # bound packets touched per goal per tick
    approver: str = "operator"

    def __post_init__(self) -> None:
        self.repo_root = Path(self.repo_root)

    # --- store / mutator (lazy, injectable) --------------------------------
    def _get_store(self):
        if self.store is not None:
            return self.store
        from forgekit_goal import GoalStore

        self.store = GoalStore(env=self.env)
        return self.store

    def _get_mutator(self):
        if self.mutator is not None:
            return self.mutator
        from ..autopilot.runner import BoundedMutator

        self.mutator = BoundedMutator(self.repo_root)
        return self.mutator

    @staticmethod
    def _attempted_packet_ids(goal) -> set:
        """Packet ids already attempted — durable idempotency key (survives restarts).

        A packet is "attempted" once it has either an ``execution`` evidence record (a
        verified physical run) OR a ``decision`` refusal record (gate-blocked: risky /
        destructive / unauthorized). Both are written with ``ref=<pid>`` by
        ``apply_approved_packet``. Skipping both means a tick never re-executes an
        executed packet AND never re-records the same refusal every tick (bounded — no
        decision-evidence churn). Re-attempting a risky packet is an operator action
        (re-link / re-approve), not an automatic per-tick retry."""

        return {e.ref for e in goal.evidence
                if e.kind in ("execution", "decision") and e.ref}

    @staticmethod
    def _proposed_packet_ids(goal) -> List[str]:
        """Linked packets that have a ``proposal`` evidence record (executable candidates).

        The goal-tick records a ``proposal`` (``ref=<pid>``) per packet; only those are
        resolvable into a runnable packet by the execute bridge. Returned in link order."""

        proposals = {e.ref for e in goal.evidence if e.kind == "proposal" and e.ref}
        return [pid for pid in goal.packets if pid in proposals]

    # --- one tick ----------------------------------------------------------
    def tick(self, n: int) -> TickOutcome:
        from forgekit_goal import GoalStatus
        from ..selfimprove import execute_bridge as EB
        from . import goal_governance as gov

        store = self._get_store()
        try:
            goals = store.load_all()
        except Exception:  # noqa: BLE001 — a store read must never crash the loop
            return TickOutcome(summary=f"tick {n}: goal-exec (store 읽기 실패)", waiting=False)

        by_id = {g.id: g for g in goals}
        # Only ACTIVE goals are operator-approved-to-proceed. Bound how many we touch.
        active = [g for g in goals if g.status == GoalStatus.ACTIVE][: self.max_goals]

        executed = 0
        blocked = 0
        skipped_done = 0
        gov_blocked = 0
        executed_paths: List[str] = []
        touched_goals = 0

        if not active:
            return TickOutcome(
                summary=f"tick {n}: goal-exec — ACTIVE goal 없음 (실행 대상 0)",
                waiting=False)

        mutator = self._get_mutator()
        for goal in active:
            # GOVERNANCE GATE — 설계 없는 구현 금지. A governance-required goal (or a child of a
            # governance-required parent) may NOT run its packets until its design chain
            # (PM brief → meeting → signed tech-lead decision(스택 ≥2) → handoff) is executable.
            # We record the refusal ONCE (idempotent) and skip — never a physical run.
            allowed, stage, reason = gov.design_gate(goal, by_id, env=self.env)
            if not allowed:
                goal = self._record_governance_block(store, goal, stage, reason)
                gov_blocked += 1
                continue
            already = self._attempted_packet_ids(goal)
            candidates = self._proposed_packet_ids(goal)
            # idempotency: never re-attempt a packet already executed or gate-refused.
            pending = [pid for pid in candidates if pid not in already]
            if not pending:
                skipped_done += 1
                continue
            touched_goals += 1
            g = goal
            for pid in pending[: self.max_packets_per_goal]:
                # persist=True → apply writes execution/verification (or refusal) evidence
                # to the store itself. We reload between packets so the next packet sees the
                # freshly persisted execution evidence (keeps the per-goal chain coherent and
                # makes the idempotency marker durable across ticks/restarts).
                outcome = EB.apply_approved_packet(
                    g, mutator, str(self.repo_root), packet_id=pid,
                    approver=self.approver, env=self.env, persist=True)
                g = self._reload_goal(store, goal.id, fallback=g)
                if outcome.executed and outcome.applied:
                    executed += 1
                    if outcome.changed_path:
                        executed_paths.append(outcome.changed_path)
                elif outcome.outcome == EB.OUTCOME_BLOCKED:
                    blocked += 1
                # ERROR / AWAITING → counted as neither executed nor blocked (recorded only)

        waiting = blocked > 0 or gov_blocked > 0
        bits = [f"goal-exec {executed} 실행"]
        if blocked:
            bits.append(f"{blocked} 게이트차단")
        if gov_blocked:
            bits.append(f"{gov_blocked} 설계미완차단")
        if skipped_done:
            bits.append(f"{skipped_done} 완료-skip")
        summary = f"tick {n}: " + " / ".join(bits)
        if executed_paths:
            summary += " · " + ", ".join(executed_paths[:2])
        return TickOutcome(
            summary=summary, waiting=waiting, blocked_count=blocked,
            executed=executed, executed_paths=tuple(executed_paths))

    def _record_governance_block(self, store, goal, stage: str, reason: str):
        """Record a single, idempotent governance refusal on the goal (no physical run).

        Keyed by ``ref='governance:<stage>'`` so a goal stuck at the same stage does not
        churn a new record every tick (bounded). When the design chain advances to a new
        stage, a fresh honest record is written. Never executes anything."""

        from . import goal_governance as gov

        ref = f"governance:{stage}"
        if any(e.kind == "decision" and e.ref == ref for e in goal.evidence):
            return goal
        g = goal.add_evidence(
            "decision", f"설계 미완 — specialist 실행 차단 (stage={stage}): {reason}", ref=ref)
        try:
            store.save(g)
        except Exception:  # noqa: BLE001 — a record must never crash the loop
            return goal
        return g

    @staticmethod
    def _reload_goal(store, goal_id: str, fallback):
        """Reload the goal the apply path just persisted (apply uses persist=True here).

        ``apply_approved_packet`` saves the post-apply goal (with execution/verification
        or refusal evidence) to its env-resolved ``GoalStore``. We re-read it so the next
        packet — and the next tick — sees the freshly written evidence (the durable
        idempotency marker). If the read returns nothing, we keep the in-memory goal."""

        try:
            got = store.get(goal_id)
            return got if got is not None else fallback
        except Exception:  # noqa: BLE001
            return fallback

    def tick_fn(self):
        """Return a ``tick_fn(n) -> TickOutcome`` bound to this ticker (for the daemon)."""

        return self.tick


__all__ = ("GoalExecTicker",)
