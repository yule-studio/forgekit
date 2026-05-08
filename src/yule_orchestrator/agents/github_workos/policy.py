"""Permission model for GitHub workspace actions.

This module is the single decision point for "is the agent allowed to
do action X right now?". It is **strictly offline** — it does not
read env, does not touch the GitHub API, does not look up secrets.
Callers pass everything in:

  * ``action`` — one of the ``ACTION_*`` constants below.
  * ``approval_granted`` — boolean, set by an *out-of-band* explicit
    operator approval (e.g. an Approval card reply). Defaults to
    False.
  * ``target_branch`` — optional; if the action targets a branch
    name in :data:`PROTECTED_BRANCH_NAMES`, the action is denied even
    when ``approval_granted=True``.
  * ``force`` — boolean; force-push / destructive flags. Always
    denies for shared / protected scope.

Returns a :class:`PermissionDecision` with ``allowed`` + ``deny_reason``.
The deny_reason string is the audit-row text — keep it short and
factual.

The five-rung mapping mirrors the G2 spec:

    L0_READ            — read issue / PR / code / log
    L1_LIGHT_WRITE     — issue comment, label, research-log,
                          planning issue
    L2_PLAN            — branch plan, code draft plan, test plan,
                          draft PR plan
    L3_REAL_WRITE      — push commit, ready PR, vault git push,
                          real code write request (denied without
                          explicit approval)
    L4_DESTRUCTIVE     — merge, deploy, secret change, destructive
                          delete (denied without explicit approval;
                          force-push to protected branch is denied
                          even *with* approval)
"""

from __future__ import annotations

from typing import Mapping, Optional, Tuple

from .models import PermissionDecision, PermissionLevel


# ---------------------------------------------------------------------------
# Action constants
# ---------------------------------------------------------------------------


# L0 — read-only
ACTION_READ_ISSUE: str = "read_issue"
ACTION_READ_PR: str = "read_pr"
ACTION_READ_CODE: str = "read_code"
ACTION_READ_LOG: str = "read_log"

# L1 — light write (visible only on issue/PR thread)
ACTION_ISSUE_COMMENT: str = "issue_comment"
ACTION_ISSUE_LABEL: str = "issue_label"
ACTION_RESEARCH_LOG: str = "research_log"
ACTION_PLANNING_ISSUE: str = "planning_issue"

# L2 — plan only (no actual write)
ACTION_BRANCH_PLAN: str = "branch_plan"
ACTION_CODE_DRAFT_PLAN: str = "code_draft_plan"
ACTION_TEST_PLAN: str = "test_plan"
ACTION_DRAFT_PR_PLAN: str = "draft_pr_plan"

# L3 — real writes that need explicit approval
ACTION_PUSH_COMMIT: str = "push_commit"
ACTION_READY_PR: str = "ready_pr"
ACTION_VAULT_GIT_PUSH: str = "vault_git_push"
ACTION_REAL_CODE_WRITE_REQUEST: str = "real_code_write_request"

# L4 — destructive / strong approval required
ACTION_MERGE: str = "merge"
ACTION_DEPLOY: str = "deploy"
ACTION_SECRET_CHANGE: str = "secret_change"
ACTION_DESTRUCTIVE_DELETE: str = "destructive_delete"
ACTION_FORCE_PUSH: str = "force_push"


_ACTION_LEVELS: Mapping[str, PermissionLevel] = {
    # L0
    ACTION_READ_ISSUE: PermissionLevel.L0_READ,
    ACTION_READ_PR: PermissionLevel.L0_READ,
    ACTION_READ_CODE: PermissionLevel.L0_READ,
    ACTION_READ_LOG: PermissionLevel.L0_READ,
    # L1
    ACTION_ISSUE_COMMENT: PermissionLevel.L1_LIGHT_WRITE,
    ACTION_ISSUE_LABEL: PermissionLevel.L1_LIGHT_WRITE,
    ACTION_RESEARCH_LOG: PermissionLevel.L1_LIGHT_WRITE,
    ACTION_PLANNING_ISSUE: PermissionLevel.L1_LIGHT_WRITE,
    # L2
    ACTION_BRANCH_PLAN: PermissionLevel.L2_PLAN,
    ACTION_CODE_DRAFT_PLAN: PermissionLevel.L2_PLAN,
    ACTION_TEST_PLAN: PermissionLevel.L2_PLAN,
    ACTION_DRAFT_PR_PLAN: PermissionLevel.L2_PLAN,
    # L3
    ACTION_PUSH_COMMIT: PermissionLevel.L3_REAL_WRITE,
    ACTION_READY_PR: PermissionLevel.L3_REAL_WRITE,
    ACTION_VAULT_GIT_PUSH: PermissionLevel.L3_REAL_WRITE,
    ACTION_REAL_CODE_WRITE_REQUEST: PermissionLevel.L3_REAL_WRITE,
    # L4
    ACTION_MERGE: PermissionLevel.L4_DESTRUCTIVE,
    ACTION_DEPLOY: PermissionLevel.L4_DESTRUCTIVE,
    ACTION_SECRET_CHANGE: PermissionLevel.L4_DESTRUCTIVE,
    ACTION_DESTRUCTIVE_DELETE: PermissionLevel.L4_DESTRUCTIVE,
    ACTION_FORCE_PUSH: PermissionLevel.L4_DESTRUCTIVE,
}


