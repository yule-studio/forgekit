"""Optional vault git auto-commit layer for Obsidian sync.

Sits next to :mod:`yule_engineering.agents.obsidian.writer`. The writer
puts a Markdown file into the vault; this module — only when the caller
opts in via ``--git-commit`` — stages that one file and commits it to
the vault's git repository.

Design constraints (encoded as code, not just docs):

- Never runs ``git push``.
- Stages exactly one file (the synced note); never ``git add .`` /
  ``git add -A``. Other changes in the working tree stay untouched.
- Refuses to run when the vault repo already has staged changes —
  committing on top of a half-staged state would mix unrelated work into
  one commit.
- All git invocations target the resolved ``git_repo_root`` via
  ``git -C <root> ...`` so the working directory of the caller is
  irrelevant.
- Failures surface as :class:`ObsidianGitError` with stderr captured.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence


class ObsidianGitError(RuntimeError):
    """Raised when a vault auto-commit cannot be honored safely."""


@dataclass(frozen=True)
class CommitResult:
    """Outcome of one auto-commit attempt.

    ``committed`` is False when there was nothing to commit (idempotent
    sync) or when ``dry_run`` short-circuited the actual git calls.
    ``commit_sha`` is None for both no-op and dry-run cases.
    """

    committed: bool
    commit_sha: Optional[str]
    repo_root: Path
    target_path: Path
    message: str
    dry_run: bool
    no_changes: bool = False


def find_git_repo_root(start: Path) -> Optional[Path]:
    """Return the nearest ancestor (inclusive) that contains ``.git``.

    Returns ``None`` if no git repository is found above ``start``.
    Uses a file/directory check rather than running ``git`` so callers
    can ask the question without spawning a subprocess.
    """

    current = start.resolve()
    for candidate in (current, *current.parents):
        marker = candidate / ".git"
        if marker.exists():
            return candidate
    return None


def is_git_repo(path: Path) -> bool:
    """True when ``path`` is inside (or equal to) a git working tree."""

    return find_git_repo_root(path) is not None


def commit_single_file(
    repo_root: Path,
    target_path: Path,
    *,
    message: str,
    dry_run: bool = False,
) -> CommitResult:
    """Stage and commit exactly ``target_path`` into ``repo_root``.

    The repo must have no pre-existing staged changes; this guard makes
    sure the auto-commit never bundles unrelated work into the sync
    commit. Other unstaged changes in the working tree are left alone.

    Raises :class:`ObsidianGitError` for any policy or git failure.
    """

    if not message.strip():
        raise ObsidianGitError("git commit message must not be empty.")

    repo_root_resolved = repo_root.resolve()
    target_resolved = target_path.resolve()

    try:
        target_resolved.relative_to(repo_root_resolved)
    except ValueError as exc:
        raise ObsidianGitError(
            f"Refusing to commit a file outside the vault repo. "
            f"repo={repo_root_resolved} target={target_resolved}"
        ) from exc

    staged = _staged_paths(repo_root_resolved)
    if staged:
        joined = ", ".join(staged[:5])
        more = f" (+{len(staged) - 5} more)" if len(staged) > 5 else ""
        raise ObsidianGitError(
            "vault repo has pre-existing staged changes — auto-commit "
            f"would mix them into the sync commit: {joined}{more}. "
            "Commit or unstage them first, or rerun without --git-commit."
        )

    if dry_run:
        return CommitResult(
            committed=False,
            commit_sha=None,
            repo_root=repo_root_resolved,
            target_path=target_resolved,
            message=message,
            dry_run=True,
        )

    add_proc = _run_git(repo_root_resolved, ["add", "--", str(target_resolved)])
    if add_proc.returncode != 0:
        raise ObsidianGitError(
            f"git add failed: {add_proc.stderr.strip() or add_proc.stdout.strip() or 'unknown error'}"
        )

    if not _staged_paths(repo_root_resolved):
        return CommitResult(
            committed=False,
            commit_sha=None,
            repo_root=repo_root_resolved,
            target_path=target_resolved,
            message=message,
            dry_run=False,
            no_changes=True,
        )

    commit_proc = _run_git(
        repo_root_resolved,
        ["commit", "-m", message, "--", str(target_resolved)],
    )
    if commit_proc.returncode != 0:
        raise ObsidianGitError(
            f"git commit failed: {commit_proc.stderr.strip() or commit_proc.stdout.strip() or 'unknown error'}"
        )

    sha_proc = _run_git(repo_root_resolved, ["rev-parse", "HEAD"])
    sha = sha_proc.stdout.strip() if sha_proc.returncode == 0 else None

    return CommitResult(
        committed=True,
        commit_sha=sha,
        repo_root=repo_root_resolved,
        target_path=target_resolved,
        message=message,
        dry_run=False,
    )


def _run_git(repo_root: Path, args: Sequence[str]) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(repo_root), *args]
    env = dict(os.environ)
    # Block any interactive prompts (auth, GPG passphrase, etc.) so the
    # auto-commit fails fast with a readable error instead of hanging.
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return subprocess.run(
        cmd,
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )


def _staged_paths(repo_root: Path) -> list[str]:
    proc = _run_git(repo_root, ["diff", "--cached", "--name-only"])
    if proc.returncode != 0:
        raise ObsidianGitError(
            f"git diff --cached failed: {proc.stderr.strip() or 'unknown error'}"
        )
    return [line for line in proc.stdout.splitlines() if line.strip()]
