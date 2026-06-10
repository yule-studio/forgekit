"""Live Protocol implementations for the coding executor — Round 2 of #73.

Foundation (Phase 1) registered 6 Protocol seams via
:mod:`coding_executor_worker`. This module ships the *repo-internal*
implementations that need no extra credentials beyond the existing
GitHub App env contract:

  * :class:`LocalGitWorktreeProvisioner` — ``git worktree add`` based
    branching off a clean main checkout.
  * :class:`RecordOnlyCodeEditor` — writes a planning markdown file
    that records exactly what an LLM-driven editor would do, but
    **does not modify code**. This keeps the rest of the pipeline
    exercisable end-to-end without an LLM in the loop.
  * :class:`SubprocessTestRunner` — runs a configurable test command
    under the worktree path; surfaces pass/fail summary.
  * :class:`LocalGitCommitter` — stages the planning artifact + any
    edits and commits with the role-bot author.
  * :class:`GithubAppPusher` — wraps :class:`LiveGithubAppClient` so
    the branch + commit land on origin via the App's git data API
    (no `git push` shell required).
  * :class:`GithubAppDraftPRCreator` — opens a draft PR via the App.

External / blocked (intentionally NOT wired here):

  * Real LLM editing — needs `claude` / `codex` CLI plus operator
    secret; tracked as ``[blocker]`` in the round-2 progress note.

The :func:`build_live_executor` factory composes these under one
``CodingExecutorWorker``-ready bundle and surfaces a
:class:`LiveExecutorAvailability` summary so the supervisor / runtime
can show what is actually wireable in the current environment.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

from .coding_execute_test_command import (
    TestCommandSelection,
    select_test_command,
)
from .coding_executor_worker import (
    CodingExecuteRequest,
    WorktreeContext,
)
from .coding_executor_live_format import (
    _commit_message,
    _draft_pr_body,
    _render_bootstrap_plan_markdown,
    _render_plan_markdown,
)

# F4 / #91 — Live LLM editor seam now lives in a sibling module. Re-export
# so existing ``from .coding_executor_live import LiveCodeEditor`` importers
# stay unchanged. One-way: the editor module imports nothing from here.
from .coding_executor_live_editor import (  # noqa: F401 - re-export
    BlockedLiveEditorError,
    CodeEditPort,
    LiveCodeEditor,
    ENV_LIVE_EDITOR_ENABLED,
    ENV_LIVE_EDITOR_PROVIDER,
    ENV_LIVE_EDITOR_MODEL,
    ENV_LIVE_EDITOR_MAX_RETRIES,
    PROVIDER_CLAUDE_CLI,
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENAI,
    build_live_editor_from_env,
)
# Pusher + draft PR seam lives in a sibling module. One-way: the push
# module imports the renderers from ``coding_executor_live_format``, never
# from here, so this re-export does not create an import-time cycle.
from .coding_executor_live_push import (  # noqa: F401 - re-export
    GithubAppDraftPRCreator,
    GithubAppPusher,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants + summaries
# ---------------------------------------------------------------------------


DEFAULT_WORKTREE_ROOT: str = "/tmp/yule-coding-executor-worktrees"
DEFAULT_TEST_COMMAND: Tuple[str, ...] = (
    "python3",
    "-m",
    "unittest",
    "discover",
    "-s",
    "tests",
    "-t",
    ".",
)
DEFAULT_PLAN_FILE_REL: str = "runs/coding-executor-plans/{branch_slug}.md"


@dataclass(frozen=True)
class LiveExecutorAvailability:
    """What the live executor can wire in the current environment.

    Operator-facing summary the supervisor surfaces in
    ``yule runtime status``; tests pin the boolean fields.
    """

    worktree_provisioner: bool
    code_editor: str  # "record_only" | "live_llm" | "blocked"
    code_editor_blocker: str = ""
    test_runner: bool = True
    committer: bool = True
    pusher: str = "blocked"  # "github_app" | "blocked"
    pusher_blocker: str = ""
    draft_pr_creator: str = "blocked"  # "github_app" | "blocked"
    draft_pr_blocker: str = ""

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "worktree_provisioner": self.worktree_provisioner,
            "code_editor": self.code_editor,
            "code_editor_blocker": self.code_editor_blocker,
            "test_runner": self.test_runner,
            "committer": self.committer,
            "pusher": self.pusher,
            "pusher_blocker": self.pusher_blocker,
            "draft_pr_creator": self.draft_pr_creator,
            "draft_pr_blocker": self.draft_pr_blocker,
        }


# ---------------------------------------------------------------------------
# Code editor — record-only (no LLM)
# ---------------------------------------------------------------------------


class RecordOnlyCodeEditor:
    """Records the LLM-bound prompt + scope as a planning markdown.

    **Does NOT modify any source files.** The Yule policy is that
    LLM-driven edits require explicit operator authorization (live
    LLM CLI + secret). This editor is the dry safety: the rest of
    the pipeline (tests / commit / push / PR) still runs end-to-end
    so the operator can verify the plumbing without real edits.

    The recorded artifact lands at ``runs/coding-executor-plans/<branch_slug>.md``
    inside the worktree — the committer stages it.
    """

    def __init__(self, *, plan_file_template: str = DEFAULT_PLAN_FILE_REL) -> None:
        self.plan_file_template = plan_file_template

    def apply(
        self,
        *,
        request: CodingExecuteRequest,
        context: WorktreeContext,
    ) -> WorktreeContext:
        if not context.worktree_path:
            raise ValueError("RecordOnlyCodeEditor requires a worktree_path")
        slug = _slugify(context.branch)
        rel = self.plan_file_template.format(branch_slug=slug)
        path = Path(context.worktree_path) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_plan_markdown(request, context), encoding="utf-8")
        return replace(context, edited_files=tuple(list(context.edited_files) + [rel]))


# P1-H — env-gated greenfield bootstrap.
ENV_GREENFIELD_BOOTSTRAP_ENABLED: str = "YULE_CODING_EXECUTOR_GREENFIELD_BOOTSTRAP_ENABLED"

# P1-M F — env-gated 정직한 blocker. 옛 wiring 은 non-greenfield 에
# RecordOnlyCodeEditor 가 plan markdown 만 commit 하면 planning-only PR
# 이 진짜 구현 PR 처럼 production main 까지 흘러갔다. 본 env 가 truthy
# 면 worker 가 ``REASON_NON_GREENFIELD_REAL_EDIT_UNAVAILABLE`` 로 blocker
# 노출 — operator 가 live editor wiring 한 뒤에야 다음 slice 굴러간다.
ENV_PLANNING_ONLY_PR_FORBIDDEN: str = (
    "YULE_CODING_EXECUTOR_PLANNING_ONLY_PR_FORBIDDEN"
)


def _planning_only_pr_forbidden(
    env: Optional[Mapping[str, str]] = None,
) -> bool:
    import os as _os

    src = env if env is not None else _os.environ
    return (src.get(ENV_PLANNING_ONLY_PR_FORBIDDEN) or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


class NonGreenfieldRealEditUnavailable(RuntimeError):
    """non-greenfield repo + record-only editor + env opt-in 시 raise.

    worker 가 ``REASON_NON_GREENFIELD_REAL_EDIT_UNAVAILABLE`` 로 매핑.
    """

    def __init__(self, *, repo_full_name: Optional[str], worktree_path: str) -> None:
        super().__init__(
            f"non-greenfield repo {repo_full_name!r} requires real-edit "
            f"capability. Set YULE_CODING_EXECUTOR_PLANNING_ONLY_PR_FORBIDDEN=0 "
            f"to allow planning-only PR (NOT recommended) OR wire a real LLM "
            f"editor. worktree={worktree_path}"
        )
        self.repo_full_name = repo_full_name
        self.worktree_path = worktree_path


def _greenfield_bootstrap_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    import os as _os

    src = env if env is not None else _os.environ
    return (src.get(ENV_GREENFIELD_BOOTSTRAP_ENABLED) or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


class GreenfieldBootstrapEditor:
    """Bootstrap-capable editor for greenfield target repos.

    P1-H — empty target repo + full-stack/python request 시:
      * deterministic scaffold plan (Next/Nest/Postgres docker-compose OR
        python pyproject layout) 생성
      * ``request.write_scope`` governance 준수
      * idempotent — 이미 존재하는 파일 절대 덮어쓰지 않음

    greenfield 가 아니면 기본 동작은 record-only delegation (plan note
    만 작성) — 옛 ``RecordOnlyCodeEditor`` 와 동일.

    Env gate: ``YULE_CODING_EXECUTOR_GREENFIELD_BOOTSTRAP_ENABLED`` 가
    truthy 일 때만 scaffold 실행. off 면 record-only 만 + worker 가
    ``bootstrap_required:live_editor_unavailable`` 로 surface (operator
    가 명시적 opt-in 필요).
    """

    def __init__(
        self,
        *,
        plan_file_template: str = DEFAULT_PLAN_FILE_REL,
        env: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.plan_file_template = plan_file_template
        self._env = env
        self._delegate = RecordOnlyCodeEditor(
            plan_file_template=plan_file_template
        )

    @property
    def is_bootstrap_capable(self) -> bool:
        return _greenfield_bootstrap_enabled(self._env)

    def apply(
        self,
        *,
        request: CodingExecuteRequest,
        context: WorktreeContext,
    ) -> WorktreeContext:
        if not context.worktree_path:
            raise ValueError("GreenfieldBootstrapEditor requires a worktree_path")

        from ..coding.greenfield_bootstrap import (
            apply_bootstrap_plan,
            detect_bootstrap_mode,
            plan_greenfield_scaffold,
        )

        mode = detect_bootstrap_mode(
            request=request, worktree_path=context.worktree_path
        )
        # P1-K — per-apply audit emitted regardless of branch so operator
        # status surface can see WHICH path executed (delegate / refuse /
        # scaffold) and at WHICH worktree. silent delegate 가 가장 진단
        # 하기 어려웠던 회귀의 직접 원인.
        logger.info(
            "GreenfieldBootstrapEditor.apply: worktree=%s repo_full_name=%s "
            "detected_mode=%s bootstrap_enabled=%s",
            context.worktree_path,
            request.repo_full_name,
            mode,
            self.is_bootstrap_capable,
        )
        if mode is None:
            # Not a greenfield case.
            # P1-M F — env gate: planning-only PR 가 production main 까지
            # 흘러가는 사고를 막기 위해 truthy 면 raise → worker 가
            # ``REASON_NON_GREENFIELD_REAL_EDIT_UNAVAILABLE`` blocker stamp.
            if _planning_only_pr_forbidden(self._env):
                logger.warning(
                    "GreenfieldBootstrapEditor.apply: non-greenfield repo "
                    "blocked by ENV_PLANNING_ONLY_PR_FORBIDDEN — repo=%s "
                    "worktree=%s",
                    request.repo_full_name,
                    context.worktree_path,
                )
                raise NonGreenfieldRealEditUnavailable(
                    repo_full_name=request.repo_full_name,
                    worktree_path=context.worktree_path,
                )
            # otherwise — record-only delegation 보존 (옛 동작).
            new_metadata = dict(context.metadata or {})
            new_metadata["bootstrap_apply"] = {
                "mode": None,
                "decision": "delegate_record_only",
                "reason": (
                    "detect_bootstrap_mode returned None — repo not greenfield "
                    "OR request text has no full-stack/python signals"
                ),
                "worktree_path": context.worktree_path,
                "repo_full_name": request.repo_full_name,
                "bootstrap_enabled": self.is_bootstrap_capable,
                "planning_only_pr_forbidden": _planning_only_pr_forbidden(
                    self._env
                ),
            }
            from dataclasses import replace as _replace

            delegated = self._delegate.apply(request=request, context=context)
            return _replace(delegated, metadata=new_metadata)

        if not self.is_bootstrap_capable:
            # Greenfield detected but operator hasn't opted into live
            # bootstrap — surface a clear capability gap. Worker maps
            # to ``REASON_BOOTSTRAP_REQUIRED:live_editor_unavailable``.
            raise BootstrapLiveEditorUnavailable(
                mode=mode,
                message=(
                    f"greenfield bootstrap mode {mode!r} requires the "
                    f"{ENV_GREENFIELD_BOOTSTRAP_ENABLED} env opt-in"
                ),
            )

        # Real scaffold path. Plan + governance-aware apply.
        plan = plan_greenfield_scaffold(mode=mode, request=request)
        result = apply_bootstrap_plan(
            worktree_path=context.worktree_path,
            plan=plan,
            write_scope=tuple(request.write_scope or ()),
            forbidden_scope=tuple(request.forbidden_scope or ()),
            allow_bootstrap_essentials=True,
        )
        # P1-J — distinguish two failure shapes so operator sees the
        # actual cause instead of the misleading "no_stack_detected"
        # loop on the next run:
        #   * write_errors (disk/permissions) → ``scaffold_apply_failed``
        #   * 0 created but ≥1 refused → ``scope_refused_bootstrap_files``
        if result.write_errors and not result.files_created:
            raise BootstrapApplyFailed(
                mode=plan.mode,
                message=(
                    f"all scaffold files failed: "
                    f"{result.write_errors[:2]} (truncated)"
                ),
                sub_reason="scaffold_apply_failed",
            )
        if result.all_files_refused_by_scope:
            raise BootstrapApplyFailed(
                mode=plan.mode,
                message=(
                    f"all scaffold files refused — scope={list(request.write_scope or ())[:3]} "
                    f"refused_by_scope={list(result.files_refused_by_scope)[:5]} "
                    f"refused_by_forbidden={list(result.files_refused_by_forbidden)[:3]}"
                ),
                sub_reason="scope_refused_bootstrap_files",
            )
        # Also write the plan note for audit (mirroring record-only path).
        slug = _slugify(context.branch)
        note_rel = self.plan_file_template.format(branch_slug=slug)
        note_path = Path(context.worktree_path) / note_rel
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text(
            _render_bootstrap_plan_markdown(request, context, plan, result),
            encoding="utf-8",
        )

        new_edited = list(context.edited_files) + [note_rel] + list(result.files_created)
        new_metadata = dict(context.metadata or {})
        new_metadata["bootstrap_apply"] = result.to_audit()
        new_metadata["bootstrap_plan_summary"] = plan.summary
        new_metadata["bootstrap_stack_signals_expected"] = list(
            plan.stack_signals_expected
        )
        return replace(
            context,
            edited_files=tuple(new_edited),
            metadata=new_metadata,
        )


class BootstrapLiveEditorUnavailable(RuntimeError):
    """Raised by ``GreenfieldBootstrapEditor`` when greenfield bootstrap
    is required but the env opt-in is not set. Worker surfaces this as
    ``REASON_BOOTSTRAP_REQUIRED:live_editor_unavailable``.
    """

    def __init__(self, *, mode: str, message: str) -> None:
        super().__init__(message)
        self.mode = mode


class BootstrapApplyFailed(RuntimeError):
    """All scaffold writes failed. Two sub-reasons:

      * ``scaffold_apply_failed`` — disk/permissions errors (real OSError)
      * ``scope_refused_bootstrap_files`` — write_scope refused every
        scaffold path even after the bootstrap-essential allowlist
        exception ran. operator must widen scope OR run with bootstrap
        env on a different role whose scope includes the scaffold paths.

    Worker surfaces as ``REASON_BOOTSTRAP_REQUIRED:<sub_reason>:<mode>``.
    """

    def __init__(
        self,
        *,
        mode: str,
        message: str,
        sub_reason: str = "scaffold_apply_failed",
    ) -> None:
        super().__init__(message)
        self.mode = mode
        self.sub_reason = sub_reason


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------


class SubprocessTestRunner:
    """Runs a configurable test command under the worktree.

    Defaults to the project's standard ``python3 -m unittest discover``
    command; operator can override per-execution via
    ``CodingExecuteRequest.metadata['test_command']`` (list of strings).
    """

    def __init__(
        self,
        *,
        default_command: Sequence[str] = DEFAULT_TEST_COMMAND,
        timeout_seconds: int = 600,
        runner: Optional[Any] = None,
    ) -> None:
        self.default_command = tuple(default_command)
        self.timeout_seconds = timeout_seconds
        self._runner = runner or _run_subprocess

    def run(
        self,
        *,
        request: CodingExecuteRequest,
        context: WorktreeContext,
    ) -> WorktreeContext:
        if not context.worktree_path:
            raise ValueError("SubprocessTestRunner requires a worktree_path")

        # P1-E stack-aware selection. operator override (metadata.test_command)
        # 가 항상 우선, 그 다음 worktree 의 실제 파일 시그널 (package.json /
        # pyproject.toml / manage.py / lock files).
        selection = select_test_command(
            worktree_path=context.worktree_path,
            request_metadata=request.metadata,
            fallback_command=self.default_command,
        )

        # P1-G bootstrap_required short-circuit — selection 이 "no stack" /
        # "greenfield" 라면 subprocess 실행 자체가 의미 없음. 옛 동작은
        # python unittest 로 fallback 해 misleading test_failed 만 남겼다.
        # 본 분기는 caller (worker) 가 ``REASON_BOOTSTRAP_REQUIRED`` 로
        # surface 할 수 있게 selection 만 stamp 하고 즉시 반환.
        if selection.requires_bootstrap:
            return replace(
                context,
                test_summary={
                    "status": "bootstrap_required",
                    "command": [],
                    "exit_code": None,
                    "selection": selection.to_audit(),
                    "reason": selection.reason,
                    "bootstrap_sub_reason": selection.bootstrap_sub_reason,
                },
            )

        cmd = list(selection.command)
        try:
            result = self._runner(
                cmd,
                cwd=context.worktree_path,
                capture_output=True,
                timeout=self.timeout_seconds,
            )
        except _SubprocessError as exc:
            return replace(
                context,
                test_summary={
                    "status": "failed",
                    "command": cmd,
                    "exit_code": exc.exit_code,
                    "stdout_tail": _tail(exc.stdout, lines=20),
                    "stderr_tail": _tail(exc.stderr, lines=20),
                    "selection": selection.to_audit(),
                },
            )
        # success
        return replace(
            context,
            test_summary={
                "status": "ok",
                "command": cmd,
                "exit_code": 0,
                "stdout_tail": _tail(result.stdout, lines=20),
                "selection": selection.to_audit(),
            },
        )


def _resolve_test_command(request: CodingExecuteRequest) -> Optional[Sequence[str]]:
    """Backward-compat helper — old callers still import this. New code
    uses :func:`select_test_command` directly via the runner above.
    """

    cmd = (request.metadata or {}).get("test_command")
    if isinstance(cmd, (list, tuple)) and cmd:
        return tuple(str(c) for c in cmd)
    return None


# ---------------------------------------------------------------------------
# Committer
# ---------------------------------------------------------------------------


class LocalGitCommitter:
    """Stages every modified path under the worktree + commits.

    Author / committer use the role-bot identity so audit trails
    match the engineering-agent governance (`#69` write-ownership
    `role-owned` mode).
    """

    def __init__(
        self,
        *,
        bot_email_template: str = "{role}[bot]@yule-studio.local",
        bot_name_template: str = "yule {role} bot",
        runner: Optional[Any] = None,
    ) -> None:
        self.bot_email_template = bot_email_template
        self.bot_name_template = bot_name_template
        self._runner = runner or _run_subprocess

    def commit(
        self,
        *,
        request: CodingExecuteRequest,
        context: WorktreeContext,
    ) -> WorktreeContext:
        if not context.worktree_path:
            raise ValueError("LocalGitCommitter requires a worktree_path")
        wt = context.worktree_path
        author_name = self.bot_name_template.format(role=request.executor_role)
        author_email = self.bot_email_template.format(role=request.executor_role)
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": author_name,
            "GIT_AUTHOR_EMAIL": author_email,
            "GIT_COMMITTER_NAME": author_name,
            "GIT_COMMITTER_EMAIL": author_email,
        }
        # Stage the planned files only (no `git add .`) so unrelated
        # files left in the worktree don't get bundled.
        for rel in context.edited_files or ():
            self._runner(["git", "-C", wt, "add", "--", rel], env=env)
        # No-op if nothing was edited.
        if not context.edited_files:
            return replace(context, commit_sha="")

        message = _commit_message(request, context)

        # P1-N — cross-repo commit convention hard guard.
        # initial commit 여부는 worktree (target repo) 의 git log 로 판별 +
        # caller (bootstrap flow) 가 ``metadata["initial_commit"]`` hint 를 줄
        # 수 있다.  ambiguous 시 honest blocker raise.
        try:
            from ..governance.repo_write_policy import (
                enforce_commit_message,
                is_initial_commit_context,
                PolicyViolation,
                validate_initial_commit_decision,
            )

            explicit_hint = None
            metadata = request.metadata or {}
            if isinstance(metadata, Mapping):
                if "initial_commit" in metadata:
                    explicit_hint = bool(metadata.get("initial_commit"))
            decision = is_initial_commit_context(
                repo_root=wt,
                explicit_hint=explicit_hint,
                branch_hint=context.branch,
            )
            decision_result = validate_initial_commit_decision(decision)
            if not decision_result.ok and decision_result.reason:
                raise PolicyViolation(
                    reason=decision_result.reason,
                    detail=decision_result.detail,
                    fields=decision_result.fields,
                )
            enforce_commit_message(message, is_initial=decision.is_initial)
        except PolicyViolation as exc:
            raise CodingCommitError(
                f"commit policy violation: {exc.reason}: {exc.detail}"
            ) from exc

        try:
            self._runner(
                ["git", "-C", wt, "commit", "-m", message],
                env=env,
            )
        except _SubprocessError as exc:
            raise CodingCommitError(
                f"commit failed (exit {exc.exit_code}): {_tail(exc.stderr, lines=5)}"
            ) from exc
        sha = self._runner(
            ["git", "-C", wt, "rev-parse", "HEAD"],
            capture_output=True,
        ).stdout.strip()
        return replace(context, commit_sha=sha)


class CodingCommitError(RuntimeError):
    """Raised when the local git commit step fails."""


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def detect_live_executor_availability(
    *,
    repo_root: Optional[str] = None,
    live_client: Optional[Any] = None,
) -> LiveExecutorAvailability:
    """Inspect environment + injected resources, return an availability summary.

    P1-K — actual editor wiring is no longer the stale ``"record_only"``
    string. ``build_live_executor`` injects ``GreenfieldBootstrapEditor``
    (which delegates to record-only behavior for non-greenfield repos),
    and the bootstrap path activates only when
    ``YULE_CODING_EXECUTOR_GREENFIELD_BOOTSTRAP_ENABLED=1``. The audit
    string here reflects that so operator surface stops claiming
    "record_only" when bootstrap is actually wired.
    """

    pusher = "github_app" if live_client is not None else "blocked"
    pr = "github_app" if live_client is not None else "blocked"
    pusher_blocker = "" if live_client else "LiveGithubAppClient 미주입 (.env.local 의 YULE_GITHUB_APP_* 필요)"
    pr_blocker = pusher_blocker
    bootstrap_on = _greenfield_bootstrap_enabled()
    editor_label = (
        "greenfield_bootstrap+record_only_delegate"
        if bootstrap_on
        else "greenfield_bootstrap (disabled — env off)"
    )
    editor_blocker = (
        ""
        if bootstrap_on
        else (
            "greenfield bootstrap disabled — set "
            f"{ENV_GREENFIELD_BOOTSTRAP_ENABLED}=1 to enable scaffold "
            "of empty target repos"
        )
    )
    return LiveExecutorAvailability(
        worktree_provisioner=bool(repo_root),
        code_editor=editor_label,
        code_editor_blocker=editor_blocker,
        test_runner=True,
        committer=True,
        pusher=pusher,
        pusher_blocker=pusher_blocker,
        draft_pr_creator=pr,
        draft_pr_blocker=pr_blocker,
    )


def build_live_executor(
    *,
    repo_root: str,
    live_client: Optional[Any] = None,
    worktree_root: Optional[str] = None,
    test_command: Optional[Sequence[str]] = None,
) -> Mapping[str, Any]:
    """Compose the 6 Protocol implementations as a kwargs dict.

    Pass ``**build_live_executor(...)`` straight into
    :class:`CodingExecutorWorker`. When *live_client* is None, the
    pusher / draft-PR slots fall back to the
    :class:`_NotImplementedStep` defaults — the worker will still
    fail loudly with ``REASON_NOT_IMPLEMENTED`` on those steps.
    """

    bundle: dict[str, Any] = {
        "worktree_provisioner": LocalGitWorktreeProvisioner(
            repo_root=repo_root, worktree_root=worktree_root
        ),
        # P1-H — pick GreenfieldBootstrapEditor automatically. The new
        # editor delegates to record-only behavior for non-greenfield
        # cases, so this is a strict superset. Bootstrap actually
        # writes files only when YULE_CODING_EXECUTOR_GREENFIELD_BOOTSTRAP_ENABLED=1;
        # otherwise greenfield surfaces a clear ``live_editor_unavailable``
        # reason instead of silently writing record-only notes only.
        "code_editor": GreenfieldBootstrapEditor(),
        "test_runner": SubprocessTestRunner(
            default_command=tuple(test_command or DEFAULT_TEST_COMMAND)
        ),
        "committer": LocalGitCommitter(),
    }
    if live_client is not None:
        bundle["pusher"] = GithubAppPusher(live_client=live_client)
        bundle["draft_pr_creator"] = GithubAppDraftPRCreator(live_client=live_client)
    return bundle


# ---------------------------------------------------------------------------
# Subprocess wrapper
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SubprocessResult:
    stdout: str
    stderr: str
    exit_code: int


class _SubprocessError(RuntimeError):
    def __init__(self, exit_code: int, stdout: str, stderr: str) -> None:
        super().__init__(f"subprocess failed: exit={exit_code}")
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


def _run_subprocess(
    cmd: Sequence[str],
    *,
    cwd: Optional[str] = None,
    capture_output: bool = False,
    env: Optional[Mapping[str, str]] = None,
    timeout: Optional[int] = None,
) -> _SubprocessResult:
    args = [c for c in cmd if c]  # filter out the empty-string force flag
    try:
        completed = subprocess.run(  # noqa: S603 - explicit list, no shell
            args,
            cwd=cwd,
            env=dict(env) if env else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise _SubprocessError(
            exit_code=124,
            stdout=str(exc.stdout or ""),
            stderr=f"timeout after {timeout}s",
        ) from exc
    if completed.returncode != 0:
        raise _SubprocessError(
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
    return _SubprocessResult(
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        exit_code=completed.returncode,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(value: str) -> str:
    text = (value or "").strip().lower()
    safe: list[str] = []
    for ch in text:
        if ch.isalnum():
            safe.append(ch)
        elif ch in {"-", "_", "/", "."}:
            safe.append("-")
        else:
            safe.append("-")
    slug = "".join(safe).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:80] or "branch"


def _tail(text: Optional[str], *, lines: int = 20) -> str:
    if not text:
        return ""
    parts = text.splitlines()
    return "\n".join(parts[-lines:])


__all__ = (
    "DEFAULT_PLAN_FILE_REL",
    "DEFAULT_TEST_COMMAND",
    "DEFAULT_WORKTREE_ROOT",
    "CodingCommitError",
    "GithubAppDraftPRCreator",
    "GithubAppPusher",
    "LiveExecutorAvailability",
    "LocalGitCommitter",
    "BootstrapApplyFailed",
    "BootstrapLiveEditorUnavailable",
    "ENV_GREENFIELD_BOOTSTRAP_ENABLED",
    "GreenfieldBootstrapEditor",
    "LocalGitWorktreeProvisioner",
    "RecordOnlyCodeEditor",
    "SubprocessTestRunner",
    "TargetRepoUnavailableError",
    "TestCommandSelection",
    "WorktreeProvisionError",
    "_default_repo_root_resolver",
    "build_live_executor",
    "detect_live_executor_availability",
    "select_test_command",
    # F4 / #91 — Live LLM editor MVP (env-gated, claude-cli only).
    "BlockedLiveEditorError",
    "CodeEditPort",
    "LiveCodeEditor",
    "ENV_LIVE_EDITOR_ENABLED",
    "ENV_LIVE_EDITOR_PROVIDER",
    "ENV_LIVE_EDITOR_MODEL",
    "ENV_LIVE_EDITOR_MAX_RETRIES",
    "PROVIDER_CLAUDE_CLI",
    "PROVIDER_ANTHROPIC",
    "PROVIDER_OPENAI",
    "build_live_editor_from_env",
    # Worktree provisioner seam (sibling module).
    "WorktreeProvisionResult",
    "_looks_like_branch_already_exists",
)


# ---------------------------------------------------------------------------
# Worktree provisioner — re-export
#
# The worktree provisioner group lives in a sibling module. The import is
# placed at the *bottom* of this module on purpose: the provisioner imports
# the shared subprocess + slug helpers (``_run_subprocess`` /
# ``_SubprocessError`` / ``_slugify`` / ``_tail`` / ``DEFAULT_WORKTREE_ROOT``)
# from here, and those are all defined above before this line runs, so the
# one-way dependency never forms an import-time cycle. Re-exporting keeps
# every ``from .coding_executor_live import LocalGitWorktreeProvisioner``
# importer unchanged.
# ---------------------------------------------------------------------------

from .coding_executor_live_provision import (  # noqa: E402,F401 - re-export
    LocalGitWorktreeProvisioner,
    TargetRepoUnavailableError,
    WorktreeProvisionError,
    WorktreeProvisionResult,
    _default_repo_root_resolver,
    _looks_like_branch_already_exists,
)
