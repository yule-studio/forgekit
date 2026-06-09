"""Agent status contract.

``AgentStatus`` is the small, department-agnostic vocabulary for "what state is
this agent / seat / session in" that other agents and the Agent Town front-end
can rely on. The rich diagnostic reports
(``SessionStatusReport``, ``LifecycleStatus``, ``ServiceStatus``) stay in their
domain modules; they describe *how* a particular subsystem is doing. This enum
is the coarse, stable lifecycle state those reports can collapse onto.
"""

from __future__ import annotations

from enum import Enum


class AgentStatus(str, Enum):
    """Coarse lifecycle state of an agent / seat / session."""

    IDLE = "idle"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"


# States in which the agent is not actively progressing and may need a human
# or upstream signal to move forward.
STALLED_STATUSES = frozenset(
    {AgentStatus.WAITING_APPROVAL, AgentStatus.BLOCKED, AgentStatus.FAILED}
)

# Terminal states — the unit of work is finished, one way or another.
TERMINAL_STATUSES = frozenset({AgentStatus.DONE, AgentStatus.FAILED})