#: Branch names the agent must never write to directly. Even with an
#: explicit approval token, a push to one of these is denied — the
#: human must do that themselves with manual scrutiny. Comparison is
#: case-insensitive and accepts the bare name (``"main"``) or a
#: fully-qualified ref (``"refs/heads/main"``).
PROTECTED_BRANCH_NAMES: Tuple[str, ...] = (
    "main",
    "master",
    "prod",
    "production",
    "release",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def permission_level_for_action(action: str) -> PermissionLevel:
    """Return the permission level for *action*.

    Raises :class:`KeyError` for unknown actions — silently mapping
    them to L0 would let new actions slip in without a policy review.
    """

    if action not in _ACTION_LEVELS:
        raise KeyError(f"unknown github_workos action: {action!r}")
    return _ACTION_LEVELS[action]


def _is_protected_branch(target_branch: Optional[str]) -> bool:
    if not target_branch:
        return False
    name = target_branch.strip().lower()
    if name.startswith("refs/heads/"):
        name = name[len("refs/heads/") :]
    return name in PROTECTED_BRANCH_NAMES


def decide_permission(
    action: str,
    *,
    approval_granted: bool = False,
    target_branch: Optional[str] = None,
    force: bool = False,
) -> PermissionDecision:
    """Decide whether *action* may be executed right now.

    Rules (first match wins):

    1. Unknown action → KeyError (defensive).
    2. Force flag against a protected branch → deny, even with
       approval.
    3. ACTION_FORCE_PUSH against a protected branch → deny, even
       with approval. Force-push elsewhere is L4 destructive and
       still requires explicit approval.
    4. ACTION_DESTRUCTIVE_DELETE / ACTION_SECRET_CHANGE — always
       require explicit approval; never granted "just because".
    5. Any L3+ action without ``approval_granted`` → deny.
    6. Any write (L1+) targeting a protected branch → deny.
    7. Otherwise → allow.
    """

    level = permission_level_for_action(action)

    # 6. Protected branch gate (applies to writes, not reads).
    protected = _is_protected_branch(target_branch)

    if force and protected:
        return PermissionDecision(
            allowed=False,
            level=level,
            requires_approval=True,
            deny_reason=(
                "force flag against protected branch "
                f"{target_branch!r} — denied (manual operator only)"
            ),
            action=action,
        )

    if action == ACTION_FORCE_PUSH and protected:
        return PermissionDecision(
            allowed=False,
            level=level,
            requires_approval=True,
            deny_reason=(
                f"force-push to protected branch {target_branch!r} — "
                "denied (manual operator only)"
            ),
            action=action,
        )

    if action == ACTION_DESTRUCTIVE_DELETE and not approval_granted:
        return PermissionDecision(
            allowed=False,
            level=level,
            requires_approval=True,
            deny_reason=(
                "destructive delete requires explicit operator approval"
            ),
            action=action,
        )

    if action == ACTION_SECRET_CHANGE and not approval_granted:
        return PermissionDecision(
            allowed=False,
            level=level,
            requires_approval=True,
            deny_reason=(
                "secret change requires explicit operator approval"
            ),
            action=action,
        )

    if level in (PermissionLevel.L3_REAL_WRITE, PermissionLevel.L4_DESTRUCTIVE):
        if not approval_granted:
            return PermissionDecision(
                allowed=False,
                level=level,
                requires_approval=True,
                deny_reason=(
                    f"action {action!r} at level {level.value} "
                    "requires explicit operator approval"
                ),
                action=action,
            )

    # Writes targeting a protected branch — deny unconditionally for
    # L1/L2/L3 (L4 already handled above when approval is missing).
    if protected and level not in (PermissionLevel.L0_READ,):
        # L4 with approval still cannot land on a protected branch
        # via this code path — operator does it manually.
        return PermissionDecision(
            allowed=False,
            level=level,
            requires_approval=True,
            deny_reason=(
                f"writes to protected branch {target_branch!r} "
                "are denied (manual operator only)"
            ),
            action=action,
        )

    return PermissionDecision(
        allowed=True,
        level=level,
        requires_approval=level
        in (PermissionLevel.L3_REAL_WRITE, PermissionLevel.L4_DESTRUCTIVE),
        deny_reason="",
        action=action,
    )


__all__ = [
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
]
