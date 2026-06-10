"""Worktree provisioner for the live coding executor.

Split out of :mod:`coding_executor_live` (responsibility: *live
runner — worktree provisioning*). Behavior-preserving move; the
original module re-exports every public symbol so importers stay
unchanged.

Dependency direction is one-way: this module imports the shared
subprocess + slug helpers (``_run_subprocess`` / ``_SubprocessError``
/ ``_slugify`` / ``_tail``) from :mod:`coding_executor_live`, which
defines them before re-exporting these provisioner symbols at the
bottom of its body — so there is no import-time cycle.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

from .coding_executor_worker import (
    CodingExecuteRequest,
    WorktreeContext,
)
from .coding_executor_live import (
    DEFAULT_WORKTREE_ROOT,
    _run_subprocess,
    _slugify,
    _tail,
    _SubprocessError,
)


logger = logging.getLogger(__name__)


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
