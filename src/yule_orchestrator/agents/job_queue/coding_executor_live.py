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

import json
import logging
import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol, Sequence, Tuple

from .coding_execute_test_command import (
    TestCommandSelection,
    select_test_command,
)
from .coding_executor_worker import (
    CodingExecuteRequest,
    WorktreeContext,
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
# Worktree provisioner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorktreeProvisionResult:
    branch: str
    worktree_path: str
    base_commit_sha: str


class TargetRepoUnavailableError(RuntimeError):
    """P1-B: 요청한 ``repo_full_name`` 의 로컬 checkout 을 찾을 수 없음.

    operator 가 immediate 하게 이해할 수 있는 reason — generic subprocess
    exit 255 대신 본 예외를 throw 해서 executor 가 specific reason
    (``target_repo_checkout_missing``) 으로 fail 한다.
    """

    def __init__(
        self,
        *,
        repo_full_name: str,
        searched_roots: Sequence[str],
        message: Optional[str] = None,
    ) -> None:
        super().__init__(
            message
            or (
                f"target repo {repo_full_name!r} not found in any of: "
                f"{', '.join(searched_roots) or '(no candidates)'}"
            )
        )
        self.repo_full_name = repo_full_name
        self.searched_roots = tuple(searched_roots)


class WorktreeProvisionError(RuntimeError):
    """P1-B: worktree provisioning specific failure.

    Caller (executor) uses the ``reason`` token (``worktree_add_failed`` /
    ``branch_already_exists`` / ``base_branch_missing`` / etc.) 를 통해
    operator 에게 구체 원인을 노출.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _default_repo_root_resolver(
    repo_full_name: str,
    *,
    orchestrator_repo_root: str,
    extra_search_roots: Sequence[str] = (),
) -> Tuple[Optional[str], Sequence[str]]:
    """Default ``repo_full_name → local checkout path`` resolver.

    Search order:
      1. If ``repo_full_name`` 이 비어있으면 orchestrator_repo_root 사용
         (intra-repo coding task).
      2. ``$YULE_CODING_EXECUTOR_REPO_ROOTS_JSON`` (JSON map) lookup.
      3. ``$YULE_CODING_EXECUTOR_REPO_SEARCH_ROOTS`` (colon-separated
         directories) 안에서 ``<repo_name>`` 디렉터리 검색.
      4. orchestrator_repo_root 의 형제 디렉터리들 (``Path(parent) /
         <repo_name>``) 검색.
      5. fallback: orchestrator_repo_root 의 basename 이 repo_full_name
         의 name 부분과 같으면 orchestrator_repo_root 사용 (self-repo).
    Returns ``(resolved_path, searched_roots)`` — resolved_path None 이면
    caller (``TargetRepoUnavailableError``) 가 raise.
    """

    name = str(repo_full_name or "").strip()
    if not name or "/" not in name:
        return str(orchestrator_repo_root), ()
    owner, _, repo_name = name.partition("/")
    repo_name = repo_name.strip().rstrip(".git")
    searched: list[str] = []

    raw_json = os.environ.get("YULE_CODING_EXECUTOR_REPO_ROOTS_JSON") or ""
    if raw_json.strip():
        try:
            mapping = json.loads(raw_json)
        except Exception:  # noqa: BLE001 - bad JSON 는 silent skip
            mapping = {}
        if isinstance(mapping, Mapping):
            candidate = mapping.get(name) or mapping.get(repo_name)
            if isinstance(candidate, str) and candidate.strip():
                searched.append(candidate)
                if Path(candidate).is_dir():
                    return candidate, searched

    raw_paths = os.environ.get("YULE_CODING_EXECUTOR_REPO_SEARCH_ROOTS") or ""
    for root in (p.strip() for p in raw_paths.split(":") if p.strip()):
        cand = str(Path(root) / repo_name)
        searched.append(cand)
        if Path(cand).is_dir():
            return cand, searched

    for root in extra_search_roots:
        cand = str(Path(root) / repo_name)
        searched.append(cand)
        if Path(cand).is_dir():
            return cand, searched

    sibling = str(Path(orchestrator_repo_root).resolve().parent / repo_name)
    searched.append(sibling)
    if Path(sibling).is_dir():
        return sibling, searched

    if Path(orchestrator_repo_root).name == repo_name:
        return str(orchestrator_repo_root), searched

    return None, searched


class LocalGitWorktreeProvisioner:
    """``git worktree add`` based provisioner.

    Defaults the worktree root to ``DEFAULT_WORKTREE_ROOT`` so the
    executor's checkouts live outside the main repo path. Each call
    creates a fresh worktree at ``<root>/<branch-slug>`` and tracks
    the path so :meth:`cleanup` can remove it after the pipeline
    finishes (success *or* failure).

    P1-B: ``repo_full_name`` aware. ``provision`` resolves the local
    checkout for the request's target repo via
    :func:`_default_repo_root_resolver` (or an injected
    ``repo_root_resolver`` for tests). If resolution fails it raises
    :class:`TargetRepoUnavailableError` so the executor surface gets a
    specific reason instead of generic subprocess exit 255.

    Also: worktree creation is **idempotent** — if the local branch
    already exists (frequent on retries) the provisioner reuses it via
    ``git worktree add <path> <branch>`` (no ``-b``).
    """

    def __init__(
        self,
        *,
        repo_root: str,
        worktree_root: Optional[str] = None,
        runner: Optional[Any] = None,
        repo_root_resolver: Optional[Any] = None,
        extra_search_roots: Sequence[str] = (),
    ) -> None:
        self.repo_root = str(repo_root)
        self.worktree_root = str(worktree_root or DEFAULT_WORKTREE_ROOT)
        self._runner = runner or _run_subprocess
        self._provisioned: list[Tuple[str, str]] = []  # (repo_root, target)
        self._repo_root_resolver = repo_root_resolver
        self._extra_search_roots = tuple(extra_search_roots)

    def resolve_repo_root_for_request(
        self, request: CodingExecuteRequest
    ) -> str:
        """Return the local checkout path for *request.repo_full_name*.

        Resolution chain (P1-F):
          1. Existing local checkout via env JSON / search paths /
             sibling (existing default resolver).
          2. Auto-materialize via :func:`materialize_repo` — opt-in via
             ``YULE_CODING_EXECUTOR_REPO_AUTO_CLONE`` + owner allowlist.
             Cache root = ``YULE_CODING_EXECUTOR_REPO_CACHE_ROOT`` (default
             ``~/.cache/yule/repos``).
          3. Raise :class:`TargetRepoUnavailableError` with the
             materialization reason embedded so operator sees exactly
             why ("auto-clone disabled", "owner not allowed",
             "git clone failed: …") instead of generic "not found".
        """

        repo_name = (request.repo_full_name or "").strip()
        if not repo_name:
            return self.repo_root
        if self._repo_root_resolver is not None:
            resolved = self._repo_root_resolver(repo_name)
            if resolved:
                return str(resolved)
            raise TargetRepoUnavailableError(
                repo_full_name=repo_name,
                searched_roots=(),
                message=(
                    f"injected repo_root_resolver returned no path for "
                    f"{repo_name!r}"
                ),
            )
        resolved, searched = _default_repo_root_resolver(
            repo_name,
            orchestrator_repo_root=self.repo_root,
            extra_search_roots=self._extra_search_roots,
        )
        if resolved is not None:
            return resolved

        # P1-F: existing checkout 못 찾음 → auto-clone 시도 (opt-in
        # gated). 성공하면 그 path 사용, 거부 / 실패하면 reason 을
        # ``TargetRepoUnavailableError`` 에 그대로 surface.
        from .coding_execute_repo_materializer import (
            ACTION_FAILED,
            materialize_repo,
        )

        materialization = materialize_repo(repo_full_name=repo_name)
        if materialization.succeeded and materialization.path:
            return materialization.path
        message_suffix = materialization.reason or "no auto-clone outcome"
        raise TargetRepoUnavailableError(
            repo_full_name=repo_name,
            searched_roots=searched,
            message=(
                f"target repo {repo_name!r} not found in any of: "
                f"{', '.join(searched) or '(no candidates)'} — "
                f"materialization: {materialization.action} ({message_suffix})"
            ),
        )

    def provision(
        self, *, request: CodingExecuteRequest, branch: str
    ) -> WorktreeContext:
        # P1-Q E — Issue-first hard guard.  어떤 agent 가 호출하든 branch
        # 생성 전에 issue anchor 가 반드시 있어야 한다.  branch name 에
        # ``issue-<n>`` 또는 request.issue_number > 0.  옛 wiring 은 issue
        # 없이도 branch / commit / draft PR 까지 그대로 가능했고, 그게 사
        # 용자가 명시 reject 한 회귀의 직접 원인.  cross-repo 적용 — repo
        # 와 무관하게 동일.
        try:
            from ..governance.repo_write_policy import (
                IssueAnchorContext,
                PolicyViolation,
                enforce_issue_anchor,
            )

            enforce_issue_anchor(
                IssueAnchorContext(
                    branch=branch,
                    issue_number_hint=request.issue_number,
                )
            )
        except PolicyViolation as exc:
            raise WorktreeProvisionError(
                reason=exc.reason,
                message=(
                    f"issue-first hard guard: {exc.detail}. "
                    "Create a GitHub issue first and reference it via "
                    "`issue-<n>` in branch_hint OR pass request.issue_number."
                ),
            ) from exc

        repo_root = self.resolve_repo_root_for_request(request)
        slug = _slugify(branch)
        target = Path(self.worktree_root) / slug
        if target.exists():
            # Stale worktree from a previous failure; remove safely.
            self._safe_remove_existing(repo_root, target)
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            base_sha = self._get_base_sha(repo_root, request.base_branch)
        except _SubprocessError as exc:
            raise WorktreeProvisionError(
                reason="base_branch_missing",
                message=(
                    f"base branch {request.base_branch!r} not found in "
                    f"{repo_root!r}: {_tail(exc.stderr, lines=2) or exc}"
                ),
            ) from exc

        # P1-B idempotent retry — try ``git worktree add -b <branch>`` first;
        # if that fails because the local branch already exists, retry with
        # ``git worktree add <path> <branch>`` (no ``-b``) so the existing
        # branch is reused instead of failing with generic exit 255.
        try:
            self._runner(
                [
                    "git",
                    "-C",
                    repo_root,
                    "worktree",
                    "add",
                    "-b",
                    branch,
                    str(target),
                    base_sha,
                ]
            )
        except _SubprocessError as exc:
            if _looks_like_branch_already_exists(exc):
                logger.info(
                    "worktree provisioner: branch %s already exists in %s "
                    "— reusing via 'git worktree add <path> <branch>'",
                    branch,
                    repo_root,
                )
                try:
                    self._runner(
                        [
                            "git",
                            "-C",
                            repo_root,
                            "worktree",
                            "add",
                            str(target),
                            branch,
                        ]
                    )
                except _SubprocessError as reuse_exc:
                    raise WorktreeProvisionError(
                        reason="worktree_add_failed_reuse",
                        message=(
                            f"reuse of existing branch {branch!r} failed: "
                            f"{_tail(reuse_exc.stderr, lines=3) or reuse_exc}"
                        ),
                    ) from reuse_exc
            else:
                raise WorktreeProvisionError(
                    reason="worktree_add_failed",
                    message=(
                        f"git worktree add -b {branch!r} failed: "
                        f"{_tail(exc.stderr, lines=3) or exc}"
                    ),
                ) from exc
        self._provisioned.append((repo_root, str(target)))
        return WorktreeContext(
            branch=branch,
            worktree_path=str(target),
            base_commit_sha=base_sha,
        )

    def cleanup(self, *, force: bool = False) -> None:
        for entry in list(self._provisioned):
            # Backward-compat: old code paths may have appended plain
            # path strings; normalize to (repo_root, path) tuple.
            if isinstance(entry, tuple):
                repo_root, path = entry
            else:
                repo_root, path = self.repo_root, str(entry)
            try:
                self._runner(
                    [
                        "git",
                        "-C",
                        repo_root,
                        "worktree",
                        "remove",
                        "--force" if force else "",
                        path,
                    ]
                )
            except _SubprocessError:
                # Worktree may already be gone or dirty; fall back to
                # filesystem removal as last resort.
                shutil.rmtree(path, ignore_errors=True)
            self._provisioned.remove(entry)

    def _get_base_sha(self, repo_root: str, base_branch: str) -> str:
        result = self._runner(
            ["git", "-C", repo_root, "rev-parse", base_branch],
            capture_output=True,
        )
        return (result.stdout or "").strip()

    def _safe_remove_existing(self, repo_root: str, target: Path) -> None:
        # Try clean removal via git first; fallback to filesystem.
        try:
            self._runner(
                ["git", "-C", repo_root, "worktree", "remove", "--force", str(target)]
            )
        except _SubprocessError:
            shutil.rmtree(target, ignore_errors=True)


def _looks_like_branch_already_exists(exc: "_SubprocessError") -> bool:
    """Detect ``git worktree add -b`` 의 "이미 branch 가 있다" 류 에러.

    git 2.42+ 영어 메시지 + 일부 한글화 환경에서의 변형까지 cover.
    매칭 실패 시 False → caller 가 generic ``worktree_add_failed`` 로
    surfaces 한다.
    """

    text = " ".join(filter(None, [exc.stderr or "", exc.stdout or ""])).lower()
    needles = (
        "already exists",
        "is not a valid branch name",
        "a branch named",
        "is already used by worktree",
        "already checked out",
    )
    return any(needle in text for needle in needles)


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


def _render_bootstrap_plan_markdown(
    request: "CodingExecuteRequest",
    context: "WorktreeContext",
    plan: Any,
    result: Any,
) -> str:
    lines = [
        f"# greenfield-bootstrap plan — {context.branch}",
        "",
        f"- session_id: `{request.session_id}`",
        f"- executor_role: `{request.executor_role}`",
        f"- repo: `{request.repo_full_name or '(unset)'}`",
        f"- bootstrap_mode: `{plan.mode}`",
        f"- summary: {plan.summary}",
        "",
        "## scaffold result",
        "",
        f"- files_created ({len(result.files_created)}): {list(result.files_created)}",
        f"- files_skipped_exists ({len(result.files_skipped_exists)}): {list(result.files_skipped_exists)}",
        f"- files_refused_by_scope ({len(result.files_refused_by_scope)}): {list(result.files_refused_by_scope)}",
        f"- write_errors: {list(result.write_errors)}",
        "",
        "## next step",
        "",
        "이 scaffold 는 stack signal (package.json / pyproject.toml / docker-compose) 만",
        "만들어 두는 minimal viable shape 입니다. 실제 product 구현은 후속 coding",
        "job 들이 같은 repo 에 PR 단위로 land 합니다.",
        "",
    ]
    return "\n".join(lines) + "\n"


def _render_plan_markdown(
    request: CodingExecuteRequest, context: WorktreeContext
) -> str:
    lines = [
        f"# coding-executor plan — {context.branch}",
        "",
        f"- session_id: `{request.session_id}`",
        f"- executor_role: `{request.executor_role}`",
        f"- repo: `{request.repo_full_name or '(unset)'}`",
        f"- issue: `#{request.issue_number}`" if request.issue_number else "- issue: (none)",
        f"- base_branch: `{request.base_branch}` @ `{context.base_commit_sha[:10]}`",
        "",
        "## 사용자 요청",
        "",
        request.user_request or "_(empty)_",
        "",
        "## write_scope",
    ]
    for entry in request.write_scope or ("(unspecified)",):
        lines.append(f"- {entry}")
    lines.append("")
    lines.append("## forbidden_scope")
    for entry in request.forbidden_scope or ("(none)",):
        lines.append(f"- {entry}")
    lines.append("")
    lines.append("## safety_rules")
    for entry in request.safety_rules or ("(none)",):
        lines.append(f"- {entry}")
    lines.append("")
    lines.append("## planned executor prompt")
    lines.append("")
    lines.append("```text")
    lines.append((request.generated_prompt or "(empty prompt)").strip())
    lines.append("```")
    lines.append("")
    lines.append(
        "> **Note:** Real LLM-driven edits require operator authorization "
        "(live `claude` / `codex` CLI + secret). This file is the dry record "
        "the executor produced via `RecordOnlyCodeEditor` so the rest of the "
        "pipeline (tests / commit / push / draft PR) can be exercised end-to-end."
    )
    lines.append("")
    return "\n".join(lines)


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


def _commit_message(
    request: CodingExecuteRequest, context: WorktreeContext
) -> str:
    head = (
        f"📝 #{request.issue_number} coding-executor 계획 기록"
        if request.issue_number
        else "📝 coding-executor 계획 기록"
    )
    return (
        f"{head}\n"
        "\n변경 이유\n"
        f"- coding_execute job (executor={request.executor_role}) 의 RecordOnly editor 산출\n"
        "\n주요 변경 사항\n"
        f"- branch={context.branch} (from {request.base_branch}) 생성\n"
        f"- 계획 markdown 1 건 추가\n"
        "\n비고\n"
        "- 본 commit 은 RecordOnly editor 의 dry 산출. 실 LLM 편집은 후속 PR 의 운영자 승인 + secret 확인 후."
    )


# ---------------------------------------------------------------------------
# Pusher + draft PR — via GitHub App git data API
# ---------------------------------------------------------------------------


class GithubAppPusher:
    """Pushes the branch + commit via the GitHub App git data API.

    Avoids local ``git push`` so we never need credential setup
    inside the worker. Reads the worktree's commit objects and
    re-creates them on origin via blob → tree → commit → ref.
    """

    def __init__(self, *, live_client: Any) -> None:
        self._live = live_client

    def push(
        self,
        *,
        request: CodingExecuteRequest,
        context: WorktreeContext,
    ) -> WorktreeContext:
        repo = request.repo_full_name
        if not repo:
            raise ValueError("GithubAppPusher requires CodingExecuteRequest.repo_full_name")
        if not context.worktree_path:
            raise ValueError("GithubAppPusher requires a worktree_path")

        base_branch = request.base_branch or "main"
        base_sha = self._live.get_branch_head_sha(repo=repo, branch=base_branch)
        base_tree = self._live.get_commit_tree_sha(repo=repo, commit_sha=base_sha)

        entries = []
        for rel in context.edited_files or ():
            full = Path(context.worktree_path) / rel
            content = full.read_text(encoding="utf-8")
            blob_sha = self._live.create_blob(repo=repo, content=content)
            entries.append(
                {"path": rel, "mode": "100644", "type": "blob", "sha": blob_sha}
            )
        if not entries:
            # Nothing to push — degenerate but valid.
            return replace(context, pushed=False)

        tree = self._live.create_tree(repo=repo, base_tree=base_tree, entries=entries)
        tree_sha = (
            tree if isinstance(tree, str) else (tree.get("sha") if isinstance(tree, Mapping) else str(tree))
        )

        # Branch ref must exist before create_commit_via_data_api PATCHes it.
        try:
            self._live.create_branch_ref(
                repo=repo, branch=context.branch, base_sha=base_sha
            )
        except Exception as exc:  # noqa: BLE001 - already-exists is acceptable
            if "already exists" not in str(exc).lower():
                raise

        actor_name = "yule-studio engineering-agent"
        actor_email = "engineering-agent[bot]@users.noreply.github.com"
        commit_obj = self._live.create_commit_via_data_api(
            repo=repo,
            branch=context.branch,
            message=_commit_message(request, context),
            tree=str(tree_sha),
            author={"name": actor_name, "email": actor_email},
            committer={"name": actor_name, "email": actor_email},
            parents=[base_sha],
        )
        commit_sha = str(commit_obj.get("sha") or "")
        return replace(context, commit_sha=commit_sha or context.commit_sha, pushed=True)


class GithubAppDraftPRCreator:
    """Opens a draft PR via :class:`LiveGithubAppClient`."""

    def __init__(self, *, live_client: Any) -> None:
        self._live = live_client

    def open(
        self,
        *,
        request: CodingExecuteRequest,
        context: WorktreeContext,
    ) -> WorktreeContext:
        repo = request.repo_full_name
        if not repo:
            raise ValueError("GithubAppDraftPRCreator requires repo_full_name")
        # P1-M D — 한국어 humanizer 가 slice/세션 정보로 명확한 제목 생성.
        # slice_spec / session_prompt 는 dispatcher 가 request.metadata 에 stamp.
        try:
            from ..coding.human_titles import build_pr_title

            metadata = request.metadata or {}
            slice_spec = (
                metadata.get("slice_spec")
                if isinstance(metadata, Mapping)
                else None
            )
            session_prompt = (
                metadata.get("session_prompt")
                if isinstance(metadata, Mapping)
                else None
            )
            title = build_pr_title(
                session_prompt=str(session_prompt or request.user_request or ""),
                slice_spec=slice_spec if isinstance(slice_spec, Mapping) else None,
                branch_hint=context.branch,
                issue_number=request.issue_number,
            )
        except Exception:  # noqa: BLE001 — never block PR on title helper
            title = (
                f"📝 #{request.issue_number} coding-executor draft"
                if request.issue_number
                else f"📝 coding-executor draft — {context.branch}"
            )
        body = _draft_pr_body(request, context)

        # P1-N — cross-repo PR title + issue anchor hard guard.
        # 옛 wiring 은 "coding-executor draft #4" 같은 기계 제목 / issue
        # 없는 PR 가 그대로 GitHub 로 흘러갔다. 본 가드가 PR 생성 직전
        # raise 해서 다음 PR 부터는 위반 자체가 막힌다.
        try:
            from ..governance.repo_write_policy import (
                IssueAnchorContext,
                enforce_issue_anchor,
                enforce_pr_title,
            )

            enforce_pr_title(title)
            enforce_issue_anchor(
                IssueAnchorContext(
                    branch=context.branch,
                    pr_body=body,
                    issue_number_hint=request.issue_number,
                )
            )
        except Exception as policy_exc:  # noqa: BLE001 — surface as RuntimeError
            # Re-raise so worker maps to REASON_PR_FAILED with policy detail.
            # PolicyViolation 의 reason/detail 이 그대로 worker progress
            # marker 에 노출됨.
            raise

        # P0-T: runtime governance policy gate — PR body 가 5 섹션 +
        # audit block 을 갖는지 검사. caller-driven gate 원칙: validation
        # 결과를 로그/audit 으로 남기되 PR 생성 자체는 진행한다 (operator
        # 가 status 에서 즉시 확인 후 후속 PR 에서 보강 가능).
        try:
            from ..governance.runtime_policy import validate_pr_body

            pr_validation = validate_pr_body(body)
            if not pr_validation.ok:
                logger.warning(
                    "draft PR body policy warning — missing=%s, audit=%s, warnings=%s",
                    pr_validation.missing_sections,
                    pr_validation.audit_block_present,
                    pr_validation.warnings,
                )
        except Exception:  # noqa: BLE001 — never block PR on validator
            pass

        pr_response = self._live.create_draft_pull_request(
            repo=repo,
            head=context.branch,
            base=request.base_branch or "main",
            title=title,
            body=body,
            draft=True,
        )
        pr_number = int(pr_response.get("number") or 0)
        pr_url = str(pr_response.get("html_url") or "")
        return replace(context, pr_number=pr_number or None, pr_url=pr_url)


def _draft_pr_body(
    request: CodingExecuteRequest, context: WorktreeContext
) -> str:
    """draft PR body. P0-T runtime_policy.validate_pr_body 통과하도록
    5 섹션 (purpose / scope / risks / tests / issue_linkage) + audit block
    을 모두 갖춘다."""

    test_summary = context.test_summary or {}
    test_status = (
        test_summary.get("status")
        if isinstance(test_summary, Mapping)
        else None
    ) or ("dry_run" if test_summary.get("dry_run") else "unknown")

    parts = [
        "## 📌 관련 이슈",
        f"- close #{request.issue_number}" if request.issue_number else "- (no issue)",
        "",
        "## ✨ 과제 내용 (목적)",
        f"- coding_execute job (executor=`{request.executor_role}`) 산출.",
        "- 본 PR 은 `RecordOnlyCodeEditor` 가 만든 계획 markdown 만 포함합니다 — 실 LLM 편집은 운영자 승인 후 별도.",
        "",
        "## 🎯 범위 (scope)",
        f"- in_scope: write_scope={list(request.write_scope) or '(미지정)'}",
        f"- out_of_scope: forbidden_scope={list(request.forbidden_scope) or '(미지정)'}",
        "",
        "## ⚠️ 리스크 (risks)",
        "- safety_rules 준수: " + (", ".join(request.safety_rules) if request.safety_rules else "(미지정)"),
        "- live editor 미연결 — 본 PR 은 record-only. operator 검토 후 후속 PR 에서 실 편집 land 예정.",
        "",
        "## ✅ 테스트 (tests)",
        f"- test_status: `{test_status}`",
        f"- test_summary: `{dict(test_summary) if isinstance(test_summary, Mapping) else test_summary}`",
        "",
        "## :camera_with_flash: 스크린샷(선택)",
        "_(N/A)_",
        "",
        "## 📚 참고 (references)",
        f"- session_id: `{request.session_id}`",
        f"- branch: `{context.branch}` (from `{request.base_branch}`)",
        f"- commit: `{context.commit_sha[:10] if context.commit_sha else '-'}`",
        "",
        "## 🤖 Agent WorkOS Audit",
        f"- branch: `{context.branch}` (from `{request.base_branch}`)",
        f"- repo: `{request.repo_full_name}`",
        f"- role: `{request.executor_role}`",
        f"- engineering-agent runtime_policy: branch/PR/tag hard rails 적용",
        "- mode: `live` (G6 LiveGithubAppClient — RecordOnly editor)",
        "- merge: do-not-merge until operator review",
    ]
    return "\n".join(parts) + "\n"


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
)


# ---------------------------------------------------------------------------
# F4 / #91 — Live LLM editor MVP
#
# Scope of this PR (intentionally minimal):
#
#   * :class:`CodeEditPort` — Protocol the worker can swap in place of
#     :class:`RecordOnlyCodeEditor`.
#   * :class:`BlockedLiveEditorError` — single exception type raised
#     when env gates / operator authorization / PasteGuard refuses
#     the call.
#   * :class:`LiveCodeEditor` — env-gated wrapper that:
#       1. Hard rail: if ``YULE_LIVE_EDITOR_ENABLED != "true"`` the
#          editor blocks immediately. This stays default-off even
#          after the PR lands — operator must flip the flag.
#       2. PasteGuard preflight on the outbound prompt; ``blocked``
#          → :class:`BlockedLiveEditorError`.
#       3. Provider dispatch:
#            ``claude-cli`` → subprocess call (default impl
#            attempts ``import subprocess`` only; the worker may
#            inject a fake runner under test).
#            ``anthropic`` / ``openai`` → blocked stub (operator
#            authorization + cost-budget gate, D-73-10).
#
# TODO (follow-up PRs, deliberately out of scope here):
#
#   * patch validation against write_scope / forbidden_scope
#   * test-first retry loop (max_retries env exposed but unused)
#   * cost tracking + per-session budget enforcement
#   * Anthropic / OpenAI SDK wiring (operator authorization gate)
#
# Hard rails enforced *in this PR* (regression-tested):
#
#   * Default OFF — ``build_live_editor_from_env({})`` returns None.
#   * Anthropic / OpenAI providers raise BlockedLiveEditorError.
#   * PasteGuard fail-closed — raw secret in prompt blocks the call.
#   * protected branch guard remains via
#     :func:`coding_executor_worker.is_protected_branch` (not
#     duplicated here; the worker invokes it before the editor).
# ---------------------------------------------------------------------------


ENV_LIVE_EDITOR_ENABLED: str = "YULE_LIVE_EDITOR_ENABLED"
ENV_LIVE_EDITOR_PROVIDER: str = "YULE_LIVE_EDITOR_PROVIDER"
ENV_LIVE_EDITOR_MODEL: str = "YULE_LIVE_EDITOR_MODEL"
ENV_LIVE_EDITOR_MAX_RETRIES: str = "YULE_LIVE_EDITOR_MAX_RETRIES"

PROVIDER_CLAUDE_CLI: str = "claude-cli"
PROVIDER_ANTHROPIC: str = "anthropic"
PROVIDER_OPENAI: str = "openai"

_DEFAULT_LIVE_EDITOR_MODEL: str = "claude-sonnet-4-6"
_DEFAULT_LIVE_EDITOR_MAX_RETRIES: int = 3


class BlockedLiveEditorError(RuntimeError):
    """Raised when :class:`LiveCodeEditor` refuses to execute a call.

    ``reason`` is operator-facing: env OFF, provider not authorized
    (anthropic / openai blocked stub), PasteGuard verdict blocked,
    or runtime resource missing (e.g. ``claude`` CLI not on PATH).

    The exception never carries the raw outbound prompt — callers
    log ``str(exc)`` directly without leaking the LLM input.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CodeEditPort(Protocol):
    """Protocol the worker depends on for the editor seam.

    :class:`RecordOnlyCodeEditor` and :class:`LiveCodeEditor` both
    satisfy this — the build factory picks one based on env. The
    contract is intentionally narrow so future implementations
    (e.g. codex CLI, GitHub Copilot CLI) can slot in unchanged.
    """

    def apply(
        self,
        *,
        request: CodingExecuteRequest,
        context: WorktreeContext,
    ) -> WorktreeContext:  # pragma: no cover - Protocol
        ...


class LiveCodeEditor:
    """Env-gated live LLM editor — MVP (claude-cli only).

    The constructor never reads env directly; use
    :func:`build_live_editor_from_env` so the env contract stays in
    one place and tests can construct the editor with explicit
    arguments.

    Provider matrix (MVP):

      * ``claude-cli`` — shells out to ``claude -p <prompt>`` via
        the injected ``subprocess_runner`` (default attempts a
        local ``subprocess.run`` call; under test the worker
        passes a fake). The default-off env flag and the
        PasteGuard preflight gate every call.
      * ``anthropic`` / ``openai`` — raises
        :class:`BlockedLiveEditorError` with reason
        ``"requires operator authorization"``. This keeps the
        D-73-10 cost-budget gate intact: live SDK wiring lands
        in a separate PR after operator sign-off.

    TODO (follow-up PRs): patch validation against write_scope,
    test-first retry loop, cost tracking.
    """

    def __init__(
        self,
        *,
        provider: str,
        model: str = _DEFAULT_LIVE_EDITOR_MODEL,
        max_retries: int = _DEFAULT_LIVE_EDITOR_MAX_RETRIES,
        subprocess_runner: Optional[Any] = None,
        http_poster: Optional[Any] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.max_retries = max_retries
        self._subprocess_runner = subprocess_runner
        self._http_poster = http_poster
        # Snapshot env so re-running apply does not silently flip
        # behaviour if the operator flips the flag mid-pipeline.
        self._env: Mapping[str, str] = dict(env) if env is not None else dict(os.environ)

    def apply(
        self,
        *,
        request: CodingExecuteRequest,
        context: WorktreeContext,
    ) -> WorktreeContext:
        # Hard rail 1 — env OFF default.
        if (self._env.get(ENV_LIVE_EDITOR_ENABLED) or "").strip().lower() != "true":
            raise BlockedLiveEditorError(
                f"{ENV_LIVE_EDITOR_ENABLED} != 'true' — live editor disabled"
            )

        # Hard rail 2 — PasteGuard preflight on the outbound prompt.
        # Imported lazily so the module stays importable in
        # environments that strip the security subpackage (e.g.
        # the planning-agent worker that never touches LLM I/O).
        from yule_orchestrator.agents.security.paste_guard import (
            OutboundChannel,
            guard_outbound,
        )

        verdict = guard_outbound(
            channel=OutboundChannel.LLM,
            payload=request.generated_prompt or "",
        )
        if verdict.blocked:
            raise BlockedLiveEditorError(
                "PasteGuard blocked outbound prompt — refusing live LLM call"
            )

        # Hard rail 3 — provider dispatch.
        if self.provider == PROVIDER_CLAUDE_CLI:
            return self._apply_via_claude_cli(
                request=request,
                context=context,
                redacted_prompt=verdict.redacted,
            )
        if self.provider in (PROVIDER_ANTHROPIC, PROVIDER_OPENAI):
            raise BlockedLiveEditorError(
                f"provider={self.provider} requires operator authorization "
                "(D-73-10 cost-budget gate)"
            )
        raise BlockedLiveEditorError(
            f"unknown live editor provider: {self.provider!r}"
        )

    # ------------------------------------------------------------------
    # Provider — claude CLI
    # ------------------------------------------------------------------

    def _apply_via_claude_cli(
        self,
        *,
        request: CodingExecuteRequest,
        context: WorktreeContext,
        redacted_prompt: str,
    ) -> WorktreeContext:
        if not context.worktree_path:
            raise BlockedLiveEditorError(
                "LiveCodeEditor requires a worktree_path in context"
            )

        runner = self._subprocess_runner
        if runner is None:
            # Default impl: best-effort attempt at locating the
            # ``claude`` binary. We intentionally do *not* fall back
            # to ``subprocess.run`` here — operators wire the runner
            # via :class:`ClaudeSubprocessAdapter` (separate PR).
            try:
                import subprocess as _subprocess  # noqa: F401 — import-only probe
            except Exception as exc:  # pragma: no cover - defensive
                raise BlockedLiveEditorError(
                    f"claude-cli runner unavailable: {type(exc).__name__}"
                ) from exc
            raise BlockedLiveEditorError(
                "claude-cli runner not injected — operator must wire "
                "subprocess_runner before enabling live editor"
            )

        # Pass the *redacted* payload — never the raw prompt. The
        # redaction is round-trip safe (head4 + mask + tail4) so the
        # LLM still has enough context to act, but a leaked secret
        # in the prompt cannot reach the network.
        cmd = ("claude", "-p", redacted_prompt, "--model", self.model)
        result = runner(cmd, cwd=context.worktree_path)
        # The runner is operator-defined; we accept any truthy
        # return shape. The MVP only verifies the call did not
        # raise. Patch validation / file diffing lands in a
        # follow-up PR (TODO).
        _ = result
        return context


def build_live_editor_from_env(
    env: Mapping[str, str],
    *,
    http_poster: Optional[Any] = None,
    subprocess_runner: Optional[Any] = None,
) -> Optional[CodeEditPort]:
    """Construct a :class:`LiveCodeEditor` from env, or return ``None``.

    Returns ``None`` (NOT an error) when:

      * ``YULE_LIVE_EDITOR_ENABLED`` is unset / not ``"true"``.
      * ``YULE_LIVE_EDITOR_PROVIDER`` is unset / empty.

    The worker treats ``None`` as "fall back to RecordOnly" so the
    pipeline stays exercisable end-to-end even with the live editor
    completely off. When the env says ON but the provider is
    ``anthropic`` / ``openai``, the returned editor still raises
    :class:`BlockedLiveEditorError` on ``apply`` — that is the
    intended D-73-10 cost-budget gate.

    TODO (follow-up PRs): validate model id against an allow-list,
    surface availability via :class:`LiveExecutorAvailability`.
    """

    if (env.get(ENV_LIVE_EDITOR_ENABLED) or "").strip().lower() != "true":
        return None

    provider = (env.get(ENV_LIVE_EDITOR_PROVIDER) or "").strip().lower()
    if not provider:
        return None

    model = (env.get(ENV_LIVE_EDITOR_MODEL) or "").strip() or _DEFAULT_LIVE_EDITOR_MODEL
    max_retries_raw = (env.get(ENV_LIVE_EDITOR_MAX_RETRIES) or "").strip()
    try:
        max_retries = int(max_retries_raw) if max_retries_raw else _DEFAULT_LIVE_EDITOR_MAX_RETRIES
    except ValueError:
        max_retries = _DEFAULT_LIVE_EDITOR_MAX_RETRIES

    return LiveCodeEditor(
        provider=provider,
        model=model,
        max_retries=max_retries,
        subprocess_runner=subprocess_runner,
        http_poster=http_poster,
        env=env,
    )
