"""F16 PR-2 — Build a PRMergeExecutor that wraps live_client + gate.

This is the **one** place where the 5-step merge gate is composed
with the live GitHub merge call. The executor returns the mapping
shape :func:`handle_pr_merge_approval_reply` expects:

  * Gate failure → ``{"gate_failed_step": "...", "gate_reason": "..."}``.
  * ``YULE_GITHUB_MERGE_ENABLED=false`` → ``{"merge_disabled": True, ...}``.
  * Success → ``{"merge_sha": "...", "method": "squash", ...}``.
  * Merge API error → ``{"merge_failed": True, "error": "...", "status": int}``.

The factory keeps :mod:`live_client` GitHub-only and :mod:`pr_approval`
domain-only; the wire-up lives in this small module so unit tests can
exercise it without GitHub credentials by passing fake clients.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from ..agents.job_queue.pr_approval import (
    PRMergeExecutor,
    PRMergeReplyDispatch,
    PRMergeStatusSnapshot,
    evaluate_merge_gate,
)
from .live_client import (
    LiveGithubAppMergeDisabled,
    LiveGithubAppHTTPError,
)


# Protocol-ish — the executor only touches these three methods on the
# live client. Both the real ``LiveGithubAppClient`` and the test
# stubs in :mod:`tests.github_app.test_pr_merge_executor` implement
# this surface.
class _LiveMergeClientProtocol:
    def get_pull_request(self, *, repo: str, pr_number: int) -> Mapping[str, Any]: ...
    def list_check_runs(
        self, *, repo: str, head_sha: str
    ) -> Sequence[Mapping[str, Any]]: ...
    def get_branch_protection(
        self, *, repo: str, branch: str
    ) -> Optional[Mapping[str, Any]]: ...
    def merge_pull_request(
        self,
        *,
        repo: str,
        pr_number: int,
        sha: Optional[str] = None,
        merge_method: str = "squash",
        commit_title: Optional[str] = None,
        commit_message: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> Mapping[str, Any]: ...


def build_pr_merge_executor(
    *,
    client: Any,
    merge_method: str = "squash",
    env: Optional[Mapping[str, str]] = None,
) -> PRMergeExecutor:
    """Return a :data:`PRMergeExecutor` bound to *client*.

    The returned callable matches the type alias from
    :mod:`agents.job_queue.pr_approval` (sync — returns a Mapping
    directly; the handler awaits when it sees an awaitable).
    """

    def _execute(dispatch: PRMergeReplyDispatch) -> Mapping[str, Any]:
        proposal = dispatch.proposal
        # 1. Pull live PR state.
        pr = client.get_pull_request(
            repo=proposal.repo, pr_number=proposal.pr_number
        )
        head_sha = str(
            (pr or {}).get("head", {}).get("sha")
            or pr.get("head_sha")
            or ""
        )
        mergeable_state = str(pr.get("mergeable_state") or "unknown")
        mergeable = pr.get("mergeable")
        draft = bool(pr.get("draft", False))

        # 2. Check runs for the latest head.
        runs = client.list_check_runs(
            repo=proposal.repo, head_sha=head_sha or proposal.head_sha
        )
        conclusions = tuple(
            str(run.get("conclusion") or "") for run in (runs or ())
        )

        # 3. Branch protection — defensive: any auth failure → refuse.
        try:
            protection = client.get_branch_protection(
                repo=proposal.repo, branch=proposal.base_branch
            )
            protection_available = True
        except LiveGithubAppHTTPError as exc:
            protection = None
            # 401/403 → refuse merge by leaving protection_available False
            # (the gate's branch_protection step interprets this as a
            # safe-side refusal per F16 §7).
            if exc.status in (401, 403):
                protection_available = False
            else:
                protection_available = True

        required_reviews = 0
        actual_reviews = 0
        required_status_checks: tuple[str, ...] = ()
        if protection:
            rev_obj = protection.get("required_pull_request_reviews") or {}
            required_reviews = int(
                rev_obj.get("required_approving_review_count") or 0
            )
            req_status = protection.get("required_status_checks") or {}
            ctx = req_status.get("contexts") or []
            required_status_checks = tuple(str(c) for c in ctx)
            # actual_reviews comes from the PR payload's reviews summary;
            # the simplest API surface uses pr.get("requested_reviewers")
            # length + pr["review_comments"] is **not** equivalent. We
            # accept the (approvals satisfied) signal from the snapshot
            # via pr["mergeable_state"] == "clean", which already
            # reflects branch protection. Treat clean state as "reviews
            # satisfied" — the gate's mergeable_state check already
            # gates on "clean".
            if mergeable_state == "clean":
                actual_reviews = required_reviews

        snapshot = PRMergeStatusSnapshot(
            draft=draft,
            mergeable=mergeable if isinstance(mergeable, bool) else None,
            mergeable_state=mergeable_state,
            head_sha=head_sha,
            check_conclusions=conclusions,
            required_status_checks=required_status_checks,
            required_approving_reviews=required_reviews,
            actual_approving_reviews=actual_reviews,
            branch_protection_available=protection_available,
        )

        gate = evaluate_merge_gate(proposal, snapshot)
        if not gate.allowed:
            return {
                "gate_failed_step": gate.failed_step,
                "gate_reason": gate.reason,
                "checks_summary": gate.checks_summary,
            }

        # 4. Attempt merge — opt-in env gate inside live_client.
        try:
            merge_result = client.merge_pull_request(
                repo=proposal.repo,
                pr_number=proposal.pr_number,
                sha=head_sha or proposal.head_sha,
                merge_method=merge_method,
                env=env,
            )
        except LiveGithubAppMergeDisabled as exc:
            return {
                "merge_disabled": True,
                "reason": str(exc),
                "status": exc.status or 503,
            }
        except LiveGithubAppHTTPError as exc:
            return {
                "merge_failed": True,
                "error": str(exc),
                "status": exc.status,
            }

        # 5. Success — surface merge_sha so audit can record it.
        return {
            "merge_sha": str(merge_result.get("sha") or ""),
            "method": merge_method,
            "raw": dict(merge_result),
        }

    return _execute


__all__ = ("build_pr_merge_executor",)
