"""github_workos — GitHub workspace policy + senior-engineer triage.

This package answers two questions the engineering-agent has to settle
*before* it touches a GitHub repository:

  1. **Identity / authority** — which agent role owns which surface,
     what permission level a given action lives at, and which actions
     are flat-out denied (main branch direct push, secret access,
     destructive deletes). See :mod:`.identity` and :mod:`.policy`.

  2. **Senior triage** — given a GitHub issue or a Discord work
     intake, produce a senior-engineer style :class:`TriagePlan`:
     primary / support / excluded roles with per-role rationale, scope
     vs. non-scope, hidden risks, an implementation step list, a test
     plan, an approval gate when code change is implied, and a per-role
     work order so each active engineer knows their mission, expected
     output, and handoff. See :mod:`.triage`.

Strictly offline. No GitHub API, no Discord API, no env / secret /
private-key access happens here. Later G-tasks (G3 executor, G4
Discord forum, G6 integration) wire the live runtime to this surface.
"""

from __future__ import annotations

from .identity import (
    COMMIT_AUTHOR_POLICY_OWNER_AS_AUTHOR,
    GITHUB_APP_ACTOR,
    AgentIdentity,
    agent_identity,
    all_agent_identities,
)
from .issue_context import (
    SourceKind,
    WorkRequest,
    build_request_from_discord_intake,
    build_request_from_github_issue,
    redact_secret_like,
)
from .models import (
    PermissionDecision,
    PermissionLevel,
    RiskLevel,
    RoleWorkOrder,
    TriagePlan,
)
from .policy import (
    ACTION_BRANCH_PLAN,
    ACTION_CODE_DRAFT_PLAN,
    ACTION_DEPLOY,
    ACTION_DESTRUCTIVE_DELETE,
    ACTION_DRAFT_PR_PLAN,
    ACTION_FORCE_PUSH,
    ACTION_ISSUE_COMMENT,
    ACTION_ISSUE_LABEL,
    ACTION_MERGE,
    ACTION_PLANNING_ISSUE,
    ACTION_PUSH_COMMIT,
    ACTION_READ_CODE,
    ACTION_READ_ISSUE,
    ACTION_READ_LOG,
    ACTION_READ_PR,
    ACTION_READY_PR,
    ACTION_REAL_CODE_WRITE_REQUEST,
    ACTION_RESEARCH_LOG,
    ACTION_SECRET_CHANGE,
    ACTION_TEST_PLAN,
    ACTION_VAULT_GIT_PUSH,
    PROTECTED_BRANCH_NAMES,
    decide_permission,
    permission_level_for_action,
)
from .triage import senior_triage

__all__ = [
    # identity
    "AgentIdentity",
    "GITHUB_APP_ACTOR",
    "COMMIT_AUTHOR_POLICY_OWNER_AS_AUTHOR",
    "agent_identity",
    "all_agent_identities",
    # issue context
    "SourceKind",
    "WorkRequest",
    "build_request_from_discord_intake",
    "build_request_from_github_issue",
    "redact_secret_like",
    # models
    "PermissionDecision",
    "PermissionLevel",
    "RiskLevel",
    "RoleWorkOrder",
    "TriagePlan",
    # policy
    "ACTION_BRANCH_PLAN",
    "ACTION_CODE_DRAFT_PLAN",
    "ACTION_DEPLOY",
    "ACTION_DESTRUCTIVE_DELETE",
    "ACTION_DRAFT_PR_PLAN",
    "ACTION_FORCE_PUSH",
    "ACTION_ISSUE_COMMENT",
    "ACTION_ISSUE_LABEL",
    "ACTION_MERGE",
    "ACTION_PLANNING_ISSUE",
    "ACTION_PUSH_COMMIT",
    "ACTION_READ_CODE",
    "ACTION_READ_ISSUE",
    "ACTION_READ_LOG",
    "ACTION_READ_PR",
    "ACTION_READY_PR",
    "ACTION_REAL_CODE_WRITE_REQUEST",
    "ACTION_RESEARCH_LOG",
    "ACTION_SECRET_CHANGE",
    "ACTION_TEST_PLAN",
    "ACTION_VAULT_GIT_PUSH",
    "PROTECTED_BRANCH_NAMES",
    "decide_permission",
    "permission_level_for_action",
    # triage
    "senior_triage",
]
