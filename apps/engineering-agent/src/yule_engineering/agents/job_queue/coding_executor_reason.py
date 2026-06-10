"""Reason classification for the coding executor worker.

Pure-data + pure-function home for the ``coding_execute`` reason tokens
and the small classification helpers (`is_protected_branch`,
`_tests_passed`, `_short`). Split out of
:mod:`coding_executor_worker` (#73 follow-up) so the worker keeps the
``process_job`` pipeline while the *failure / outcome reason* vocabulary
lives in one cohesive, side-effect-free module.

The worker re-exports every public symbol below, so existing importers
(``from .coding_executor_worker import REASON_TEST_FAILED`` etc.) keep
working unchanged. This module imports *nothing* from the worker — one
way dependency, no cycle.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Mapping

from ..governance.runtime_policy import derive_standard_branch_name

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .coding_executor_worker import CodingExecuteRequest


# ---------------------------------------------------------------------------
# Service / job-type identity
# ---------------------------------------------------------------------------


JOB_TYPE_CODING_EXECUTE: str = "coding_execute"
SERVICE_ID_CODING_EXECUTOR: str = "eng-coding-executor"


# ---------------------------------------------------------------------------
# Outcome / skip reasons
# ---------------------------------------------------------------------------


# Outcome reasons surfaced via :class:`CodingExecuteOutcome`.
SKIPPED_DUPLICATE: str = "duplicate_in_flight"
SKIPPED_CLAIMED: str = "claimed_by_other_worker"
SKIPPED_VAULT_UNAVAILABLE: str = "worktree_root_unavailable"

REASON_PROTECTED_BRANCH: str = "protected_branch_blocked"
REASON_BRANCH_POLICY_VIOLATION: str = "branch_policy_violation"
REASON_FORCE_PUSH_BLOCKED: str = "force_push_blocked"
REASON_DRY_RUN: str = "dry_run"
REASON_TEST_FAILED: str = "test_failed"
REASON_PUSH_FAILED: str = "push_failed"
REASON_PR_FAILED: str = "draft_pr_failed"
REASON_EDIT_FAILED: str = "edit_failed"
REASON_COMMIT_FAILED: str = "commit_failed"
REASON_NOT_IMPLEMENTED: str = "executor_not_wired_yet"
REASON_INVALID_REQUEST: str = "invalid_request"
# P1-B — worktree / target repo specific failures (generic subprocess
# exit 255 대신 operator 가 즉시 이해 가능한 token 으로 분기).
REASON_TARGET_REPO_MISSING: str = "target_repo_checkout_missing"
REASON_WORKTREE_FAILED: str = "worktree_provision_failed"
# P1-G — repo 가 detectable stack 이 하나도 없어 ``test_failed`` 가
# 의미 없음. record-only editor + greenfield 조합 이면 sub-reason 에
# editor capability 정보까지 포함 (e.g.
# ``bootstrap_required:empty_or_greenfield_repo+editor_record_only_insufficient``).
REASON_BOOTSTRAP_REQUIRED: str = "bootstrap_required"
# P1-M F — non-greenfield + record-only editor + ``YULE_CODING_EXECUTOR_
# PLANNING_ONLY_PR_FORBIDDEN=1`` 일 때. planning-only PR 가 production 까지
# 반복되는 회귀를 차단하기 위한 honest blocker.
REASON_NON_GREENFIELD_REAL_EDIT_UNAVAILABLE: str = (
    "non_greenfield_real_edit_unavailable"
)


# ---------------------------------------------------------------------------
# Branch protection classification
# ---------------------------------------------------------------------------


_PROTECTED_BRANCH_NAMES: frozenset[str] = frozenset(
    {"main", "master", "develop", "dev", "prod", "release"}
)


def is_protected_branch(name: str) -> bool:
    """Return True for canonically protected branches.

    Mirrors the more conservative subset of
    :func:`agents.github_workos.branching.is_protected_branch` — the
    full check includes regex-shaped runtime-managed branches and is
    deferred to that module when the executor wires through G3.
    """

    if not name:
        return True
    candidate = str(name).strip().lower()
    if candidate in _PROTECTED_BRANCH_NAMES:
        return True
    if candidate.startswith("release/") or candidate.startswith("hotfix/"):
        return True
    return False


# ---------------------------------------------------------------------------
# Test-result + error classification
# ---------------------------------------------------------------------------


def _tests_passed(summary: Mapping[str, Any]) -> bool:
    if not isinstance(summary, Mapping):
        return False
    if summary.get("dry_run"):
        return True
    status = str(summary.get("status") or "").lower()
    if status in {"ok", "passed", "success"}:
        return True
    if status in {"failed", "fail", "error"}:
        return False
    # Unknown status — fall back to failures count if present.
    if "failures" in summary:
        return summary.get("failures") in (0, "0", "")
    if "failed" in summary:
        return summary.get("failed") in (0, "0", "")
    return False


def _short(exc: BaseException) -> str:
    text = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    return f"{type(exc).__name__}: {text}"[:200]


def suggest_branch(request: "CodingExecuteRequest") -> str:
    """Return a branch name when caller didn't pass a hint.

    Default convention: ``agent/<role>/issue-<n>-coding-execute`` —
    기존 G3 호환. issue 가 있으면 추가로 :func:`derive_standard_branch_name`
    의 표준 prefix (`feat/`) 로 안전 fallback 도 제공 (request 의
    metadata['use_standard_prefix'] 가 True 일 때).
    """

    metadata = request.metadata or {}
    use_standard = False
    if isinstance(metadata, Mapping):
        use_standard = bool(metadata.get("use_standard_prefix"))

    if use_standard and request.issue_number is not None:
        short = (
            request.executor_role.split("-", 1)[0]
            if request.executor_role
            else "work"
        )
        return derive_standard_branch_name(
            kind="feat",
            short_purpose=short,
            issue_number=int(request.issue_number),
        )
    if request.issue_number is not None:
        return f"agent/{request.executor_role}/issue-{int(request.issue_number)}-coding-execute"
    ts = int((time.time())) % 10_000
    return f"agent/{request.executor_role}/coding-execute-{ts}"


__all__ = (
    "JOB_TYPE_CODING_EXECUTE",
    "SERVICE_ID_CODING_EXECUTOR",
    "SKIPPED_DUPLICATE",
    "SKIPPED_CLAIMED",
    "SKIPPED_VAULT_UNAVAILABLE",
    "REASON_PROTECTED_BRANCH",
    "REASON_BRANCH_POLICY_VIOLATION",
    "REASON_FORCE_PUSH_BLOCKED",
    "REASON_DRY_RUN",
    "REASON_TEST_FAILED",
    "REASON_PUSH_FAILED",
    "REASON_PR_FAILED",
    "REASON_EDIT_FAILED",
    "REASON_COMMIT_FAILED",
    "REASON_NOT_IMPLEMENTED",
    "REASON_INVALID_REQUEST",
    "REASON_TARGET_REPO_MISSING",
    "REASON_WORKTREE_FAILED",
    "REASON_BOOTSTRAP_REQUIRED",
    "REASON_NON_GREENFIELD_REAL_EDIT_UNAVAILABLE",
    "is_protected_branch",
    "suggest_branch",
)
