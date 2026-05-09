"""Coding executor worker — Phase 1 of #73 tech-lead runtime loop.

Consumes ``coding_execute`` jobs that the gateway enqueues when a
:class:`CodingJob` reaches ``status="ready"``. Drives the canonical 7-step
pipeline (worktree → edit → test → commit → push → draft PR → state
transition) via injected Protocol seams so this PR can land the contract
without forcing live executor / GitHub App / shell wiring.

Hard rails (worker-level, *cannot* be relaxed by injection):

  * `is_protected_branch` rejects ``main`` / ``master`` / ``develop`` /
    ``dev`` / ``prod`` / ``release`` (and any name marked protected by
    :mod:`agents.github_workos.branching`). protected branch push lands
    as ``FAILED_TERMINAL`` with ``reason="protected_branch_blocked"``.
  * `force_push` request (regardless of `Pusher` impl) is rejected — the
    executor never sets ``force=True`` and the Pusher Protocol does not
    expose a force flag.
  * Authorization headers / pem / installation tokens are never logged.
    All errors run through :func:`agents.github_app.doctor.redact_secret_like`.

The actual `claude` / `codex` / `gh` invocations live in *injected*
Protocol implementations (`WorktreeProvisioner` / `CodeEditor` /
`TestRunner` / `Committer` / `Pusher` / `DraftPRCreator`). The default
implementations are deliberately :class:`NotImplementedRunner` — `--live`
wiring belongs to a follow-up PR (D-73-2).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (
    Any,
    Awaitable,
    Callable,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

from .heartbeat import HeartbeatStore
from .state_machine import JobState
from .store import Job, JobQueue


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


JOB_TYPE_CODING_EXECUTE: str = "coding_execute"
SERVICE_ID_CODING_EXECUTOR: str = "eng-coding-executor"


# Outcome reasons surfaced via :class:`CodingExecuteOutcome`.
SKIPPED_DUPLICATE: str = "duplicate_in_flight"
SKIPPED_CLAIMED: str = "claimed_by_other_worker"
SKIPPED_VAULT_UNAVAILABLE: str = "worktree_root_unavailable"

REASON_PROTECTED_BRANCH: str = "protected_branch_blocked"
REASON_FORCE_PUSH_BLOCKED: str = "force_push_blocked"
REASON_DRY_RUN: str = "dry_run"
REASON_TEST_FAILED: str = "test_failed"
REASON_PUSH_FAILED: str = "push_failed"
REASON_PR_FAILED: str = "draft_pr_failed"
REASON_EDIT_FAILED: str = "edit_failed"
REASON_COMMIT_FAILED: str = "commit_failed"
REASON_NOT_IMPLEMENTED: str = "executor_not_wired_yet"
REASON_INVALID_REQUEST: str = "invalid_request"


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
# Request payload
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CodingExecuteRequest:
    """Strongly-typed payload for a ``coding_execute`` queue row.

    The payload mirrors :class:`agents.coding.job.CodingJob` but
    flattens fields the executor actually needs (e.g. ``base_branch``)
    and drops fields that only matter to the authorization layer.
    """

    session_id: str
    executor_role: str
    user_request: str
    generated_prompt: str
    write_scope: Tuple[str, ...]
    forbidden_scope: Tuple[str, ...]
    safety_rules: Tuple[str, ...]
    base_branch: str = "main"
    branch_hint: str = ""
    repo_full_name: str = ""
    issue_number: Optional[int] = None
    dry_run: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "CodingExecuteRequest":
        return cls(
            session_id=str(payload.get("session_id") or ""),
            executor_role=str(payload.get("executor_role") or ""),
            user_request=str(payload.get("user_request") or ""),
            generated_prompt=str(payload.get("generated_prompt") or ""),
            write_scope=tuple(
                str(p) for p in (payload.get("write_scope") or ()) if str(p).strip()
            ),
            forbidden_scope=tuple(
                str(p) for p in (payload.get("forbidden_scope") or ()) if str(p).strip()
            ),
            safety_rules=tuple(
                str(p) for p in (payload.get("safety_rules") or ()) if str(p).strip()
            ),
            base_branch=str(payload.get("base_branch") or "main"),
            branch_hint=str(payload.get("branch_hint") or ""),
            repo_full_name=str(payload.get("repo_full_name") or ""),
            issue_number=_coerce_int(payload.get("issue_number")),
            dry_run=bool(payload.get("dry_run", True)),
            metadata=dict(payload.get("metadata") or {}),
        )

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "session_id": self.session_id,
            "executor_role": self.executor_role,
            "user_request": self.user_request,
            "generated_prompt": self.generated_prompt,
            "write_scope": list(self.write_scope),
            "forbidden_scope": list(self.forbidden_scope),
            "safety_rules": list(self.safety_rules),
            "base_branch": self.base_branch,
            "branch_hint": self.branch_hint,
            "repo_full_name": self.repo_full_name,
            "issue_number": self.issue_number,
            "dry_run": self.dry_run,
            "metadata": dict(self.metadata),
        }


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CodingExecuteOutcome:
    """Result of one ``coding_execute`` run.

    ``terminal_state`` is the ``JobState`` value the worker landed in
    (``SAVED`` for happy / dry-run path, ``FAILED_TERMINAL`` for hard
    rail violations, ``FAILED_RETRYABLE`` for transient).
    """

    job: Optional[Job]
    terminal_state: Optional[str] = None
    skipped_reason: Optional[str] = None
    failure_reason: Optional[str] = None
    branch: Optional[str] = None
    commit_sha: Optional[str] = None
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    test_summary: Optional[Mapping[str, Any]] = None
    audit_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Protocol seams
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorktreeContext:
    """Hand-off carried between pipeline steps.

    Fields are populated incrementally — ``branch`` / ``worktree_path``
    after `WorktreeProvisioner.provision`, ``commit_sha`` after
    `Committer.commit`, ``pr_number` / ``pr_url` after `DraftPRCreator.open`.
    """

    branch: str
    worktree_path: str = ""
    base_commit_sha: str = ""
    edited_files: Tuple[str, ...] = ()
    test_summary: Mapping[str, Any] = field(default_factory=dict)
    commit_sha: str = ""
    pushed: bool = False
    pr_number: Optional[int] = None
    pr_url: str = ""


class WorktreeProvisioner(Protocol):
    def provision(
        self, *, request: CodingExecuteRequest, branch: str
    ) -> WorktreeContext:  # pragma: no cover - Protocol
        ...


class CodeEditor(Protocol):
    def apply(
        self,
        *,
        request: CodingExecuteRequest,
        context: WorktreeContext,
    ) -> WorktreeContext:  # pragma: no cover - Protocol
        ...


class TestRunner(Protocol):
    def run(
        self,
        *,
        request: CodingExecuteRequest,
        context: WorktreeContext,
    ) -> WorktreeContext:  # pragma: no cover - Protocol
        ...


class Committer(Protocol):
    def commit(
        self,
        *,
        request: CodingExecuteRequest,
        context: WorktreeContext,
    ) -> WorktreeContext:  # pragma: no cover - Protocol
        ...


class Pusher(Protocol):
    def push(
        self,
        *,
        request: CodingExecuteRequest,
        context: WorktreeContext,
    ) -> WorktreeContext:  # pragma: no cover - Protocol

        ...


class DraftPRCreator(Protocol):
    def open(
        self,
        *,
        request: CodingExecuteRequest,
        context: WorktreeContext,
    ) -> WorktreeContext:  # pragma: no cover - Protocol
        ...


class _NotImplementedStep:
    """Default Protocol implementation — refuses to run.

    Live implementations are wired by a follow-up PR with explicit
    user authorization. This class prevents accidental "smoke" runs
    against production resources.
    """

    def __init__(self, step_name: str) -> None:
        self.step_name = step_name

    def __call__(self, **_kwargs: Any) -> WorktreeContext:
        raise CodingExecutorNotImplementedError(
            f"{self.step_name!r} is not wired; pass a custom Protocol "
            "implementation or run with dry_run=True"
        )

    # Protocol method names — all delegate to __call__.
    def provision(self, **kwargs: Any) -> WorktreeContext: return self(**kwargs)
    def apply(self, **kwargs: Any) -> WorktreeContext: return self(**kwargs)
    def run(self, **kwargs: Any) -> WorktreeContext: return self(**kwargs)
    def commit(self, **kwargs: Any) -> WorktreeContext: return self(**kwargs)
    def push(self, **kwargs: Any) -> WorktreeContext: return self(**kwargs)
    def open(self, **kwargs: Any) -> WorktreeContext: return self(**kwargs)


class CodingExecutorNotImplementedError(RuntimeError):
    """Raised when a Protocol step has no live implementation."""


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


class CodingExecutorWorker:
    """Idempotent worker for the ``coding_execute`` job type.

    Public surface mirrors the existing M5b workers:

      * :meth:`enqueue` — idempotent insert (dedup on session + role + branch_hint).
      * :meth:`process_job` — drive the 7-step pipeline.
      * :meth:`run_one` — pick + lease + process.
      * :meth:`find_active` — retrieve a non-terminal row for the session.
    """

    _ACTIVE_STATES: Tuple[JobState, ...] = (
        JobState.QUEUED,
        JobState.ASSIGNED,
        JobState.IN_PROGRESS,
        JobState.PENDING_APPROVAL,
        JobState.READY_FOR_OBSIDIAN,
    )

    def __init__(
        self,
        *,
        queue: JobQueue,
        heartbeats: Optional[HeartbeatStore] = None,
        worktree_provisioner: Optional[WorktreeProvisioner] = None,
        code_editor: Optional[CodeEditor] = None,
        test_runner: Optional[TestRunner] = None,
        committer: Optional[Committer] = None,
        pusher: Optional[Pusher] = None,
        draft_pr_creator: Optional[DraftPRCreator] = None,
    ) -> None:
        self._queue = queue
        self._heartbeats = heartbeats
        self._worktree = worktree_provisioner or _NotImplementedStep("worktree_provisioner")
        self._editor = code_editor or _NotImplementedStep("code_editor")
        self._tests = test_runner or _NotImplementedStep("test_runner")
        self._committer = committer or _NotImplementedStep("committer")
        self._pusher = pusher or _NotImplementedStep("pusher")
        self._pr_creator = draft_pr_creator or _NotImplementedStep("draft_pr_creator")

    # ------------------------------------------------------------------
    # Producer
    # ------------------------------------------------------------------

    def find_active(
        self,
        *,
        session_id: str,
        executor_role: str,
        branch_hint: str = "",
    ) -> Optional[Job]:
        if not session_id or not executor_role:
            return None
        try:
            rows = self._queue.list_for_session(session_id)
        except Exception:  # noqa: BLE001
            return None
        for job in rows or ():
            if job.job_type != JOB_TYPE_CODING_EXECUTE:
                continue
            payload = job.payload or {}
            if str(payload.get("executor_role") or "") != executor_role:
                continue
            if branch_hint and str(payload.get("branch_hint") or "") != branch_hint:
                continue
            if job.state in self._ACTIVE_STATES:
                return job
        return None

    def enqueue(
        self,
        request: CodingExecuteRequest,
        *,
        priority: int = 0,
        max_attempts: int = 1,
        now: Optional[float] = None,
    ) -> Tuple[Job, bool]:
        """Idempotent insert — returns ``(job, created)``."""

        if not request.session_id or not request.executor_role:
            raise ValueError("session_id + executor_role required")
        existing = self.find_active(
            session_id=request.session_id,
            executor_role=request.executor_role,
            branch_hint=request.branch_hint,
        )
        if existing is not None:
            return existing, False
        job = self._queue.enqueue(
            session_id=request.session_id,
            job_type=JOB_TYPE_CODING_EXECUTE,
            payload=dict(request.to_payload()),
            priority=priority,
            max_attempts=max_attempts,
            now=now,
            role=request.executor_role,
        )
        return job, True

    # ------------------------------------------------------------------
    # Consumer
    # ------------------------------------------------------------------

    def process_job(
        self,
        job: Job,
        *,
        now: Optional[float] = None,
    ) -> CodingExecuteOutcome:
        """Drive the 7-step pipeline for an already-leased job."""

        if self._heartbeats is not None:
            try:
                self._heartbeats.record(
                    SERVICE_ID_CODING_EXECUTOR,
                    pid=os.getpid(),
                    metadata={"job_id": job.job_id},
                    now=now,
                )
            except Exception:  # noqa: BLE001
                pass

        in_progress = self._queue.transition(
            job.job_id, JobState.IN_PROGRESS, now=now
        )
        try:
            request = CodingExecuteRequest.from_payload(in_progress.payload or {})
        except Exception as exc:  # noqa: BLE001
            return self._fail(
                in_progress,
                terminal=True,
                reason=f"{REASON_INVALID_REQUEST}: {_short(exc)}",
            )

        # --- Hard rail: validate branch hint before any execution -----------
        branch = request.branch_hint or self._suggest_branch(request)
        if is_protected_branch(branch):
            return self._fail(
                in_progress,
                terminal=True,
                reason=f"{REASON_PROTECTED_BRANCH} (branch={branch})",
                branch=branch,
            )

        # --- 7-step pipeline ------------------------------------------------
        try:
            ctx = WorktreeContext(branch=branch)

            if request.dry_run:
                # Dry-run path — no Protocol invoked, pure spec exercise.
                return self._success(
                    in_progress,
                    branch=branch,
                    test_summary={"dry_run": True},
                    audit_reason=REASON_DRY_RUN,
                )

            ctx = self._worktree.provision(request=request, branch=branch)
            ctx = self._editor.apply(request=request, context=ctx)
            ctx = self._tests.run(request=request, context=ctx)
            if not _tests_passed(ctx.test_summary):
                return self._fail(
                    in_progress,
                    terminal=False,
                    reason=REASON_TEST_FAILED,
                    branch=branch,
                    test_summary=dict(ctx.test_summary),
                )
            ctx = self._committer.commit(request=request, context=ctx)
            if not ctx.commit_sha:
                return self._fail(
                    in_progress,
                    terminal=False,
                    reason=REASON_COMMIT_FAILED,
                    branch=branch,
                )
            ctx = self._pusher.push(request=request, context=ctx)
            if not ctx.pushed:
                return self._fail(
                    in_progress,
                    terminal=False,
                    reason=REASON_PUSH_FAILED,
                    branch=branch,
                    commit_sha=ctx.commit_sha,
                )
            ctx = self._pr_creator.open(request=request, context=ctx)
            if not ctx.pr_number:
                return self._fail(
                    in_progress,
                    terminal=False,
                    reason=REASON_PR_FAILED,
                    branch=branch,
                    commit_sha=ctx.commit_sha,
                )
        except CodingExecutorNotImplementedError as exc:
            return self._fail(
                in_progress,
                terminal=True,
                reason=f"{REASON_NOT_IMPLEMENTED}: {_short(exc)}",
                branch=branch,
            )
        except Exception as exc:  # noqa: BLE001
            return self._fail(
                in_progress,
                terminal=False,
                reason=f"{REASON_EDIT_FAILED}: {_short(exc)}",
                branch=branch,
            )

        return self._success(
            in_progress,
            branch=ctx.branch,
            commit_sha=ctx.commit_sha,
            pr_number=ctx.pr_number,
            pr_url=ctx.pr_url,
            test_summary=dict(ctx.test_summary),
        )

    def run_one(
        self,
        *,
        worker_id: str,
        now: Optional[float] = None,
    ) -> CodingExecuteOutcome:
        """Pick + lease + process a single coding_execute job."""

        picked = self._queue.pick(
            worker_id=worker_id,
            job_types=[JOB_TYPE_CODING_EXECUTE],
            now=now,
        )
        if picked is None:
            return CodingExecuteOutcome(job=None, skipped_reason="no_jobs")
        return self.process_job(picked, now=now)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _suggest_branch(self, request: CodingExecuteRequest) -> str:
        # Mirror the G3 branch convention — prefer the role + issue number,
        # fall back to a deterministic timestamp slug. Caller can override
        # via request.branch_hint.
        if request.issue_number is not None:
            return f"agent/{request.executor_role}/issue-{int(request.issue_number)}-coding-execute"
        ts = int((time.time())) % 10_000
        return f"agent/{request.executor_role}/coding-execute-{ts}"

    def _fail(
        self,
        job: Job,
        *,
        terminal: bool,
        reason: str,
        branch: Optional[str] = None,
        commit_sha: Optional[str] = None,
        test_summary: Optional[Mapping[str, Any]] = None,
    ) -> CodingExecuteOutcome:
        target = JobState.FAILED_TERMINAL if terminal else JobState.FAILED_RETRYABLE
        try:
            self._queue.transition(
                job.job_id,
                target,
                now=time.time(),
                result={"reason": reason, "branch": branch, "commit_sha": commit_sha},
            )
        except Exception:  # noqa: BLE001
            pass
        return CodingExecuteOutcome(
            job=job,
            terminal_state=target.value,
            failure_reason=reason,
            branch=branch,
            commit_sha=commit_sha,
            test_summary=test_summary,
        )

    def _success(
        self,
        job: Job,
        *,
        branch: str,
        commit_sha: Optional[str] = None,
        pr_number: Optional[int] = None,
        pr_url: Optional[str] = None,
        test_summary: Optional[Mapping[str, Any]] = None,
        audit_reason: Optional[str] = None,
    ) -> CodingExecuteOutcome:
        try:
            self._queue.transition(
                job.job_id,
                JobState.SAVED,
                now=time.time(),
                result={
                    "branch": branch,
                    "commit_sha": commit_sha,
                    "pr_number": pr_number,
                    "pr_url": pr_url,
                    "audit_reason": audit_reason,
                },
            )
        except Exception:  # noqa: BLE001
            pass
        return CodingExecuteOutcome(
            job=job,
            terminal_state=JobState.SAVED.value,
            branch=branch,
            commit_sha=commit_sha,
            pr_number=pr_number,
            pr_url=pr_url,
            test_summary=test_summary,
        )


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


__all__ = (
    "CodingExecuteOutcome",
    "CodingExecuteRequest",
    "CodingExecutorNotImplementedError",
    "CodingExecutorWorker",
    "Committer",
    "CodeEditor",
    "DraftPRCreator",
    "JOB_TYPE_CODING_EXECUTE",
    "Pusher",
    "REASON_COMMIT_FAILED",
    "REASON_DRY_RUN",
    "REASON_EDIT_FAILED",
    "REASON_FORCE_PUSH_BLOCKED",
    "REASON_INVALID_REQUEST",
    "REASON_NOT_IMPLEMENTED",
    "REASON_PR_FAILED",
    "REASON_PROTECTED_BRANCH",
    "REASON_PUSH_FAILED",
    "REASON_TEST_FAILED",
    "SERVICE_ID_CODING_EXECUTOR",
    "TestRunner",
    "WorktreeContext",
    "WorktreeProvisioner",
    "is_protected_branch",
)
