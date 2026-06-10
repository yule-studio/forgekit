"""Job state machine for the always-on engineering runtime.

The 11 states match the always-on operations spec
(:doc:`docs/operations.md`). Why the lifecycle exists at *this*
granularity instead of reusing :class:`agents.workflow_state.WorkflowState`:

- ``WorkflowState`` is the *macro* lifecycle of a user-facing session
  (intake / in_progress / completed / rejected). It changes a few
  times per session.
- ``JobState`` is the *micro* lifecycle of a single dispatched
  turn — one role take, one collector pass, one Obsidian write,
  one approval-channel post. It changes many times per session.

Keeping these separate lets a worker crash mid-turn without rolling
back the whole session, and lets the supervisor reason about job
recovery without re-running session-level decisions.
"""

from __future__ import annotations

from enum import Enum
from typing import FrozenSet, Mapping


class JobState(str, Enum):
    """11 lifecycle states for a single dispatched job.

    String-valued so the enum round-trips through SQLite TEXT columns
    and JSON directly. Order intentionally matches the natural happy
    path so a state listing reads like the lifecycle.
    """

    DISCOVERED = "discovered"
    QUEUED = "queued"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    WAITING_FOR_ROLE = "waiting_for_role"
    RESEARCHING = "researching"
    PENDING_APPROVAL = "pending_approval"
    READY_FOR_OBSIDIAN = "ready_for_obsidian"
    SAVED = "saved"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"


# Allowed transitions. Anything not listed here raises in
# :func:`validate_transition`. The graph is intentionally small —
# divergent paths usually indicate a bug, not a missing edge.
STATE_TRANSITIONS: Mapping[JobState, FrozenSet[JobState]] = {
    JobState.DISCOVERED: frozenset({JobState.QUEUED, JobState.FAILED_TERMINAL}),
    JobState.QUEUED: frozenset(
        {
            JobState.ASSIGNED,
            JobState.WAITING_FOR_ROLE,
            JobState.FAILED_TERMINAL,
        }
    ),
    JobState.ASSIGNED: frozenset(
        {JobState.IN_PROGRESS, JobState.QUEUED, JobState.FAILED_RETRYABLE}
    ),
    JobState.IN_PROGRESS: frozenset(
        {
            JobState.RESEARCHING,
            JobState.PENDING_APPROVAL,
            JobState.READY_FOR_OBSIDIAN,
            JobState.SAVED,
            JobState.FAILED_RETRYABLE,
            JobState.FAILED_TERMINAL,
            JobState.WAITING_FOR_ROLE,
        }
    ),
    JobState.WAITING_FOR_ROLE: frozenset(
        {JobState.QUEUED, JobState.ASSIGNED, JobState.FAILED_TERMINAL}
    ),
    JobState.RESEARCHING: frozenset(
        {
            JobState.IN_PROGRESS,
            JobState.READY_FOR_OBSIDIAN,
            JobState.FAILED_RETRYABLE,
            JobState.FAILED_TERMINAL,
        }
    ),
    JobState.PENDING_APPROVAL: frozenset(
        {
            JobState.IN_PROGRESS,
            JobState.READY_FOR_OBSIDIAN,
            JobState.SAVED,
            JobState.FAILED_TERMINAL,
        }
    ),
    JobState.READY_FOR_OBSIDIAN: frozenset(
        {
            JobState.ASSIGNED,
            JobState.SAVED,
            JobState.FAILED_RETRYABLE,
            JobState.FAILED_TERMINAL,
        }
    ),
    # ``failed_retryable`` requeues to ``queued`` for backoff retry —
    # the supervisor / consumer is responsible for setting
    # ``available_at`` so the next pick is rate-limited.
    JobState.FAILED_RETRYABLE: frozenset(
        {JobState.QUEUED, JobState.FAILED_TERMINAL}
    ),
    # Terminal — no outgoing edges. Saved + failed_terminal are
    # reaped after retention windows, never transitioned.
    JobState.SAVED: frozenset(),
    JobState.FAILED_TERMINAL: frozenset(),
}


TERMINAL_STATES: FrozenSet[JobState] = frozenset(
    {JobState.SAVED, JobState.FAILED_TERMINAL}
)


def is_terminal(state: JobState) -> bool:
    """True for states the supervisor leaves alone (no reaper touches)."""

    return state in TERMINAL_STATES


def validate_transition(current: JobState, target: JobState) -> None:
    """Raise :class:`ValueError` when the transition isn't allowed.

    Same-state self-transition is rejected too — callers should
    short-circuit if they have nothing to update. Catching this in
    one place keeps the invariant visible: any transition the queue
    persists has been checked against :data:`STATE_TRANSITIONS`.
    """

    if current == target:
        raise ValueError(
            f"job state transition '{current.value}' → '{target.value}' is a no-op"
        )
    allowed = STATE_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise ValueError(
            f"job state transition '{current.value}' → '{target.value}' is not allowed"
        )
