"""Goal status transition rules (GW1).

The transition matrix is the *only* sanctioned way to change a goal's status.
Two invariants it enforces:

1. **Legal transitions only.** ``draft`` can't jump straight to ``done``; a
   terminal-ish state can only be re-opened to ``active``. Anything not in the
   matrix raises ``InvalidTransition`` — no silent no-ops, no fake progress.
2. **``done`` requires evidence.** A goal cannot be marked ``done`` unless it
   carries at least one evidence record. This is the code-level guarantee behind
   "no fake-green": ForgeKit can't claim a goal complete with nothing to show.

Kept separate from ``models`` so the rule table is a single auditable surface
(see ``docs/forgekit-goal-roadmap.md`` GW1 acceptance).
"""

from __future__ import annotations

from typing import Callable, Dict, FrozenSet

from .models import Goal, GoalStatus, _utcnow

S = GoalStatus

# Allowed target states per source state. Terminal states (done/abandoned) can
# only be re-opened to active — long-term goals do come back.
_ALLOWED: Dict[GoalStatus, FrozenSet[GoalStatus]] = {
    S.DRAFT: frozenset({S.ACTIVE, S.ABANDONED}),
    S.ACTIVE: frozenset({S.BLOCKED, S.AWAITING_APPROVAL, S.DONE, S.ABANDONED}),
    S.BLOCKED: frozenset({S.ACTIVE, S.ABANDONED}),
    S.AWAITING_APPROVAL: frozenset({S.ACTIVE, S.BLOCKED, S.DONE, S.ABANDONED}),
    S.DONE: frozenset({S.ACTIVE}),
    S.ABANDONED: frozenset({S.ACTIVE}),
}


class InvalidTransition(Exception):
    """Raised when a status change is not permitted by the matrix or a guard."""


def allowed_targets(status: GoalStatus) -> FrozenSet[GoalStatus]:
    """The set of states reachable from ``status`` in one step."""

    return _ALLOWED.get(status, frozenset())


def can_transition(src: GoalStatus, dst: GoalStatus) -> bool:
    return dst in allowed_targets(src)


def apply(goal: Goal, dst: GoalStatus, *, now: Callable[[], str] = _utcnow) -> Goal:
    """Return ``goal`` moved to ``dst``, or raise ``InvalidTransition``.

    Same-state is a no-op (returns the goal unchanged) rather than an error, so
    idempotent callers (e.g. a tick that re-asserts ``active``) don't churn.
    """

    if dst == goal.status:
        return goal
    if not can_transition(goal.status, dst):
        raise InvalidTransition(
            f"cannot move goal {goal.id} from {goal.status.value} to {dst.value}; "
            f"allowed: {sorted(t.value for t in allowed_targets(goal.status))}"
        )
    if dst == S.DONE and not goal.evidence:
        raise InvalidTransition(
            f"goal {goal.id} cannot be marked done with no evidence (no fake-green)"
        )
    return goal.with_status(dst, now=now)


__all__ = (
    "InvalidTransition",
    "allowed_targets",
    "can_transition",
    "apply",
)
