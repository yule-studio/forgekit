"""Tracking enforcement — P0-I stage 3 (#141).

Enforces the *implementation chain* declared in stage-1 policy
``policies/runtime/agents/engineering-agent/github-workflow.md`` §1~§5
and the work-mode policy in ``docs/autonomy-policy.md §0``:

    issue → branch → commit → PR → merge

Each stage of the chain has *required tracking metadata* in
``session.extra``. When metadata is missing, the gateway halts the
session in a draft / planning state instead of letting the executor
run with "no idea which PR this commit belongs to".

The validator is **read-only** — it never mutates session state. It
returns a :class:`TrackingValidation` describing what's present and
what's blocking. Caller decides how to surface (Discord warning,
session.extra append, status diagnostic line).

Two stage-1 policies inform the enforcement:

  * ``repo-contract-discovery.md §4`` — when the target repo's
    ``RepoContract`` allows *direct PR* (e.g. no issue required by
    repo convention), the absence of an ``issue`` reference is not
    blocking.
  * ``autonomy-policy.md §0`` — ``work_mode=autonomous_merge`` only
    permits merge after the full chain is satisfied. ``approval_required``
    additionally requires a ``coding_handoff_packet`` so the
    approval reviewer has the canonical envelope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Tuple


# Status codes emitted by validate_tracking_chain. Stable identifiers
# so tests / status surface match without depending on Korean strings.
STATUS_OK = "ok"
STATUS_NEEDS_ISSUE = "needs_issue"
STATUS_NEEDS_BRANCH = "needs_branch"
STATUS_NEEDS_PR = "needs_pr"
STATUS_NEEDS_HANDOFF_PACKET = "needs_handoff_packet"
STATUS_NEEDS_MODE = "needs_mode"
STATUS_STANDALONE_NO_TARGET = "standalone_no_target"

STATUSES = (
    STATUS_OK,
    STATUS_NEEDS_ISSUE,
    STATUS_NEEDS_BRANCH,
    STATUS_NEEDS_PR,
    STATUS_NEEDS_HANDOFF_PACKET,
    STATUS_NEEDS_MODE,
    STATUS_STANDALONE_NO_TARGET,
)


@dataclass(frozen=True)
class TrackingValidation:
    """Outcome of :func:`validate_tracking_chain`.

    ``status`` — one of the STATUS_* constants.
    ``blocked`` — True when the executor must halt at draft / planning.
    ``blocked_reason`` — short Korean message for Discord / status.
    ``missing_links`` — ordered list of chain segments that are absent.
    ``allowed_via_contract_exception`` — True when RepoContract grants
    an exception (e.g. direct-PR repos don't require an issue link).
    ``next_action`` — caller hint for what to do (``open_issue`` /
    ``open_pr_branch`` / ``proceed`` / ``ask_user``).
    """

    status: str
    blocked: bool
    blocked_reason: Optional[str] = None
    missing_links: Tuple[str, ...] = ()
    allowed_via_contract_exception: bool = False
    next_action: str = "proceed"
    # Captured tracking summary — what *was* found, for status surface.
    has_github_target: bool = False
    has_issue: bool = False
    has_branch: bool = False
    has_pull_request: bool = False
    has_handoff_packet: bool = False
    has_mode: bool = False

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "status": self.status,
            "blocked": self.blocked,
            "blocked_reason": self.blocked_reason,
            "missing_links": list(self.missing_links),
            "allowed_via_contract_exception": self.allowed_via_contract_exception,
            "next_action": self.next_action,
            "has_github_target": self.has_github_target,
            "has_issue": self.has_issue,
            "has_branch": self.has_branch,
            "has_pull_request": self.has_pull_request,
            "has_handoff_packet": self.has_handoff_packet,
            "has_mode": self.has_mode,
        }

    def status_summary_line(self) -> str:
        """One-line summary for status diagnostic surface."""

        if self.status == STATUS_OK:
            return "✅ tracking chain complete"
        if self.status == STATUS_STANDALONE_NO_TARGET:
            return "ℹ️ tracking chain: GitHub target 없음 (research/discussion only)"
        missing = ", ".join(self.missing_links) if self.missing_links else "unknown"
        flag = "⚠️" if self.blocked else "ℹ️"
        suffix = (
            " (RepoContract 예외 적용)"
            if self.allowed_via_contract_exception
            else ""
        )
        return f"{flag} tracking chain: missing {missing}{suffix}"


# ---------------------------------------------------------------------------
# Validation entry point
# ---------------------------------------------------------------------------


def validate_tracking_chain(extra: Mapping[str, Any]) -> TrackingValidation:
    """Inspect ``session.extra`` and report tracking-chain completeness.

    Algorithm:

      1. If no ``github_target`` in extra at all → "standalone" — the
         user is doing research/discussion, not coding. Not blocked.
      2. If ``github_target`` is a repo-root URL → caller hasn't picked
         an issue/PR yet. Block with ``needs_issue`` unless RepoContract
         explicitly allows direct-PR (no issue required).
      3. If ``github_target`` is issue / pr → walk the chain forward
         (issue must have branch + PR for merge eligibility).
      4. ``work_mode`` must be decided (stage-1 ask-once contract).
      5. ``approval_required`` mode additionally requires
         ``coding_handoff_packet`` for the reviewer card.

    Never raises. Returns a :class:`TrackingValidation` even for
    invalid input.
    """

    if not isinstance(extra, Mapping):
        return TrackingValidation(
            status=STATUS_STANDALONE_NO_TARGET,
            blocked=False,
            next_action="proceed",
        )

    github_target = extra.get("github_target")
    if not isinstance(github_target, Mapping) or not github_target:
        # No GitHub target at all — research / discussion mode, not blocked.
        return TrackingValidation(
            status=STATUS_STANDALONE_NO_TARGET,
            blocked=False,
            next_action="proceed",
            has_github_target=False,
            has_mode=bool(extra.get("work_mode")),
            has_handoff_packet=bool(extra.get("coding_handoff_packet")),
        )

    kind = str(github_target.get("kind") or "")
    has_issue = kind == "issue"
    has_pr = kind == "pull_request"
    has_branch = bool(
        extra.get("branch_name")
        or github_target.get("branch_or_sha")
        or has_pr  # PR implies a branch exists
    )
    work_mode = extra.get("work_mode")
    has_mode = bool(work_mode)
    has_packet = bool(extra.get("coding_handoff_packet"))

    summary = dict(
        has_github_target=True,
        has_issue=has_issue,
        has_branch=has_branch,
        has_pull_request=has_pr,
        has_mode=has_mode,
        has_handoff_packet=has_packet,
    )

    # Stage 0 — mode must be decided (stage-1 ask-once).
    if not has_mode:
        return TrackingValidation(
            status=STATUS_NEEDS_MODE,
            blocked=True,
            blocked_reason=(
                "이번 세션의 work_mode 가 정해지지 않아 작업을 시작할 수 없어요. "
                "`자율 머지` 또는 `승인 필요` 중 하나로 답해 주세요."
            ),
            missing_links=("work_mode",),
            next_action="ask_user",
            **summary,
        )

    # Stage 1 — handoff packet required for approval_required mode.
    if work_mode == "approval_required" and not has_packet:
        return TrackingValidation(
            status=STATUS_NEEDS_HANDOFF_PACKET,
            blocked=True,
            blocked_reason=(
                "approval_required 모드에서는 reviewer 가 볼 coding_handoff_packet "
                "이 비어 있어 승인 카드를 만들 수 없어요. gateway 가 packet "
                "을 다시 만들도록 메시지를 다시 보내 주세요."
            ),
            missing_links=("coding_handoff_packet",),
            next_action="ask_user",
            **summary,
        )

    # Stage 2 — repo-root target: must escalate to issue or PR.
    if kind == "repo":
        contract_exception = _allows_direct_pr(extra)
        if contract_exception:
            return TrackingValidation(
                status=STATUS_OK,
                blocked=False,
                next_action="open_pr_branch",
                allowed_via_contract_exception=True,
                **summary,
            )
        return TrackingValidation(
            status=STATUS_NEEDS_ISSUE,
            blocked=True,
            blocked_reason=(
                "추적 가능한 issue 가 없어 작업을 진행할 수 없어요. "
                "먼저 GitHub issue 를 열고 해당 issue URL 을 다시 알려주세요."
            ),
            missing_links=("issue",),
            next_action="open_issue",
            **summary,
        )

    # Stage 3 — issue target: must have a branch (and eventually a PR
    # for merge eligibility). Issue → branch missing → "open_pr_branch".
    if kind == "issue":
        if not has_branch:
            return TrackingValidation(
                status=STATUS_NEEDS_BRANCH,
                blocked=True,
                blocked_reason=(
                    "issue 는 있지만 작업 branch 가 아직 없어요. "
                    "feature branch 를 만들고 다시 시작해 주세요."
                ),
                missing_links=("branch",),
                next_action="open_pr_branch",
                **summary,
            )
        # Issue + branch is enough to begin implementation; PR will be
        # opened later. Not blocked.
        return TrackingValidation(
            status=STATUS_OK,
            blocked=False,
            next_action="open_pr_branch",
            **summary,
        )

    # Stage 4 — PR target: chain complete enough to implement/merge.
    if kind == "pull_request":
        return TrackingValidation(
            status=STATUS_OK,
            blocked=False,
            next_action="proceed",
            **summary,
        )

    # Other kinds (commit / compare / tree / blob) — informational only,
    # never blocks. Caller decides what to do.
    return TrackingValidation(
        status=STATUS_OK,
        blocked=False,
        next_action="proceed",
        **summary,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _allows_direct_pr(extra: Mapping[str, Any]) -> bool:
    """Stage-1 §4 — does the discovered RepoContract permit direct PR (no issue)?

    Two signals:
      * ``repo_contract.branch_strategy in {"trunk-based", "github-flow"}``
        — these conventions don't require an issue before a PR.
      * ``repo_contract.workflows`` contains no ``issue_required`` hint
        (heuristic — kept conservative; default False).
    """

    contract = extra.get("repo_contract")
    if not isinstance(contract, Mapping) or not contract:
        return False
    if contract.get("fallback"):
        return False
    strategy = (contract.get("branch_strategy") or "").lower()
    if strategy in ("trunk-based", "github-flow"):
        return True
    return False


__all__ = (
    "STATUS_NEEDS_BRANCH",
    "STATUS_NEEDS_HANDOFF_PACKET",
    "STATUS_NEEDS_ISSUE",
    "STATUS_NEEDS_MODE",
    "STATUS_NEEDS_PR",
    "STATUS_OK",
    "STATUS_STANDALONE_NO_TARGET",
    "STATUSES",
    "TrackingValidation",
    "validate_tracking_chain",
)
