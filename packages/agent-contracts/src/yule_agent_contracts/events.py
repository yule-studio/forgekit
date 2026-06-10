"""Agent event contract.

``AgentEvent`` is the department-agnostic envelope for things that *happen*
inside an agent and need to be observed elsewhere (the gateway, sibling agents,
the Agent Town front-end): a job completed, a seat got blocked, a lifecycle
stage advanced. The heavyweight in-process counterpart is
``yule_engineering.agents.job_queue.completion_hook.JobCompletionEvent``; this
contract is the stable wire/observation shape it can project onto.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional

from .roles import AgentRole
from .tasks import TaskRef

# Well-known event names. Free-form is allowed; these avoid typos.
EVENT_STARTED = "started"
EVENT_PROGRESS = "progress"
EVENT_COMPLETED = "completed"
EVENT_BLOCKED = "blocked"
EVENT_FAILED = "failed"
EVENT_STATE_CHANGED = "state_changed"
EVENT_APPROVAL_REQUESTED = "approval_requested"


@dataclass(frozen=True)
class AgentEvent:
    """An event emitted by an agent.

    - ``event``: what happened (see the ``EVENT_*`` constants).
    - ``source``: which agent/role emitted it.
    - ``task``: the work item the event concerns.
    - ``status`` / ``reason``: optional outcome detail.
    - ``payload``: event-specific metadata.
    """

    event: str
    source: Optional[AgentRole] = None
    task: Optional[TaskRef] = None
    status: Optional[str] = None
    reason: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: datetime = field(default_factory=datetime.utcnow)
