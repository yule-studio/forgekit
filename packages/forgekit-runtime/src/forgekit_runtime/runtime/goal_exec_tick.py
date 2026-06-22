"""Goal execution tick (G1) — the always-on serve loop physically ADVANCES active goals.

``execute_bridge.apply_approved_packet`` (GW4-B physical execution) had **zero callers** — the
real safe-class execution of a goal's proposed packets was a declared seam. This driver closes
it: each bounded tick, for every ACTIVE goal, it executes the goal's **next pending safe-class
packet** via ``apply_approved_packet`` (BoundedMutator-gated, internal-chain-authorized, verified,
evidence written to the goal store AND — when a Nexus vault is configured — a vault note).

This is **goal-driven execution continuity** (not host uptime): a long-term goal advances one
safe step per tick, accumulating evidence, *without operator input* — pausing only at genuine
approval boundaries. Honesty rails (nothing here bypasses the gate):

- ``apply_approved_packet`` **self-gates** — safe + chain-authorized → execute; risky / destructive
  / unauthorized → ``OUTCOME_BLOCKED`` (no mutation, decision evidence). This driver only *drives*
  it; it never reclassifies or force-approves.
- **dedupe** — a packet that already has execution/verification evidence is skipped (no re-run).
- **bounded** — at most one packet per goal per tick, capped at ``max_goals`` goals per tick; the
  daemon's kill-switch / cooldown still own the time dimension.
- a goal sitting in ``awaiting_approval`` (risky proposal) is **surfaced, not executed** — the
  operator decides via ``/goal approve`` (gw3 surface). It is reported as ``awaiting``.

``forgekit_goal`` is imported lazily (best-effort) so the runtime stays importable without a goal
store. Pure given (store, mutator) → unit-testable with a tempdir store + fake/real mutator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Mapping, Optional, Tuple

# evidence kinds that mark a packet as already executed (dedupe) vs merely proposed.
_DONE_KINDS = ("execution", "verification")
_PROPOSAL_KIND = "proposal"


@dataclass(frozen=True)
class GoalExecReport:
    """Honest summary of one goal-execution pass across the active goals."""

    executed: int = 0                      # packets physically executed this pass
    blocked: int = 0                       # packets refused by the gate (risky/unauthorized)
    awaiting: int = 0                      # active goals with a risky proposal awaiting operator
    goals_touched: Tuple[str, ...] = ()
    executed_paths: Tuple[str, ...] = ()
    executed_refs: Tuple[str, ...] = ()    # packet ids physically executed

    @property
    def did_work(self) -> bool:
        return self.executed > 0

    def to_dict(self) -> dict:
        return {"executed": self.executed, "blocked": self.blocked, "awaiting": self.awaiting,
                "goals_touched": list(self.goals_touched),
                "executed_paths": list(self.executed_paths),
                "executed_refs": list(self.executed_refs)}


def _pending_packet(goal) -> Optional[str]:
    """The next packet id linked to *goal* that has a proposal record but no execution yet."""

    proposed = {e.ref for e in goal.evidence if e.kind == _PROPOSAL_KIND and e.ref}
    done = {e.ref for e in goal.evidence if e.kind in _DONE_KINDS and e.ref}
    for pid in goal.packets:                # append-only order → oldest pending first
        if pid in proposed and pid not in done:
            return pid
    return None


def execute_active_goals(repo_root, mutator, *, env: Optional[Mapping[str, str]] = None,
                         config: Optional[Mapping] = None, max_goals: int = 3,
                         store=None, execute_fn=None) -> GoalExecReport:
    """Advance each ACTIVE goal by its next pending safe packet (physical, gated). Bounded.

    ``execute_fn`` defaults to ``selfimprove.apply_approved_packet`` (real BoundedMutator +
    git commit); tests may inject a fake with the same ``(goal, mutator, repo_root, packet_id=,
    approver=, env=, config=)`` shape. Returns a :class:`GoalExecReport`; no goal store / goal
    package → empty report (honest)."""

    if mutator is None or not repo_root:
        return GoalExecReport()
    if store is None:
        try:
            from forgekit_goal import GoalStore  # lazy / best-effort
        except Exception:  # noqa: BLE001 - goal package absent → nothing to drive
            return GoalExecReport()
        store = GoalStore(env=env)
    try:
        from forgekit_goal import GoalStatus
    except Exception:  # noqa: BLE001
        GoalStatus = None  # type: ignore
    apply_approved_packet = execute_fn
    if apply_approved_packet is None:
        # the physical execution bridge lives in selfimprove; import lazily so the runtime stays
        # importable standalone (and degrades honestly if the bridge is absent).
        try:
            from ..selfimprove import apply_approved_packet
        except Exception:  # noqa: BLE001 - bridge absent → cannot execute
            return GoalExecReport()

    try:
        goals = store.load_all()
    except Exception:  # noqa: BLE001
        return GoalExecReport()

    executed = blocked = awaiting = 0
    touched: List[str] = []
    paths: List[str] = []
    refs: List[str] = []
    active_word = getattr(GoalStatus, "ACTIVE", None) if GoalStatus else None
    awaiting_word = getattr(GoalStatus, "AWAITING_APPROVAL", None) if GoalStatus else None

    for goal in goals:
        if awaiting_word is not None and goal.status == awaiting_word:
            awaiting += 1                   # risky proposal — operator decides, not us
            continue
        if active_word is not None and goal.status != active_word:
            continue                        # only ACTIVE goals are auto-advanced
        if len(touched) >= max_goals:
            break
        pid = _pending_packet(goal)
        if pid is None:
            continue
        outcome = apply_approved_packet(goal, mutator, str(repo_root), packet_id=pid,
                                        approver="always-on", env=env, config=config)
        touched.append(goal.id)
        if getattr(outcome, "executed", False):
            executed += 1
            refs.append(pid)
            if getattr(outcome, "changed_path", ""):
                paths.append(outcome.changed_path)
        else:
            # apply_approved_packet self-gated it off (risky/unauthorized) — honest, no mutation.
            blocked += 1

    return GoalExecReport(executed=executed, blocked=blocked, awaiting=awaiting,
                          goals_touched=tuple(touched), executed_paths=tuple(paths),
                          executed_refs=tuple(refs))


__all__ = ("GoalExecReport", "execute_active_goals")
