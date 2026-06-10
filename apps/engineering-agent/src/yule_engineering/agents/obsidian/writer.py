"""Thin file-writer layer for Obsidian notes.

Sits on top of :mod:`yule_engineering.agents.obsidian.export` and writes
the rendered :class:`ObsidianNote` content to a real Obsidian vault on
disk. The exporter still owns the contract (frontmatter, body, vault path
shape); this module only handles IO.

Safety guarantees:

- Vault root must be an absolute path that already exists as a directory.
- The resolved file path must remain inside the vault root (no traversal
  via ``..`` or symlinks resolving outside).
- Parent directories are created automatically.
- When the recommended path already exists and ``overwrite`` is False,
  the writer auto-appends ``_2``, ``_3``, ... before the ``.md`` suffix
  inside the same folder so existing notes are never silently clobbered
  and the new write is never silently skipped.
- ``overwrite=True`` keeps the recommended filename and replaces it.
- ``dry_run=True`` performs all checks (including suffix selection) but
  never writes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .export import ObsidianNote


ENV_VAULT_PATH = "OBSIDIAN_VAULT_PATH"


class ObsidianWriteError(RuntimeError):
    """Raised when an Obsidian write request cannot be honored safely."""


MAX_SUFFIX_ATTEMPTS = 1000


@dataclass(frozen=True)
class ObsidianWriteResult:
    """Outcome of one write attempt.

    ``target_path`` is always the real path that was (or would be) written
    — including any auto-suffix applied. ``original_target_path`` is the
    untouched recommendation from the exporter; ``suffix_applied`` is True
    iff the writer had to pick a different filename to avoid a collision.
    ``written`` is False only for dry-runs.
    """

    target_path: Path
    written: bool
    dry_run: bool
    overwrite: bool
    original_target_path: Optional[Path] = None
    suffix_applied: bool = False
    skipped_reason: Optional[str] = None


def resolve_vault_root(env: Optional[dict] = None, *, override: Optional[str] = None) -> Path:
    """Return the absolute vault root from ``override`` or ``OBSIDIAN_VAULT_PATH``.

    Raises :class:`ObsidianWriteError` with a human-readable message when
    the value is missing, not absolute, missing on disk, or not a
    directory. Validation lives here so the CLI gets one consistent
    failure mode regardless of how the path was supplied.
    """

    raw = (override or "").strip() if override is not None else ""
    if not raw:
        source = env if env is not None else os.environ
        raw = (source.get(ENV_VAULT_PATH) or "").strip()
    if not raw:
        raise ObsidianWriteError(
            f"{ENV_VAULT_PATH} is not set. "
            "Add it to .env.local with the absolute path to your Obsidian vault."
        )

    expanded = os.path.expanduser(raw)
    path = Path(expanded)
    if not path.is_absolute():
        raise ObsidianWriteError(
            f"{ENV_VAULT_PATH} must be an absolute path; got {raw!r}."
        )
    if not path.exists():
        raise ObsidianWriteError(
            f"Obsidian vault root does not exist: {path}. "
            "Open the vault in Obsidian once or fix the path in .env.local."
        )
    if not path.is_dir():
        raise ObsidianWriteError(
            f"Obsidian vault root is not a directory: {path}."
        )
    return path.resolve()


def write_note(
    note: ObsidianNote,
    vault_root: Path,
    *,
    overwrite: bool = False,
    dry_run: bool = False,
) -> ObsidianWriteResult:
    """Write *note.content* to ``<vault_root>/<note.path.full>``.

    When the recommended path already exists and *overwrite* is False,
    the writer auto-appends ``_2``, ``_3``, ... before the file suffix
    until it finds a free name in the same folder. The returned
    :class:`ObsidianWriteResult` always reports the real path that was
    selected, so the caller can echo it back to the user.
    """

    vault_root_resolved = vault_root.resolve()
    relative = note.path.full
    target = (vault_root_resolved / relative).resolve()

    try:
        target.relative_to(vault_root_resolved)
    except ValueError as exc:
        raise ObsidianWriteError(
            f"Refusing to write outside the vault root. "
            f"vault={vault_root_resolved} target={target}"
        ) from exc

    original_target = target
    suffix_applied = False
    if target.exists() and not overwrite:
        target = _resolve_collision_free_target(target, vault_root_resolved)
        suffix_applied = True

    if dry_run:
        return ObsidianWriteResult(
            target_path=target,
            written=False,
            dry_run=True,
            overwrite=overwrite,
            original_target_path=original_target,
            suffix_applied=suffix_applied,
        )

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ObsidianWriteError(
            f"Could not prepare parent directories for {target}: {exc}"
        ) from exc

    try:
        target.write_text(note.content, encoding="utf-8")
    except OSError as exc:
        raise ObsidianWriteError(
            f"Could not write Obsidian note to {target}: {exc}"
        ) from exc

    return ObsidianWriteResult(
        target_path=target,
        written=True,
        dry_run=False,
        overwrite=overwrite,
        original_target_path=original_target,
        suffix_applied=suffix_applied,
    )


def _resolve_collision_free_target(target: Path, vault_root: Path) -> Path:
    """Return ``target`` with ``_<n>`` appended before the suffix until free.

    Searches the same folder for ``stem_2.<suffix>``, ``stem_3.<suffix>``,
    ... (capped at :data:`MAX_SUFFIX_ATTEMPTS`). Each candidate is
    re-validated against the vault root so symlinks or odd filesystem
    states cannot drag the write outside the vault.
    """

    parent = target.parent
    stem = target.stem
    suffix = target.suffix
    for index in range(2, MAX_SUFFIX_ATTEMPTS + 2):
        candidate = (parent / f"{stem}_{index}{suffix}").resolve()
        try:
            candidate.relative_to(vault_root)
        except ValueError as exc:
            raise ObsidianWriteError(
                f"Refusing to write outside the vault root. "
                f"vault={vault_root} target={candidate}"
            ) from exc
        if not candidate.exists():
            return candidate
    raise ObsidianWriteError(
        f"Could not find a free filename near {target} after "
        f"{MAX_SUFFIX_ATTEMPTS} attempts. Run with --overwrite or "
        "clean up old notes."
    )
