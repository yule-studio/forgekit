"""GitHub sync — *plan only*, no direct push.

This module is a hard rail: when the engineering-knowledge collector
finishes a daily sweep, the operator's vault has new notes, but those
notes do **NOT** get pushed to a GitHub repository in this task. The
G-task spec is explicit:

  * "main 직접 push 금지."
  * "실제 GitHub push/PR 생성은 이번 범위 밖이다."
  * "GitHub sync는 pending/plan/audit만 남긴다."

What we *do* produce here:

  1. :class:`PendingGitSyncPlan` — a structured plan describing which
     vault notes should land in which (future) GitHub PR. The plan
     names a docs-only branch (``docs/eng-knowledge/<role>/<date>``)
     and tags the request type as ``docs_only_sync_plan`` so the
     downstream G6 gateway / G3 executor can pick it up.
  2. An audit row marking the plan as ``status="pending"`` so a
     supervisor sweep can list "what hasn't been pushed yet".

The plan structure is also designed to feed
``agents.github_workos.triage`` later — the ``approval_required=True``
flag on every plan ensures the gateway will route through the L3
approval surface even if the collector ever runs unattended.

No GitHub API call. No env / token / private-key access. The
``GithubAppInterface`` Protocol is just a structural placeholder so
the executor in G3 can satisfy it in the future without forcing this
module to import the workos package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Mapping, Optional, Protocol, Sequence, Tuple

from .models import EngineeringKnowledgeItem


# ---------------------------------------------------------------------------
# Protocols (structural — no import from github_workos here)
# ---------------------------------------------------------------------------


class GithubAppInterface(Protocol):
    """Structural placeholder for the eventual GitHub App writer.

    Implementations live in ``agents.github_workos``. This module
    refuses to call any of them — it only emits a plan that records
    *intent* to push.
    """

    def supports_docs_only_sync(self) -> bool: ...


# ---------------------------------------------------------------------------
# Plan + audit shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PendingGitSyncFile:
    """One vault note that needs to land in the future PR."""

    topic_key: str
    role: str
    title: str
    proposed_path: str
    rationale: str = ""

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "topic_key": self.topic_key,
            "role": self.role,
            "title": self.title,
            "proposed_path": self.proposed_path,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class PendingGitSyncPlan:
    """Plan-only record for a future docs-only PR.

    The plan is a *contract*, not an action. It records:

      * which role this batch belongs to,
      * which target branch the gateway should create,
      * which target base branch the future PR should target (NEVER
        ``main``/``master``/``prod``/``release`` directly — the
        gateway must open a PR against a non-protected base or rely
        on the operator to merge),
      * the file list,
      * the request_type tag (``docs_only_sync_plan``) so audit logs
        and downstream routing can pivot on it,
      * the ``approval_required=True`` flag so this can never become
        an automatic push.
    """

    plan_id: str
    role: str
    target_branch: str
    target_base_branch: str
    request_type: str
    files: Tuple[PendingGitSyncFile, ...]
    approval_required: bool = True
    direct_push_to_main: bool = False  # always False — set explicitly
    status: str = "pending"
    created_at: str = ""
    rejected_reasons: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()
    # Optional structural reference; never invoked from this module.
    github_app_iface: Optional[GithubAppInterface] = None

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "plan_id": self.plan_id,
            "role": self.role,
            "target_branch": self.target_branch,
            "target_base_branch": self.target_base_branch,
            "request_type": self.request_type,
            "files": [f.to_payload() for f in self.files],
            "approval_required": self.approval_required,
            "direct_push_to_main": self.direct_push_to_main,
            "status": self.status,
            "created_at": self.created_at,
            "rejected_reasons": list(self.rejected_reasons),
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_PROTECTED_BASES = ("main", "master", "prod", "production", "release")


def _validate_base_branch(base: str) -> Tuple[str, Tuple[str, ...]]:
    """Return ``(safe_base, reasons)``.

    If *base* names a protected branch, downgrade to a safe sentinel
    (``"unset-safe-base"``) and record the rejection reason. The
    plan still gets emitted — operators can see the violation in the
    audit and fix the wiring.
    """

    name = (base or "").strip().lower()
    if name.startswith("refs/heads/"):
        name = name[len("refs/heads/") :]
    if name in _PROTECTED_BASES:
        return ("unset-safe-base", (f"protected_base_branch_rejected:{name}",))
    if not name:
        return ("unset-safe-base", ("base_branch_missing",))
    return (name, ())


def _utc_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _proposed_path(item: EngineeringKnowledgeItem, *, layout: str) -> str:
    safe_topic = item.topic_key.replace("/", "-").replace(" ", "-") or "untitled"
    role_dir = item.role
    if layout == "yule-agent-vault":
        return (
            f"05-engineering/knowledge/{role_dir}/"
            f"{item.collected_at.split('T', 1)[0]}-{safe_topic}.md"
        )
    return (
        f"engineering/knowledge/{role_dir}/"
        f"{item.collected_at.split('T', 1)[0]}-{safe_topic}.md"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_pending_git_sync_plan(
    role_id: str,
    items: Sequence[EngineeringKnowledgeItem],
    *,
    target_base_branch: str = "develop",
    target_branch_prefix: str = "docs/eng-knowledge",
    today: str = "",
    layout: str = "yule-agent-vault",
    plan_id: str = "",
    notes: Sequence[str] = (),
) -> PendingGitSyncPlan:
    """Build the pending sync plan for *items*.

    The function never raises on a protected base — it downgrades
    the base to a safe sentinel and records the rejection reason on
    the plan so the audit captures operator misconfiguration loudly.
    """

    safe_base, base_reasons = _validate_base_branch(target_base_branch)
    today = today or _utc_iso().split("T", 1)[0]
    target_branch = f"{target_branch_prefix}/{role_id}/{today}"

    files = tuple(
        PendingGitSyncFile(
            topic_key=item.topic_key,
            role=item.role,
            title=item.title,
            proposed_path=_proposed_path(item, layout=layout),
            rationale=f"engineering-knowledge L1 auto-save (importance={item.importance.value})",
        )
        for item in items
    )

    final_id = plan_id or (
        f"eng-knowledge-sync:{role_id}:{today}:{len(files)}"
    )

    return PendingGitSyncPlan(
        plan_id=final_id,
        role=role_id,
        target_branch=target_branch,
        target_base_branch=safe_base,
        request_type="docs_only_sync_plan",
        files=files,
        approval_required=True,
        direct_push_to_main=False,
        status="pending",
        created_at=_utc_iso(),
        rejected_reasons=base_reasons,
        notes=tuple(notes),
    )


def build_pending_audit(plan: PendingGitSyncPlan) -> Mapping[str, Any]:
    """Audit row that says 'this plan is pending; nothing was pushed'."""

    return {
        "action": "engineering_knowledge_github_sync",
        "outcome": "plan_pending_no_push",
        "plan_id": plan.plan_id,
        "role": plan.role,
        "target_branch": plan.target_branch,
        "target_base_branch": plan.target_base_branch,
        "approval_required": plan.approval_required,
        "direct_push_to_main": plan.direct_push_to_main,
        "file_count": len(plan.files),
        "rejected_reasons": list(plan.rejected_reasons),
        "summary": (
            f"docs_only_sync_plan pending — role={plan.role} files={len(plan.files)} "
            f"branch={plan.target_branch} base={plan.target_base_branch}"
        ),
    }


__all__ = [
    "GithubAppInterface",
    "PendingGitSyncFile",
    "PendingGitSyncPlan",
    "build_pending_audit",
    "build_pending_git_sync_plan",
]
