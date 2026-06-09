"""Task reference contract.

``TaskRef`` is a department-agnostic pointer to a unit of work: an internal
session/brief id, and/or an external GitHub issue/PR. It is deliberately a
*reference* (identity + location), not the work item itself — the rich
in-process types (``Job``, ``TaskBrief``, …) stay in their domain modules and
can expose a ``TaskRef`` view when they need to be addressed across agents or
surfaced to the Agent Town front-end.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Well-known ``kind`` values. Free-form is allowed; these avoid typos.
KIND_TASK = "task"
KIND_ISSUE = "issue"
KIND_PR = "pr"
KIND_SESSION = "session"
KIND_BRIEF = "brief"


@dataclass(frozen=True)
class TaskRef:
    """A pointer to a task / issue / work item.

    At least one of ``task_id`` or (``repo`` + ``number``) is expected to be
    set so the reference resolves to something. ``kind`` labels what the
    reference points at (see the ``KIND_*`` constants).
    """

    task_id: str = ""
    repo: Optional[str] = None  # "owner/name"
    number: Optional[int] = None  # GitHub issue / PR number
    kind: str = KIND_TASK
    label: Optional[str] = None

    @property
    def slug(self) -> str:
        """A short human/log-friendly identifier for this reference."""

        if self.repo and self.number is not None:
            return f"{self.repo}#{self.number}"
        if self.number is not None:
            return f"#{self.number}"
        return self.task_id or self.kind
