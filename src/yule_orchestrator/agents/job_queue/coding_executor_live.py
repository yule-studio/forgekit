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
import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

from .coding_executor_worker import (
    CodingExecuteRequest,
    WorktreeContext,
)


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


class LocalGitWorktreeProvisioner:
    """``git worktree add`` based provisioner.

    Defaults the worktree root to ``DEFAULT_WORKTREE_ROOT`` so the
    executor's checkouts live outside the main repo path. Each call
    creates a fresh worktree at ``<root>/<branch-slug>`` and tracks
    the path so :meth:`cleanup` can remove it after the pipeline
    finishes (success *or* failure).
    """

    def __init__(
        self,
        *,
        repo_root: str,
        worktree_root: Optional[str] = None,
        runner: Optional[Any] = None,
    ) -> None:
        self.repo_root = str(repo_root)
        self.worktree_root = str(worktree_root or DEFAULT_WORKTREE_ROOT)
        self._runner = runner or _run_subprocess
        self._provisioned: list[str] = []

    def provision(
        self, *, request: CodingExecuteRequest, branch: str
    ) -> WorktreeContext:
        slug = _slugify(branch)
        target = Path(self.worktree_root) / slug
        if target.exists():
            # Stale worktree from a previous failure; remove safely.
            self._safe_remove_existing(target)
        target.parent.mkdir(parents=True, exist_ok=True)

        base_sha = self._get_base_sha(request.base_branch)
        # Fresh branch from the resolved base.
        self._runner(
            [
                "git",
                "-C",
                self.repo_root,
                "worktree",
                "add",
                "-b",
                branch,
                str(target),
                base_sha,
            ]
        )
        self._provisioned.append(str(target))
        return WorktreeContext(
            branch=branch,
            worktree_path=str(target),
            base_commit_sha=base_sha,
        )

    def cleanup(self, *, force: bool = False) -> None:
        for path in list(self._provisioned):
            try:
                self._runner(
                    [
                        "git",
                        "-C",
                        self.repo_root,
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
            self._provisioned.remove(path)

    def _get_base_sha(self, base_branch: str) -> str:
        result = self._runner(
            ["git", "-C", self.repo_root, "rev-parse", base_branch],
            capture_output=True,
        )
        return (result.stdout or "").strip()

    def _safe_remove_existing(self, target: Path) -> None:
        # Try clean removal via git first; fallback to filesystem.
        try:
            self._runner(
                ["git", "-C", self.repo_root, "worktree", "remove", "--force", str(target)]
            )
        except _SubprocessError:
            shutil.rmtree(target, ignore_errors=True)


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
        cmd = list(_resolve_test_command(request) or self.default_command)
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
            },
        )


def _resolve_test_command(request: CodingExecuteRequest) -> Optional[Sequence[str]]:
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
        title = (
            f"📝 #{request.issue_number} coding-executor draft"
            if request.issue_number
            else f"📝 coding-executor draft — {context.branch}"
        )
        body = _draft_pr_body(request, context)
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
    parts = [
        "## 📌 관련 이슈",
        f"- close #{request.issue_number}" if request.issue_number else "- (no issue)",
        "",
        "## ✨ 과제 내용",
        f"- coding_execute job (executor=`{request.executor_role}`) 의 dry pipeline 산출.",
        "- 본 PR 은 `RecordOnlyCodeEditor` 가 만든 계획 markdown 만 포함합니다 — 실 LLM 편집은 운영자 승인 후 별도.",
        "",
        "## :camera_with_flash: 스크린샷(선택)",
        "_(N/A)_",
        "",
        "## 📚 레퍼런스 (또는 새로 알게 된 내용) 혹은 궁금한 사항들",
        f"- session_id: `{request.session_id}`",
        f"- branch: `{context.branch}` (from `{request.base_branch}`)",
        f"- commit: `{context.commit_sha[:10] or '-'}`",
        "",
        "## 🤖 Agent WorkOS Audit",
        f"- branch: `{context.branch}` (from `{request.base_branch}`)",
        f"- repo: `{request.repo_full_name}`",
        f"- role: `{request.executor_role}`",
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
    """Inspect environment + injected resources, return an availability summary."""

    pusher = "github_app" if live_client is not None else "blocked"
    pr = "github_app" if live_client is not None else "blocked"
    pusher_blocker = "" if live_client else "LiveGithubAppClient 미주입 (.env.local 의 YULE_GITHUB_APP_* 필요)"
    pr_blocker = pusher_blocker
    return LiveExecutorAvailability(
        worktree_provisioner=bool(repo_root),
        code_editor="record_only",
        code_editor_blocker=(
            "live LLM editor (claude / codex CLI + secret) 미연결 — "
            "운영자 승인 후 별도 PR 에서 ClaudeCodeCodeEditor 등 추가"
        ),
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
        "code_editor": RecordOnlyCodeEditor(),
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
    "LocalGitWorktreeProvisioner",
    "RecordOnlyCodeEditor",
    "SubprocessTestRunner",
    "build_live_executor",
    "detect_live_executor_availability",
)
