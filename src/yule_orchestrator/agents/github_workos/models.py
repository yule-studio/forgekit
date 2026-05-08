"""Shared dataclasses + enums for the github_workos surface.

Keeping the shape definitions in one tiny module so that
:mod:`.identity`, :mod:`.policy`, :mod:`.triage`, and
:mod:`.issue_context` can import without cycles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional, Tuple


class PermissionLevel(str, Enum):
    """Five-rung permission ladder for GitHub workspace actions.

    Aligned with the wider engineering-agent autonomy ladder
    (:mod:`agents.lifecycle.autonomy_policy`) but tuned for GitHub
    surfaces specifically:

    * ``L0_READ`` — read-only (issue / PR / code / log).
    * ``L1_LIGHT_WRITE`` — issue comment, label, research-log,
      planning issue. Side effects visible only on the issue/PR
      thread, not in a branch or release surface.
    * ``L2_PLAN`` — branch plan, code draft plan, test plan, draft PR
      plan. Plans are *not* writes; they describe what would happen.
    * ``L3_REAL_WRITE`` — push commit, ready PR, vault git push, real
      code write request. Always denied without explicit approval.
    * ``L4_DESTRUCTIVE`` — merge, deploy, secret change, destructive
      command (force-push, branch delete, prod data drop). Requires
      strong approval; some actions (e.g. force-push to main) are
      flat-out denied even with approval.
    """

    L0_READ = "L0_READ"
    L1_LIGHT_WRITE = "L1_LIGHT_WRITE"
    L2_PLAN = "L2_PLAN"
    L3_REAL_WRITE = "L3_REAL_WRITE"
    L4_DESTRUCTIVE = "L4_DESTRUCTIVE"


_LEVEL_RANK: Mapping[PermissionLevel, int] = {
    PermissionLevel.L0_READ: 0,
    PermissionLevel.L1_LIGHT_WRITE: 1,
    PermissionLevel.L2_PLAN: 2,
    PermissionLevel.L3_REAL_WRITE: 3,
    PermissionLevel.L4_DESTRUCTIVE: 4,
}


def permission_rank(level: PermissionLevel) -> int:
    """Numeric rank so callers can compare levels with ``<`` / ``>``."""

    return _LEVEL_RANK[level]


class RiskLevel(str, Enum):
    """Coarse risk bucket carried on a triage plan."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class PermissionDecision:
    """Result of a single permission check in :func:`policy.decide_permission`.

    * ``allowed`` — final yes/no.
    * ``level`` — which permission rung the action lives at.
    * ``requires_approval`` — True for L3+ unless an approval token
      was supplied.
    * ``deny_reason`` — non-empty when ``allowed`` is False; empty
      otherwise. Used verbatim in audit rows / Discord rejection
      messages.
    * ``action`` — the canonical ACTION_* id passed in.
    """

    allowed: bool
    level: PermissionLevel
    requires_approval: bool
    deny_reason: str
    action: str


@dataclass(frozen=True)
class RoleWorkOrder:
    """Per-role assignment slip emitted by :func:`triage.senior_triage`.

    Each active role gets one. Excluded roles do NOT get a work order
    (the triage plan tracks them in ``excluded_roles`` +
    ``rationale_by_role`` instead — generating a work order for an
    excluded role would let downstream code mistakenly schedule that
    role).
    """

    role: str
    mission: str
    expected_output: str
    files_or_domains_to_inspect: Tuple[str, ...]
    done_criteria: Tuple[str, ...]
    handoff_to_next_role: Optional[str]


@dataclass(frozen=True)
class TriagePlan:
    """Senior-engineer style triage output.

    Required fields are ordered to mirror the spec in the G2 task. All
    sequence fields are ``Tuple[str, ...]`` so the plan is hashable
    and JSON-serialisable without extra ceremony.
    """

    request_type: str
    primary_role: str
    support_roles: Tuple[str, ...]
    excluded_roles: Tuple[str, ...]
    rationale_by_role: Mapping[str, str]
    risk_level: RiskLevel
    autonomy_level: PermissionLevel
    scope: Tuple[str, ...]
    non_scope: Tuple[str, ...]
    hidden_risks: Tuple[str, ...]
    assumptions: Tuple[str, ...]
    implementation_steps: Tuple[str, ...]
    test_plan: Tuple[str, ...]
    approval_required_actions: Tuple[str, ...]
    suggested_branch: str
    role_work_orders: Tuple[RoleWorkOrder, ...]
    coding_required: bool = False
    approval_required_before_write: bool = False
    decisions: Tuple[str, ...] = field(default_factory=tuple)


__all__ = [
    "PermissionDecision",
    "PermissionLevel",
    "RiskLevel",
    "RoleWorkOrder",
    "TriagePlan",
    "permission_rank",
]
