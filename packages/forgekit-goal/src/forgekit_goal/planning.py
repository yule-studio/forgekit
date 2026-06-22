"""Goal planning (GW-EXEC) — decomposition + progress + continuation rules.

This is the *execution-core spine* of a long-term goal: it turns a big operator
goal into an ordered plan of **child goals** (decomposition), measures how far
that plan has actually advanced from real evidence (progress), and decides the
single next legal move (continuation). It is the pure brain the runtime
continuation tick drives.

Honest boundaries kept here (mirrors ``transitions`` / ``models``):

- **Executes nothing.** Decomposition only *creates plan records* (child goals +
  a ``plan`` evidence entry on the parent). It never runs a packet, never writes
  a file. Physical execution stays behind the runtime's gated apply path. Making
  a plan is safe-class by construction.
- **Evidence-derived, not asserted.** ``progress`` / ``is_goal_complete`` read
  the goal's own append-only evidence (``execution`` / ``verification`` /
  ``decision`` records that the gated bridge writes). A goal is "complete" only
  when every linked packet carries real execution evidence and none is pending
  or gate-blocked — the same "no fake-green" rule ``transitions`` enforces for
  ``done``, but expressed as a derived predicate the continuation loop can act on.
- **One legal move at a time.** ``continuation_action`` returns the *next* action
  (advance / complete / replan / wait / noop) but applies nothing — the caller
  routes it through ``transitions.apply`` so the matrix and the done-requires-
  evidence guard always hold. Risky/blocked work surfaces as ``REPLAN``/``WAIT``
  (operator), never an auto-advance.

Owner: ``packages/forgekit-goal``. Roadmap/acceptance: ``docs/forgekit-goal-roadmap.md``
(GW-EXEC). The runtime caller is ``forgekit_runtime.runtime.goal_continuation_tick``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, List, Mapping, Optional, Sequence, Tuple

from .models import Goal, GoalStatus, _utcnow

# Evidence kinds the planning layer reasons about. ``proposal`` is a linked,
# not-yet-executed packet; ``execution``/``verification`` are written by the
# gated apply path on a real run; ``decision`` is a gate refusal (blocked).
EV_PROPOSAL = "proposal"
EV_EXECUTION = "execution"
EV_VERIFICATION = "verification"
EV_DECISION = "decision"
EV_PLAN = "plan"

# Continuation action kinds — the next legal move for a parent's plan.
ADVANCE = "advance"    # activate the next pending child step (safe-class progress)
COMPLETE = "complete"  # every child is done → parent may close (evidence-gated)
REPLAN = "replan"      # a child is blocked → operator must re-decide
WAIT = "wait"          # a child is awaiting_approval / active in-flight → no move
NOOP = "noop"          # nothing to do (no children, or already terminal)


# --------------------------------------------------------------------------- #
# Decomposition
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PlanStep:
    """One sub-step of a decomposed goal. Becomes a child ``Goal`` (draft).

    ``title`` is required (a child goal needs a title); ``intent`` is the
    optional one-line "what closing this step means". A step owns no packets at
    decomposition time — the goal-tick links packets to the child later.
    """

    title: str
    intent: str = ""


def decompose(
    parent: Goal,
    steps: Sequence[PlanStep],
    *,
    now: Callable[[], str] = _utcnow,
    new_id: Optional[Callable[[], str]] = None,
) -> Tuple[Goal, Tuple[Goal, ...]]:
    """Break ``parent`` into ordered child goals. Pure: creates records only.

    Returns ``(parent', children)`` where ``parent'`` has each child linked
    (``add_child``) and a single ``plan`` evidence record summarising the
    decomposition, and ``children`` are fresh DRAFT goals each carrying
    ``parent_id == parent.id``. The caller persists them (store write); this
    function executes nothing and mutates no shared state.

    Idempotency is the caller's concern — re-decomposing appends another plan
    record and new children. The runtime continuation tick decomposes once
    (guarded on "parent already has children"), so ticks don't churn.
    """

    steps = [s for s in steps if (s.title or "").strip()]
    if not steps:
        raise ValueError("decompose needs at least one non-empty plan step")

    g = parent
    children: List[Goal] = []
    for s in steps:
        gid = new_id() if new_id is not None else None
        child = Goal.create(
            s.title,
            intent=s.intent,
            parent_id=parent.id,
            mode=parent.mode,
            goal_id=gid,
            now=now,
        )
        children.append(child)
        g = g.add_child(child.id, now=now)

    titles = ", ".join(c.title for c in children)
    g = g.add_evidence(
        EV_PLAN,
        f"decomposed into {len(children)} step(s): {titles}",
        now=now,
    )
    return g, tuple(children)


# --------------------------------------------------------------------------- #
# Packet / completion accounting (evidence-derived)
# --------------------------------------------------------------------------- #
def _packet_refs(goal: Goal, kind: str) -> set:
    """Set of packet ids that carry an evidence record of ``kind`` (ref-tagged)."""

    return {e.ref for e in goal.evidence if e.kind == kind and e.ref}


@dataclass(frozen=True)
class PacketTally:
    """How a goal's linked proposal packets stand, derived from evidence only."""

    total: int          # linked packets that have a proposal record (runnable candidates)
    executed: int       # have an execution record (verified physical run)
    blocked: int        # have a decision refusal but no execution (gate-blocked)
    pending: int        # proposed but neither executed nor blocked yet


def tally_packets(goal: Goal) -> PacketTally:
    """Classify a goal's proposal packets by their durable evidence.

    Only packets with a ``proposal`` record are counted (those the execute
    bridge can resolve into a runnable packet). ``execution`` wins over
    ``decision`` for the same packet (a packet executed after an earlier refusal
    is executed), so a re-approved-and-run packet is never double-counted as
    blocked."""

    proposed = _packet_refs(goal, EV_PROPOSAL)
    executed_refs = _packet_refs(goal, EV_EXECUTION) & proposed
    decision_refs = _packet_refs(goal, EV_DECISION) & proposed
    blocked_refs = decision_refs - executed_refs
    pending_refs = proposed - executed_refs - blocked_refs
    return PacketTally(
        total=len(proposed),
        executed=len(executed_refs),
        blocked=len(blocked_refs),
        pending=len(pending_refs),
    )


def is_goal_complete(goal: Goal) -> bool:
    """True iff every linked packet ran and was verified — no fake-green.

    A goal is complete when it has at least one ``verification`` record AND every
    proposal packet has an ``execution`` record (nothing pending, nothing
    gate-blocked). A goal with zero packets is NOT complete (nothing to show) —
    completion must be evidence-backed, the same rule ``transitions`` enforces
    for ``done``. This is the predicate the continuation loop uses to advance a
    child to ``done`` and to roll a parent up.
    """

    if goal.status in (GoalStatus.ABANDONED,):
        return False
    t = tally_packets(goal)
    if t.total == 0:
        return False
    has_verification = any(e.kind == EV_VERIFICATION for e in goal.evidence)
    return has_verification and t.pending == 0 and t.blocked == 0


# --------------------------------------------------------------------------- #
# Progress (parent-or-leaf)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GoalProgress:
    """A snapshot of how far a goal has actually advanced (evidence-derived).

    For a decomposed goal (has children) progress is child-based; for a leaf
    goal it is packet-based. ``next_action`` mirrors ``continuation_action`` so a
    single read tells the operator both "how far" and "what's next"."""

    goal_id: str
    decomposed: bool
    total_steps: int        # children (decomposed) or proposal packets (leaf)
    done_steps: int
    blocked_steps: int
    pending_steps: int
    ratio: float            # done / total, 0.0 when total == 0
    complete: bool          # all steps done with evidence (eligible for done)
    next_step_id: Optional[str]  # next child/packet to act on, or None
    summary: str

    def to_dict(self) -> dict:
        return {
            "goal_id": self.goal_id,
            "decomposed": self.decomposed,
            "total_steps": self.total_steps,
            "done_steps": self.done_steps,
            "blocked_steps": self.blocked_steps,
            "pending_steps": self.pending_steps,
            "ratio": round(self.ratio, 4),
            "complete": self.complete,
            "next_step_id": self.next_step_id,
            "summary": self.summary,
        }


def _children_in_order(parent: Goal, resolved: Mapping[str, Goal]) -> List[Goal]:
    """Resolve the parent's child ids to Goals in declared (plan) order.

    Unresolvable child ids (store miss) are skipped — the continuation loop must
    never crash on a dangling reference; it simply has fewer steps to act on."""

    return [resolved[cid] for cid in parent.children if cid in resolved]


def progress(goal: Goal, children: Iterable[Goal] = ()) -> GoalProgress:
    """Compute progress for ``goal``. Pass resolved ``children`` for a parent.

    Child-based when the goal has children, else packet-based. ``next_step_id``
    is the first non-terminal child (or first pending packet) — what the runtime
    would advance or the operator would unblock next."""

    by_id = {c.id: c for c in children}
    kids = _children_in_order(goal, by_id) if goal.children else []

    if kids:
        done = [c for c in kids if c.status == GoalStatus.DONE]
        blocked = [c for c in kids if c.status == GoalStatus.BLOCKED]
        awaiting = [c for c in kids if c.status == GoalStatus.AWAITING_APPROVAL]
        pending = [c for c in kids
                   if c.status not in (GoalStatus.DONE, GoalStatus.ABANDONED)]
        total = len(kids)
        next_step = pending[0].id if pending else None
        complete = len(done) == total and total > 0
        summary = (f"{len(done)}/{total} step done"
                   + (f", {len(blocked)} blocked" if blocked else "")
                   + (f", {len(awaiting)} 승인대기" if awaiting else ""))
        return GoalProgress(
            goal_id=goal.id, decomposed=True, total_steps=total,
            done_steps=len(done), blocked_steps=len(blocked),
            pending_steps=len(pending), ratio=(len(done) / total),
            complete=complete, next_step_id=next_step, summary=summary,
        )

    # leaf goal: packet-based
    t = tally_packets(goal)
    proposed_order = [pid for pid in goal.packets
                      if pid in _packet_refs(goal, EV_PROPOSAL)]
    executed = _packet_refs(goal, EV_EXECUTION)
    blocked_refs = _packet_refs(goal, EV_DECISION) - executed
    next_pid = next((pid for pid in proposed_order
                     if pid not in executed and pid not in blocked_refs), None)
    ratio = (t.executed / t.total) if t.total else 0.0
    summary = (f"{t.executed}/{t.total} packet 실행"
               + (f", {t.blocked} 차단" if t.blocked else "")
               + (f", {t.pending} 대기" if t.pending else ""))
    return GoalProgress(
        goal_id=goal.id, decomposed=False, total_steps=t.total,
        done_steps=t.executed, blocked_steps=t.blocked, pending_steps=t.pending,
        ratio=ratio, complete=is_goal_complete(goal), next_step_id=next_pid,
        summary=summary,
    )


# --------------------------------------------------------------------------- #
# Continuation (the next legal move)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ContinuationAction:
    """The single next move for a decomposed parent's plan. Applies nothing.

    ``kind`` is one of ADVANCE/COMPLETE/REPLAN/WAIT/NOOP. ``target_id`` is the
    child to advance (ADVANCE) or the parent itself (COMPLETE); None otherwise.
    The runtime caller routes ADVANCE/COMPLETE through ``transitions.apply``."""

    kind: str
    target_id: Optional[str]
    reason: str


def continuation_action(parent: Goal, children: Iterable[Goal] = ()) -> ContinuationAction:
    """Decide the next legal move for a decomposed parent. Pure.

    Sequential-plan semantics: walk children in plan order. The first child that
    is not DONE/ABANDONED is the cursor:

    - BLOCKED       → ``REPLAN`` (operator must re-decide; never auto-advance)
    - AWAITING      → ``WAIT`` (operator approval pending)
    - ACTIVE        → ``WAIT`` while a packet is still pending; ``REPLAN`` once every
      packet has been attempted and gate-blocked (stuck — the exec tick won't retry a
      gate-refused packet, so only the operator can move it)
    - DRAFT         → ``ADVANCE`` (activate it so the exec tick can run its packets)

    If every child is DONE → ``COMPLETE`` (parent eligible for ``done``). A parent
    with no children → ``NOOP`` (a leaf goal; the goal-exec tick handles it)."""

    if not parent.children:
        return ContinuationAction(NOOP, None, "no children (leaf goal)")
    if parent.status in (GoalStatus.DONE, GoalStatus.ABANDONED):
        return ContinuationAction(NOOP, None, f"parent already {parent.status.value}")

    by_id = {c.id: c for c in children}
    kids = _children_in_order(parent, by_id)
    if not kids:
        return ContinuationAction(NOOP, None, "child ids unresolved")

    for child in kids:
        if child.status in (GoalStatus.DONE, GoalStatus.ABANDONED):
            continue
        if child.status == GoalStatus.BLOCKED:
            return ContinuationAction(REPLAN, child.id,
                                      f"child {child.id} blocked — operator re-decide")
        if child.status == GoalStatus.AWAITING_APPROVAL:
            return ContinuationAction(WAIT, child.id,
                                      f"child {child.id} 승인 대기")
        if child.status == GoalStatus.ACTIVE:
            t = tally_packets(child)
            # Stuck: every packet attempted, some gate-blocked, none pending → the exec
            # tick won't retry a gate-refused packet, so only the operator can move it.
            if t.pending == 0 and t.blocked > 0:
                return ContinuationAction(REPLAN, child.id,
                                          f"child {child.id} 게이트 차단 — operator re-decide")
            return ContinuationAction(WAIT, child.id,
                                      f"child {child.id} 실행 중")
        # DRAFT — the next step to activate
        return ContinuationAction(ADVANCE, child.id,
                                  f"activate next step {child.id}")

    # no non-terminal child remained → all done
    return ContinuationAction(COMPLETE, parent.id,
                              f"all {len(kids)} step done — parent eligible for done")


__all__ = (
    "PlanStep",
    "decompose",
    "PacketTally",
    "tally_packets",
    "is_goal_complete",
    "GoalProgress",
    "progress",
    "ContinuationAction",
    "continuation_action",
    "ADVANCE",
    "COMPLETE",
    "REPLAN",
    "WAIT",
    "NOOP",
    "EV_PROPOSAL",
    "EV_EXECUTION",
    "EV_VERIFICATION",
    "EV_DECISION",
    "EV_PLAN",
)
