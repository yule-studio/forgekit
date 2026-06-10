"""Agent command contract.

``AgentCommand`` is the department-agnostic envelope for "do this" instructions
flowing *into* an agent (from the gateway, from another agent, or from the
Agent Town front-end). It is intentionally thin: the heavyweight in-process
representation is ``yule_engineering.agents.job_queue.store.Job``, whose
``job_type`` + ``payload`` map onto ``command`` + ``payload`` here. Keeping the
contract separate from the queue row lets transports (Discord, HTTP, sockets)
speak a stable shape without importing the queue internals.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional

from .roles import AgentRole
from .tasks import TaskRef


@dataclass(frozen=True)
class AgentCommand:
    """An instruction directed at an agent.

    - ``command``: the verb / job type (e.g. ``"research_collect"``,
      ``"role_take"``, ``"coding_execute"``).
    - ``target``: which agent/role should execute it (optional — the gateway
      may leave routing to the runtime).
    - ``task``: the work item the command concerns.
    - ``payload``: command-specific parameters (kept as a plain mapping so the
      contract does not need to know every command's shape).
    """

    command: str
    target: Optional[AgentRole] = None
    task: Optional[TaskRef] = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    issued_by: Optional[str] = None
    command_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: datetime = field(default_factory=datetime.utcnow)
