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
from typing import (
    Any,
    Mapping,
    Optional,
    Protocol,
    Tuple,
)

from ..governance.runtime_policy import validate_branch_name
from .heartbeat import HeartbeatStore
from .state_machine import JobState
from .store import Job, JobQueue
from .work_order_coding_continuation import (
    PROGRESS_CODING_BLOCKED,
    PROGRESS_CODING_IN_PROGRESS,
    PROGRESS_DRAFT_PR_OPENED,
)

# Reason classification (constants + branch/test/error classifiers) and
# progress stamping were split into sibling modules (#73 follow-up).
# ``import *`` re-exports the reason module's public ``__all__`` so existing
# importers (``from .coding_executor_worker import REASON_TEST_FAILED`` etc.)
# keep working unchanged; the private helpers used internally are imported
# explicitly below.
from .coding_executor_reason import *  # noqa: F401,F403
from .coding_executor_reason import (
    _PROTECTED_BRANCH_NAMES,
    _short,
    _tests_passed,
    suggest_branch as _suggest_branch_fn,
)
from .coding_executor_progress import (
    stamp_pr_merge_continuation as _stamp_pr_merge_continuation_fn,
    stamp_progress as _stamp_progress_fn,
)


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
    metadata: Mapping[str, Any] = field(default_factory=dict)


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

        # --- Progress marker: coding_in_progress ----------------------------
        # session.extra 에 marker stamp — operator 가 status 에서 "지금 실행
        # 중인지 / 아직 큐에서 대기인지" 즉시 구분 가능.
        self._stamp_progress(
            session_id=request.session_id,
            marker=PROGRESS_CODING_IN_PROGRESS,
            detail={
                "job_id": in_progress.job_id,
                "executor_role": request.executor_role,
                "branch_hint": request.branch_hint or None,
                "issue_number": request.issue_number,
            },
        )

        # --- Hard rail: validate branch hint before any execution -----------
        branch = request.branch_hint or self._suggest_branch(request)
        if is_protected_branch(branch):
            self._stamp_progress(
                session_id=request.session_id,
                marker=PROGRESS_CODING_BLOCKED,
                detail={
                    "job_id": in_progress.job_id,
                    "reason": REASON_PROTECTED_BRANCH,
                    "branch": branch,
                },
            )
            return self._fail(
                in_progress,
                terminal=True,
                reason=f"{REASON_PROTECTED_BRANCH} (branch={branch})",
                branch=branch,
            )

        # P0-T: runtime governance policy gate — protected 검사 외에 형식 /
        # protected qualified ref 도 추가 검사. issue anchor missing 같은
        # warning 은 거부하지 않고 audit 에만 남긴다.
        policy = validate_branch_name(
            branch, issue_number=request.issue_number
        )
        if not policy.allowed:
            self._stamp_progress(
                session_id=request.session_id,
                marker=PROGRESS_CODING_BLOCKED,
                detail={
                    "job_id": in_progress.job_id,
                    "reason": REASON_BRANCH_POLICY_VIOLATION,
                    "branch": branch,
                    "policy_reason": policy.reason,
                },
            )
            return self._fail(
                in_progress,
                terminal=True,
                reason=f"{REASON_BRANCH_POLICY_VIOLATION} ({policy.reason}; branch={branch})",
                branch=branch,
            )

        # --- 7-step pipeline ------------------------------------------------
        try:
            ctx = WorktreeContext(branch=branch)

            if request.dry_run:
                # Dry-run path — no Protocol invoked, pure spec exercise.
                self._stamp_progress(
                    session_id=request.session_id,
                    marker=PROGRESS_DRAFT_PR_OPENED,
                    detail={
                        "job_id": in_progress.job_id,
                        "branch": branch,
                        "dry_run": True,
                    },
                )
                return self._success(
                    in_progress,
                    branch=branch,
                    test_summary={"dry_run": True},
                    audit_reason=REASON_DRY_RUN,
                )

            # P1-B: separate worktree provisioning from editor so we can
            # surface specific failure tokens (target_repo_checkout_missing
            # / worktree_provision_failed) instead of generic edit_failed.
            try:
                ctx = self._worktree.provision(request=request, branch=branch)
            except Exception as exc:  # noqa: BLE001 - mapped below
                target_repo_unavail = (
                    type(exc).__name__ == "TargetRepoUnavailableError"
                )
                worktree_specific = (
                    type(exc).__name__ == "WorktreeProvisionError"
                )
                if target_repo_unavail:
                    reason_token = REASON_TARGET_REPO_MISSING
                    # P1-D: target checkout missing is *recoverable infra
                    # state*, not a permanent business failure. operator
                    # creates the checkout (or sets env mapping) → the
                    # dedicated recovery hook revives the row on next
                    # tick. attempts still bounded by max_attempts so a
                    # tight retry burst stops naturally.
                    terminal = False
                elif worktree_specific:
                    reason_token = (
                        f"{REASON_WORKTREE_FAILED}:{getattr(exc, 'reason', 'unknown')}"
                    )
                    terminal = False
                else:
                    # fallthrough — keep old edit_failed behaviour via
                    # the outer except, re-raise so the broad handler
                    # below catches.
                    raise
                # P1-D: progress marker distinguishes "waiting on operator
                # checkout / env" (recoverable) from "worktree subprocess
                # failed" (different recovery path).
                progress_marker = PROGRESS_CODING_BLOCKED
                progress_detail = {
                    "job_id": in_progress.job_id,
                    "reason": reason_token,
                    "branch": branch,
                    "detail": _short(exc),
                    "repo_full_name": request.repo_full_name,
                }
                if target_repo_unavail:
                    progress_detail["status"] = "waiting_for_operator_checkout"
                    progress_detail["searched_roots"] = list(
                        getattr(exc, "searched_roots", ()) or ()
                    )
                self._stamp_progress(
                    session_id=request.session_id,
                    marker=progress_marker,
                    detail=progress_detail,
                )
                return self._fail(
                    in_progress,
                    terminal=terminal,
                    reason=f"{reason_token}: {_short(exc)}",
                    branch=branch,
                )
            # P1-H — editor 가 greenfield bootstrap 경로를 가지고 있고
            # capability gap (env opt-in 안 됨) 이거나 scaffold 자체가
            # 실패하면 두 specialized exception 으로 surface. 둘 다
            # ``REASON_BOOTSTRAP_REQUIRED`` 로 mapping 후 terminal 처리.
            try:
                ctx = self._editor.apply(request=request, context=ctx)
            except Exception as exc:  # noqa: BLE001 - mapped below
                exc_name = type(exc).__name__
                if exc_name == "BootstrapLiveEditorUnavailable":
                    sub_reason = (
                        f"live_editor_unavailable:{getattr(exc, 'mode', 'unknown')}"
                    )
                    self._stamp_progress(
                        session_id=request.session_id,
                        marker=PROGRESS_CODING_BLOCKED,
                        detail={
                            "job_id": in_progress.job_id,
                            "reason": REASON_BOOTSTRAP_REQUIRED,
                            "sub_reason": sub_reason,
                            "branch": branch,
                            "code_editor": type(self._editor).__name__,
                            "detail": _short(exc),
                        },
                    )
                    return self._fail(
                        in_progress,
                        terminal=True,
                        reason=f"{REASON_BOOTSTRAP_REQUIRED}:{sub_reason}",
                        branch=branch,
                    )
                if exc_name == "NonGreenfieldRealEditUnavailable":
                    # P1-M F — planning-only PR 가 production main 까지
                    # 흘러가는 사고 차단. live editor wiring 전까지는 다음
                    # slice 가 굴러가지 않는다.
                    self._stamp_progress(
                        session_id=request.session_id,
                        marker=PROGRESS_CODING_BLOCKED,
                        detail={
                            "job_id": in_progress.job_id,
                            "reason": REASON_NON_GREENFIELD_REAL_EDIT_UNAVAILABLE,
                            "branch": branch,
                            "repo_full_name": request.repo_full_name,
                            "worktree_path": getattr(exc, "worktree_path", ""),
                            "code_editor": type(self._editor).__name__,
                            "detail": _short(exc),
                        },
                    )
                    return self._fail(
                        in_progress,
                        terminal=True,
                        reason=REASON_NON_GREENFIELD_REAL_EDIT_UNAVAILABLE,
                        branch=branch,
                    )
                if exc_name == "BootstrapApplyFailed":
                    # P1-J: BootstrapApplyFailed.sub_reason is one of
                    # ``scaffold_apply_failed`` (disk/perm) or
                    # ``scope_refused_bootstrap_files`` (write_scope
                    # rejected every essential scaffold path).
                    sub_token = getattr(exc, "sub_reason", "scaffold_apply_failed")
                    sub_reason = (
                        f"{sub_token}:{getattr(exc, 'mode', 'unknown')}"
                    )
                    self._stamp_progress(
                        session_id=request.session_id,
                        marker=PROGRESS_CODING_BLOCKED,
                        detail={
                            "job_id": in_progress.job_id,
                            "reason": REASON_BOOTSTRAP_REQUIRED,
                            "sub_reason": sub_reason,
                            "branch": branch,
                            "code_editor": type(self._editor).__name__,
                            "detail": _short(exc),
                        },
                    )
                    return self._fail(
                        in_progress,
                        terminal=True,
                        reason=f"{REASON_BOOTSTRAP_REQUIRED}:{sub_reason}",
                        branch=branch,
                    )
                raise

            ctx = self._tests.run(request=request, context=ctx)
            # P1-G: test runner 가 bootstrap_required 로 short-circuit
            # 한 경우 — repo 에 detectable stack 이 없음. record-only
            # editor + greenfield 조합이면 sub-reason 에 capability
            # 부족까지 명시. terminal=True 로 무한 retry churn 차단 (생산
            # 차원의 fix: live editor / repo scaffolding 작업이 필요).
            test_summary_mapping = (
                ctx.test_summary if isinstance(ctx.test_summary, Mapping) else {}
            )
            if test_summary_mapping.get("status") == "bootstrap_required":
                sub_reason = (
                    test_summary_mapping.get("bootstrap_sub_reason") or "no_signals"
                )
                editor_class = type(self._editor).__name__
                editor_audit = editor_class
                if editor_class == "RecordOnlyCodeEditor":
                    sub_reason = (
                        f"{sub_reason}+editor_record_only_insufficient"
                    )
                self._stamp_progress(
                    session_id=request.session_id,
                    marker=PROGRESS_CODING_BLOCKED,
                    detail={
                        "job_id": in_progress.job_id,
                        "reason": REASON_BOOTSTRAP_REQUIRED,
                        "sub_reason": sub_reason,
                        "branch": branch,
                        "selection": test_summary_mapping.get("selection"),
                        "code_editor": editor_audit,
                    },
                )
                return self._fail(
                    in_progress,
                    terminal=True,
                    reason=f"{REASON_BOOTSTRAP_REQUIRED}:{sub_reason}",
                    branch=branch,
                    test_summary=dict(test_summary_mapping),
                )
            if not _tests_passed(ctx.test_summary):
                self._stamp_progress(
                    session_id=request.session_id,
                    marker=PROGRESS_CODING_BLOCKED,
                    detail={
                        "job_id": in_progress.job_id,
                        "reason": REASON_TEST_FAILED,
                        "branch": branch,
                    },
                )
                return self._fail(
                    in_progress,
                    terminal=False,
                    reason=REASON_TEST_FAILED,
                    branch=branch,
                    test_summary=dict(ctx.test_summary),
                )
            ctx = self._committer.commit(request=request, context=ctx)
            if not ctx.commit_sha:
                self._stamp_progress(
                    session_id=request.session_id,
                    marker=PROGRESS_CODING_BLOCKED,
                    detail={
                        "job_id": in_progress.job_id,
                        "reason": REASON_COMMIT_FAILED,
                        "branch": branch,
                    },
                )
                return self._fail(
                    in_progress,
                    terminal=False,
                    reason=REASON_COMMIT_FAILED,
                    branch=branch,
                )
            ctx = self._pusher.push(request=request, context=ctx)
            if not ctx.pushed:
                self._stamp_progress(
                    session_id=request.session_id,
                    marker=PROGRESS_CODING_BLOCKED,
                    detail={
                        "job_id": in_progress.job_id,
                        "reason": REASON_PUSH_FAILED,
                        "branch": branch,
                        "commit_sha": ctx.commit_sha,
                    },
                )
                return self._fail(
                    in_progress,
                    terminal=False,
                    reason=REASON_PUSH_FAILED,
                    branch=branch,
                    commit_sha=ctx.commit_sha,
                )
            ctx = self._pr_creator.open(request=request, context=ctx)
            if not ctx.pr_number:
                self._stamp_progress(
                    session_id=request.session_id,
                    marker=PROGRESS_CODING_BLOCKED,
                    detail={
                        "job_id": in_progress.job_id,
                        "reason": REASON_PR_FAILED,
                        "branch": branch,
                        "commit_sha": ctx.commit_sha,
                    },
                )
                return self._fail(
                    in_progress,
                    terminal=False,
                    reason=REASON_PR_FAILED,
                    branch=branch,
                    commit_sha=ctx.commit_sha,
                )
        except CodingExecutorNotImplementedError as exc:
            self._stamp_progress(
                session_id=request.session_id,
                marker=PROGRESS_CODING_BLOCKED,
                detail={
                    "job_id": in_progress.job_id,
                    "reason": REASON_NOT_IMPLEMENTED,
                    "branch": branch,
                    "detail": _short(exc),
                },
            )
            return self._fail(
                in_progress,
                terminal=True,
                reason=f"{REASON_NOT_IMPLEMENTED}: {_short(exc)}",
                branch=branch,
            )
        except Exception as exc:  # noqa: BLE001
            self._stamp_progress(
                session_id=request.session_id,
                marker=PROGRESS_CODING_BLOCKED,
                detail={
                    "job_id": in_progress.job_id,
                    "reason": REASON_EDIT_FAILED,
                    "branch": branch,
                    "detail": _short(exc),
                },
            )
            return self._fail(
                in_progress,
                terminal=False,
                reason=f"{REASON_EDIT_FAILED}: {_short(exc)}",
                branch=branch,
            )

        # PR 생성 성공 — operator-visible marker
        self._stamp_progress(
            session_id=request.session_id,
            marker=PROGRESS_DRAFT_PR_OPENED,
            detail={
                "job_id": in_progress.job_id,
                "branch": ctx.branch,
                "commit_sha": ctx.commit_sha,
                "pr_number": ctx.pr_number,
                "pr_url": ctx.pr_url,
            },
        )

        # P1-L — work_mode 분기. autonomous_merge 면 background 머지 루프가
        # pick 할 stage 를, approval_required 면 background producer 가
        # approval card 를 올릴 stage 를 session.extra 에 stamp.
        self._stamp_pr_merge_continuation(
            session_id=request.session_id,
            job_id=in_progress.job_id,
            repo_full_name=request.repo_full_name,
            pr_number=ctx.pr_number,
            pr_url=ctx.pr_url,
            head_sha=ctx.commit_sha,
            base_branch=request.base_branch or "main",
            dry_run=bool(request.dry_run),
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
        """Thin delegator — see :func:`coding_executor_reason.suggest_branch`."""

        return _suggest_branch_fn(request)

    def _stamp_progress(
        self,
        *,
        session_id: str,
        marker: str,
        detail: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Thin delegator — see :func:`coding_executor_progress.stamp_progress`."""

        _stamp_progress_fn(
            session_id=session_id, marker=marker, detail=detail
        )

    def _stamp_pr_merge_continuation(
        self,
        *,
        session_id: str,
        job_id: str,
        repo_full_name: Optional[str],
        pr_number: Optional[int],
        pr_url: Optional[str],
        head_sha: Optional[str],
        base_branch: str,
        dry_run: bool,
    ) -> None:
        """Thin delegator — see
        :func:`coding_executor_progress.stamp_pr_merge_continuation`."""

        _stamp_pr_merge_continuation_fn(
            session_id=session_id,
            job_id=job_id,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            pr_url=pr_url,
            head_sha=head_sha,
            base_branch=base_branch,
            dry_run=dry_run,
        )

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
    "REASON_BOOTSTRAP_REQUIRED",
    "REASON_NON_GREENFIELD_REAL_EDIT_UNAVAILABLE",
    "REASON_PUSH_FAILED",
    "REASON_TARGET_REPO_MISSING",
    "REASON_TEST_FAILED",
    "REASON_WORKTREE_FAILED",
    "SERVICE_ID_CODING_EXECUTOR",
    "TestRunner",
    "WorktreeContext",
    "WorktreeProvisioner",
    "is_protected_branch",
)
