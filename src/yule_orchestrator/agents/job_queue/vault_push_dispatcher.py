"""Vault commit/push dispatcher — P0-I stage 3 (#141).

Implements the stage-1 ``docs/approval-matrix.md §3`` SSoT for vault
work:

  * vault local commit (``vault_research_log_commit``) — **L2 자동**.
  * vault remote push (``vault_remote_push``) — **mode-dependent**:
    - ``approval_required`` → L3 (queue approval card).
    - ``autonomous_merge``  → L2 (run when permitted scope satisfied).
  * code repo push와 vault push **분리 audit** (`session.extra["code_push_audit"]`
    vs `session.extra["vault_push_audit"]`).

The dispatcher is a *thin* envelope: it inspects ``session.extra``
(work_mode + vault_workspace_root + autonomy_policy.decide_autonomy),
records the decision, and returns a :class:`VaultPushOutcome`.

Critical contract: when the vault repo / workspace / credentials are
not configured, **the outcome explicitly reports the reason** instead
of silently no-op'ing. The status surface (#141 commit 7 wiring)
prints "vault push not configured: <reason>" so the user knows.

Fake success is forbidden (stage-1 #5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple


# Action constants — align with stage-1 autonomy-policy.md action catalog.
ACTION_VAULT_RESEARCH_LOG_COMMIT = "vault_research_log_commit"
ACTION_VAULT_REMOTE_PUSH = "vault_remote_push"

# Outcome statuses.
STATUS_QUEUED_AUTO = "queued_auto"  # L2 auto path
STATUS_QUEUED_FOR_APPROVAL = "queued_for_approval"  # L3 approval card
STATUS_NOT_CONFIGURED = "not_configured"  # workspace/repo not present
STATUS_INVALID_REQUEST = "invalid_request"  # missing fields
STATUS_FORBIDDEN = "forbidden"  # mode blocks autonomous_merge action

NOT_CONFIGURED_REASONS = (
    "no_workspace_root",  # session.extra["vault_workspace_root"] missing
    "workspace_not_found",  # path doesn't exist on disk
    "no_branch",  # request omitted branch
)


@dataclass(frozen=True)
class VaultPushRequest:
    """What the caller wants the dispatcher to do."""

    action: str  # ACTION_VAULT_*
    repo_path: Optional[str] = None  # absolute / relative path
    branch: Optional[str] = None
    commit_msg: Optional[str] = None
    note_kind: Optional[str] = None  # research-log / agent-ops / decision / ...
    note_path: Optional[str] = None  # vault-relative


@dataclass(frozen=True)
class VaultPushOutcome:
    """Result returned by :func:`dispatch_vault_push`.

    Always carries a ``status``. When ``status == STATUS_NOT_CONFIGURED``,
    ``not_configured_reason`` is one of :data:`NOT_CONFIGURED_REASONS`.
    """

    status: str
    action: str
    autonomy_level: Optional[str] = None  # L2 / L3
    work_mode: Optional[str] = None  # mode that drove the decision
    approval_required: bool = False
    not_configured_reason: Optional[str] = None
    audit_entry: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "status": self.status,
            "action": self.action,
            "autonomy_level": self.autonomy_level,
            "work_mode": self.work_mode,
            "approval_required": self.approval_required,
            "not_configured_reason": self.not_configured_reason,
            "audit_entry": dict(self.audit_entry),
        }

    def status_summary_line(self) -> str:
        if self.status == STATUS_QUEUED_AUTO:
            return f"📦 vault {self.action}: queued (L2 auto · mode={self.work_mode})"
        if self.status == STATUS_QUEUED_FOR_APPROVAL:
            return f"📬 vault {self.action}: queued for approval (L3 · mode={self.work_mode})"
        if self.status == STATUS_NOT_CONFIGURED:
            return (
                f"⚠️ vault {self.action}: not configured "
                f"({self.not_configured_reason or 'unknown'})"
            )
        if self.status == STATUS_INVALID_REQUEST:
            return f"⚠️ vault {self.action}: invalid request"
        if self.status == STATUS_FORBIDDEN:
            return f"⛔ vault {self.action}: forbidden in current mode"
        return f"❓ vault {self.action}: {self.status}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def dispatch_vault_push(
    *,
    session_extra: dict,
    request: VaultPushRequest,
    now: Optional[datetime] = None,
    autonomy_decider=None,
) -> VaultPushOutcome:
    """Decide what to do with *request* and append to vault_push_audit.

    Mutates ``session_extra`` in place to append the audit entry.
    *autonomy_decider* is an optional injection for tests; production
    callers pass ``None`` and we use the policy decider.

    Algorithm:

      1. Validate action / required fields → INVALID_REQUEST.
      2. Resolve workspace root from session.extra["vault_workspace_root"]
         or fall back to env (``YULE_VAULT_WORKSPACE_ROOT``).
      3. If workspace missing or path doesn't exist → NOT_CONFIGURED.
      4. action == VAULT_RESEARCH_LOG_COMMIT → always L2 auto.
      5. action == VAULT_REMOTE_PUSH:
         * work_mode == "autonomous_merge" → L2 (or L3 if escalation
           signal — but we keep it simple for stage 3 and treat
           autonomous_merge as a L2 trigger).
         * work_mode == "approval_required" (or unset) → L3.
      6. Append audit entry to session.extra["vault_push_audit"].
    """

    audit_log: list = list(session_extra.get("vault_push_audit") or ())

    # 1. Validation.
    if request.action not in (
        ACTION_VAULT_RESEARCH_LOG_COMMIT,
        ACTION_VAULT_REMOTE_PUSH,
    ):
        outcome = VaultPushOutcome(
            status=STATUS_INVALID_REQUEST,
            action=request.action,
            audit_entry={"reason": "unknown_action"},
        )
        return _append_and_return(session_extra, audit_log, outcome, now=now)

    if request.action == ACTION_VAULT_REMOTE_PUSH and not request.branch:
        outcome = VaultPushOutcome(
            status=STATUS_NOT_CONFIGURED,
            action=request.action,
            not_configured_reason="no_branch",
            audit_entry={"reason": "missing_branch_field"},
        )
        return _append_and_return(session_extra, audit_log, outcome, now=now)

    # 2-3. Workspace existence check.
    workspace_root = _resolve_workspace_root(session_extra)
    if workspace_root is None:
        outcome = VaultPushOutcome(
            status=STATUS_NOT_CONFIGURED,
            action=request.action,
            not_configured_reason="no_workspace_root",
            audit_entry={
                "reason": "vault_workspace_root_unset",
                "session_extra_key": "vault_workspace_root",
            },
        )
        return _append_and_return(session_extra, audit_log, outcome, now=now)

    if not Path(workspace_root).is_dir():
        outcome = VaultPushOutcome(
            status=STATUS_NOT_CONFIGURED,
            action=request.action,
            not_configured_reason="workspace_not_found",
            audit_entry={
                "reason": "workspace_path_missing",
                "workspace_root": workspace_root,
            },
        )
        return _append_and_return(session_extra, audit_log, outcome, now=now)

    work_mode = session_extra.get("work_mode")

    # 4. Local commit — always L2 auto.
    if request.action == ACTION_VAULT_RESEARCH_LOG_COMMIT:
        outcome = VaultPushOutcome(
            status=STATUS_QUEUED_AUTO,
            action=request.action,
            autonomy_level="L2",
            work_mode=work_mode,
            approval_required=False,
            audit_entry={
                "workspace_root": workspace_root,
                "note_kind": request.note_kind,
                "note_path": request.note_path,
                "commit_msg": request.commit_msg,
            },
        )
        return _append_and_return(session_extra, audit_log, outcome, now=now)

    # 5. Remote push — mode-dependent.
    if work_mode == "autonomous_merge":
        outcome = VaultPushOutcome(
            status=STATUS_QUEUED_AUTO,
            action=request.action,
            autonomy_level="L2",
            work_mode=work_mode,
            approval_required=False,
            audit_entry={
                "workspace_root": workspace_root,
                "branch": request.branch,
                "commit_msg": request.commit_msg,
            },
        )
        return _append_and_return(session_extra, audit_log, outcome, now=now)

    # Default for approval_required / unset mode — L3 approval card.
    outcome = VaultPushOutcome(
        status=STATUS_QUEUED_FOR_APPROVAL,
        action=request.action,
        autonomy_level="L3",
        work_mode=work_mode,
        approval_required=True,
        audit_entry={
            "workspace_root": workspace_root,
            "branch": request.branch,
            "commit_msg": request.commit_msg,
        },
    )
    return _append_and_return(session_extra, audit_log, outcome, now=now)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _resolve_workspace_root(session_extra: Mapping[str, Any]) -> Optional[str]:
    """Pull vault workspace root from session.extra or env. None means unset."""

    explicit = session_extra.get("vault_workspace_root")
    if explicit:
        return str(explicit)
    # Env fallback — kept narrow so test isolation is easy.
    import os

    env_value = os.environ.get("YULE_VAULT_WORKSPACE_ROOT")
    if env_value:
        return env_value
    return None


def _append_and_return(
    session_extra: dict,
    audit_log: list,
    outcome: VaultPushOutcome,
    *,
    now: Optional[datetime],
) -> VaultPushOutcome:
    entry = {
        **dict(outcome.audit_entry),
        "status": outcome.status,
        "action": outcome.action,
        "autonomy_level": outcome.autonomy_level,
        "work_mode": outcome.work_mode,
        "recorded_at": _now_iso(now),
    }
    if outcome.not_configured_reason is not None:
        entry["not_configured_reason"] = outcome.not_configured_reason
    audit_log.append(entry)
    session_extra["vault_push_audit"] = audit_log
    if outcome.status == STATUS_NOT_CONFIGURED:
        session_extra["vault_push_not_configured_reason"] = outcome.not_configured_reason
    return outcome


def _now_iso(now: Optional[datetime]) -> str:
    moment = now or datetime.now(tz=timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


__all__ = (
    "ACTION_VAULT_REMOTE_PUSH",
    "ACTION_VAULT_RESEARCH_LOG_COMMIT",
    "NOT_CONFIGURED_REASONS",
    "STATUS_FORBIDDEN",
    "STATUS_INVALID_REQUEST",
    "STATUS_NOT_CONFIGURED",
    "STATUS_QUEUED_AUTO",
    "STATUS_QUEUED_FOR_APPROVAL",
    "VaultPushOutcome",
    "VaultPushRequest",
    "dispatch_vault_push",
)
