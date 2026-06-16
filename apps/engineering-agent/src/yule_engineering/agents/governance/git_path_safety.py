"""Repo-local git write safety — never run a write at HOME / ambiguous path.

Hard rail born from a real incident: an automated git write executed with the
wrong working directory (HOME) and a broad ``git add .`` staged the entire home
tree. This module makes that class of accident *unrepresentable* for repo-local
automation:

  * :func:`assert_safe_git_repo_path` — refuses an empty / ``"."`` / ``"~"`` /
    relative / non-existent / non-git-repo path, and refuses ``$HOME`` itself or
    any ancestor of ``$HOME`` (too broad to ever be a safe write target).
  * :func:`assert_not_broad_stage` — refuses ``git add .`` / ``-A`` / ``--all``
    / ``:/`` style broad staging. Automation must stage explicit pathspecs.
  * :func:`safe_git_argv` / :func:`run_safe_git` — build/run git as
    ``git -C <validated-repo> <args>`` so the caller's cwd is irrelevant.

The policy is repo-local only: it never touches global/system git config and
only validates the *target* path a write would mutate.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence


class UnsafeGitPathError(RuntimeError):
    """Raised when a git write target path is HOME / ambiguous / not a repo."""


class BroadStageError(RuntimeError):
    """Raised when git argv would broadly stage (``add .`` / ``-A`` / ``--all``)."""


# Tokens that broadly stage the whole tree — banned for automation.
_BROAD_STAGE_TOKENS: frozenset[str] = frozenset({".", "-A", "--all", "*", ":/", "./"})
# Subcommands that write to the index/working tree (path-safety required).
WRITE_SUBCOMMANDS: frozenset[str] = frozenset(
    {"add", "commit", "checkout", "switch", "reset", "rm", "mv", "clean", "restore", "push", "stash"}
)


def home_dir() -> Path:
    return Path(os.path.expanduser("~")).resolve()


def assert_safe_git_repo_path(path: object) -> Path:
    """Validate *path* as a safe git write target; return its resolved Path.

    Raises :class:`UnsafeGitPathError` for any of: None / empty / ``"."`` /
    ``"~"`` / relative / non-existent / not-a-git-repo / equal to ``$HOME`` /
    an ancestor of ``$HOME``.
    """

    if path is None:
        raise UnsafeGitPathError("git repo path is None")
    raw = str(path).strip()
    if raw in {"", ".", "~", "./", "~/"}:
        raise UnsafeGitPathError(f"ambiguous git repo path: {raw!r}")

    expanded = Path(os.path.expanduser(raw))
    if not expanded.is_absolute():
        raise UnsafeGitPathError(
            f"git repo path must be absolute (got relative {raw!r}); "
            "use an explicit repo root, never a relative/ambiguous cwd"
        )
    resolved = expanded.resolve()
    home = home_dir()

    if resolved == home:
        raise UnsafeGitPathError(
            f"refusing git write at HOME ({home}) — never a safe target"
        )
    # If $HOME is *inside* resolved, then resolved is an ancestor of HOME
    # (e.g. '/' or '/Users') — far too broad.
    try:
        home.relative_to(resolved)
        raise UnsafeGitPathError(
            f"refusing git write at an ancestor of HOME ({resolved}) — too broad"
        )
    except ValueError:
        pass

    if not resolved.exists():
        raise UnsafeGitPathError(f"git repo path does not exist: {resolved}")
    if not (resolved / ".git").exists():
        raise UnsafeGitPathError(f"not a git repository (no .git): {resolved}")
    return resolved


def assert_not_broad_stage(args: Sequence[str]) -> None:
    """Refuse argv that would broadly stage the whole tree.

    Applies to ``add`` (any broad token) and to a bare ``commit -a`` /
    ``commit --all`` (stages all tracked modifications repo-wide).
    """

    argv = [str(a) for a in args]
    if not argv:
        return
    sub = argv[0]
    if sub == "add":
        for tok in argv[1:]:
            if tok in _BROAD_STAGE_TOKENS:
                raise BroadStageError(
                    f"broad stage refused: `git add {tok}`. "
                    "Automation must stage explicit pathspecs (git add -- <path>)."
                )
    if sub == "commit":
        for tok in argv[1:]:
            if tok in {"-a", "--all"}:
                raise BroadStageError(
                    "broad stage refused: `git commit -a/--all` stages all "
                    "tracked changes. Stage explicit pathspecs first."
                )


def safe_git_argv(repo_root: object, args: Sequence[str]) -> list[str]:
    """Return ``["git", "-C", <validated-repo>, *args]`` or raise.

    Validates the repo path (HOME/ambiguous guard) and the args (broad-stage
    guard) before producing the argv. The cwd of the caller is irrelevant.
    """

    resolved = assert_safe_git_repo_path(repo_root)
    assert_not_broad_stage(args)
    return ["git", "-C", str(resolved), *[str(a) for a in args]]


def run_safe_git(
    repo_root: object,
    args: Sequence[str],
    *,
    check: bool = False,
    capture_output: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess:
    """Run a validated ``git -C <repo> <args>``. Never inherits an ambiguous cwd."""

    cmd = safe_git_argv(repo_root, args)
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")  # fail fast, never hang on prompts
    return subprocess.run(
        cmd, check=check, capture_output=capture_output, text=text, env=env
    )


__all__ = (
    "UnsafeGitPathError",
    "BroadStageError",
    "WRITE_SUBCOMMANDS",
    "home_dir",
    "assert_safe_git_repo_path",
    "assert_not_broad_stage",
    "safe_git_argv",
    "run_safe_git",
)
