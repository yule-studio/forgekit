"""Worktree-root hygiene — detect stale worktrees, dry-run cleanup, disk view.

Coding-executor and self-improve runs each provision throwaway git worktrees
under a *root*:

  * coding executor → ``/tmp/yule-coding-executor-worktrees`` (env
    ``YULE_CODING_EXECUTOR_WORKTREE_ROOT``)
  * self-improve → ``<repo>/.cache/yule/self-improve-worktrees`` (env
    ``YULE_SELF_IMPROVEMENT_WORKTREE_ROOT``)

If a run dies mid-flight the child directory leaks. This module finds those
abandoned directories *on disk* (the in-memory registry detector in
``lifecycle.self_improvement_worktree`` only sees what a live process tracked)
and offers a **dry-run-by-default** cleanup.

Hard safety rails (mirrors :mod:`git_path_safety`):
  * Cleanup only ever touches a *direct child* of an allowlisted worktree root.
  * It refuses to remove the root itself, ``$HOME``, an ancestor of ``$HOME``,
    or the repo working tree / its ``.git``.
  * Nothing is removed unless the caller passes ``apply=True``; the default
    surface is a plan. No global/system path is ever read or written.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence, Tuple

from .git_path_safety import home_dir

ENV_CODING_EXECUTOR_WORKTREE_ROOT = "YULE_CODING_EXECUTOR_WORKTREE_ROOT"
ENV_SELF_IMPROVEMENT_WORKTREE_ROOT = "YULE_SELF_IMPROVEMENT_WORKTREE_ROOT"
DEFAULT_CODING_EXECUTOR_ROOT = "/tmp/yule-coding-executor-worktrees"
DEFAULT_SELF_IMPROVEMENT_ROOT_REL = ".cache/yule/self-improve-worktrees"


class UnsafeCleanupError(RuntimeError):
    """Raised when a cleanup target is not a safe allowlisted worktree child."""


def allowlisted_roots(
    repo_root: object, *, env: Optional[Mapping[str, str]] = None
) -> Tuple[Path, ...]:
    """Return the resolved worktree roots cleanup is *allowed* to sweep.

    Repo-outside cleanup (``/tmp/...``) is permitted ONLY because the path is a
    known worktree root from this allowlist — never an arbitrary path.
    """

    source = env if env is not None else os.environ
    repo = Path(os.path.expanduser(str(repo_root))).resolve()
    coding = (source.get(ENV_CODING_EXECUTOR_WORKTREE_ROOT) or "").strip() or DEFAULT_CODING_EXECUTOR_ROOT
    selfimp = (source.get(ENV_SELF_IMPROVEMENT_WORKTREE_ROOT) or "").strip()
    roots: list[Path] = [Path(os.path.expanduser(coding)).resolve()]
    if selfimp:
        roots.append(Path(os.path.expanduser(selfimp)).resolve())
    else:
        roots.append((repo / DEFAULT_SELF_IMPROVEMENT_ROOT_REL).resolve())
    # de-dup while preserving order
    seen: set[Path] = set()
    unique: list[Path] = []
    for r in roots:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return tuple(unique)


def assert_safe_cleanup_target(
    target: object, *, allow_roots: Sequence[Path], repo_root: object
) -> Path:
    """Validate *target* as a removable stale-worktree directory; return it.

    Refuses anything that is not a direct child of an allowlisted root, plus
    HOME / HOME-ancestor / the repo / a ``.git`` directory.
    """

    if target is None:
        raise UnsafeCleanupError("cleanup target is None")
    raw = str(target).strip()
    if raw in {"", ".", "~", "/", "./", "~/"}:
        raise UnsafeCleanupError(f"ambiguous cleanup target: {raw!r}")
    resolved = Path(os.path.expanduser(raw)).resolve()
    home = home_dir()
    repo = Path(os.path.expanduser(str(repo_root))).resolve()

    if resolved == home:
        raise UnsafeCleanupError(f"refusing to remove HOME ({home})")
    try:  # resolved is an ancestor of HOME (e.g. '/', '/Users') => too broad
        home.relative_to(resolved)
        raise UnsafeCleanupError(f"refusing to remove an ancestor of HOME ({resolved})")
    except ValueError:
        pass
    if resolved == repo or resolved == repo / ".git":
        raise UnsafeCleanupError(f"refusing to remove the repo / its .git ({resolved})")
    if resolved.name == ".git":
        raise UnsafeCleanupError(f"refusing to remove a .git directory ({resolved})")

    roots = [Path(r).resolve() for r in allow_roots]
    if resolved in roots:
        raise UnsafeCleanupError(f"refusing to remove a worktree ROOT itself ({resolved})")
    if not any(resolved.parent == r for r in roots):
        raise UnsafeCleanupError(
            f"cleanup target {resolved} is not a direct child of an allowlisted "
            f"worktree root {tuple(str(r) for r in roots)}"
        )
    return resolved


@dataclass(frozen=True)
class StaleWorktreeDir:
    """A worktree directory on disk that looks abandoned."""

    path: Path
    age_seconds: float
    reason: str  # "older_than_threshold" | "unreadable_mtime"


def detect_stale_worktree_dirs(
    root: object,
    *,
    now: Optional[datetime] = None,
    stale_after_seconds: int = 24 * 3600,
    active_paths: Iterable[object] = (),
) -> Tuple[StaleWorktreeDir, ...]:
    """Return direct child dirs of *root* that look abandoned.

    Stale := mtime older than ``stale_after_seconds`` AND not in ``active_paths``.
    A directory whose mtime cannot be read is flagged ``unreadable_mtime`` so it
    surfaces rather than silently lingering. Missing root → empty (no error).
    """

    root_path = Path(os.path.expanduser(str(root))).resolve()
    if not root_path.exists() or not root_path.is_dir():
        return ()
    when = (now or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
    active = {Path(os.path.expanduser(str(p))).resolve() for p in active_paths}
    out: list[StaleWorktreeDir] = []
    for child in sorted(root_path.iterdir()):
        if not child.is_dir():
            continue
        if child.resolve() in active:
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            out.append(StaleWorktreeDir(path=child, age_seconds=float(stale_after_seconds), reason="unreadable_mtime"))
            continue
        age = when.timestamp() - mtime
        if age < stale_after_seconds:
            continue
        out.append(StaleWorktreeDir(path=child, age_seconds=age, reason="older_than_threshold"))
    return tuple(out)


@dataclass(frozen=True)
class CleanupPlan:
    """Dry-run-by-default cleanup outcome.

    ``removed`` is populated only when ``apply=True``; otherwise everything sits
    in ``would_remove`` and the disk is untouched.
    """

    applied: bool
    would_remove: Tuple[Path, ...] = ()
    removed: Tuple[Path, ...] = ()
    refused: Tuple[Tuple[Path, str], ...] = ()

    def render(self) -> str:
        verb = "removed" if self.applied else "would remove"
        lines = [f"worktree cleanup ({'apply' if self.applied else 'dry-run'}):"]
        targets = self.removed if self.applied else self.would_remove
        lines.append(f"  {verb}: {len(targets)}")
        for p in targets:
            lines.append(f"    - {p}")
        if self.refused:
            lines.append(f"  refused: {len(self.refused)}")
            for p, why in self.refused:
                lines.append(f"    ! {p}: {why}")
        return "\n".join(lines)


def plan_worktree_cleanup(
    stale: Sequence[StaleWorktreeDir],
    *,
    repo_root: object,
    allow_roots: Sequence[Path],
    apply: bool = False,
) -> CleanupPlan:
    """Plan (and only with ``apply=True``, perform) removal of stale worktrees.

    Each target is re-validated through :func:`assert_safe_cleanup_target`; a
    target that fails validation is recorded under ``refused`` and never removed,
    even when ``apply=True``.
    """

    would: list[Path] = []
    removed: list[Path] = []
    refused: list[Tuple[Path, str]] = []
    for entry in stale:
        try:
            safe = assert_safe_cleanup_target(
                entry.path, allow_roots=allow_roots, repo_root=repo_root
            )
        except UnsafeCleanupError as exc:
            refused.append((Path(entry.path), str(exc)))
            continue
        would.append(safe)
        if apply:
            shutil.rmtree(safe, ignore_errors=True)
            removed.append(safe)
    return CleanupPlan(
        applied=apply,
        would_remove=tuple(would),
        removed=tuple(removed),
        refused=tuple(refused),
    )


@dataclass(frozen=True)
class DiskUsageEntry:
    label: str
    path: Path
    exists: bool
    bytes: int
    entries: int


def _du(path: Path) -> Tuple[int, int]:
    """Return (total_bytes, file_count) under *path*; (0, 0) if absent."""

    if not path.exists():
        return (0, 0)
    if path.is_file():
        try:
            return (path.stat().st_size, 1)
        except OSError:
            return (0, 1)
    total = 0
    count = 0
    for sub in path.rglob("*"):
        if sub.is_file():
            try:
                total += sub.stat().st_size
            except OSError:
                pass
            count += 1
    return (total, count)


def summarize_disk_usage(
    repo_root: object, *, env: Optional[Mapping[str, str]] = None
) -> Tuple[DiskUsageEntry, ...]:
    """Read-only observability over the paths automation accumulates into.

    Covers the repo ``.git``, each allowlisted worktree root, ``.cache`` and
    ``runs``. Never mutates anything.
    """

    repo = Path(os.path.expanduser(str(repo_root))).resolve()
    targets: list[Tuple[str, Path]] = [
        ("repo .git", repo / ".git"),
        ("repo .cache", repo / ".cache"),
        ("repo runs", repo / "runs"),
    ]
    for idx, root in enumerate(allowlisted_roots(repo, env=env)):
        targets.append((f"worktree root[{idx}]", root))
    out: list[DiskUsageEntry] = []
    for label, path in targets:
        size, entries = _du(path)
        out.append(
            DiskUsageEntry(
                label=label, path=path, exists=path.exists(), bytes=size, entries=entries
            )
        )
    return tuple(out)


def render_disk_usage(entries: Sequence[DiskUsageEntry]) -> str:
    lines = ["# git / worktree disk usage", ""]
    for e in entries:
        mb = e.bytes / (1024 * 1024)
        state = f"{mb:.1f} MB / {e.entries} files" if e.exists else "absent"
        lines.append(f"- **{e.label}** (`{e.path}`): {state}")
    return "\n".join(lines)


__all__ = (
    "ENV_CODING_EXECUTOR_WORKTREE_ROOT",
    "ENV_SELF_IMPROVEMENT_WORKTREE_ROOT",
    "DEFAULT_CODING_EXECUTOR_ROOT",
    "DEFAULT_SELF_IMPROVEMENT_ROOT_REL",
    "UnsafeCleanupError",
    "allowlisted_roots",
    "assert_safe_cleanup_target",
    "StaleWorktreeDir",
    "detect_stale_worktree_dirs",
    "CleanupPlan",
    "plan_worktree_cleanup",
    "DiskUsageEntry",
    "summarize_disk_usage",
    "render_disk_usage",
)
