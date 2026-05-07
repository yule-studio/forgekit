from __future__ import annotations

import sys
from typing import Optional

from ..agents.deliberation import synthesis_from_dict
from ..agents.obsidian_export import render_research_note
from ..agents.obsidian_git import (
    ObsidianGitError,
    commit_single_file,
    find_git_repo_root,
)
from ..agents.obsidian_writer import (
    ObsidianWriteError,
    resolve_vault_root,
    write_note,
)
from ..agents.research.pack import pack_from_dict
from ..agents.workflow_state import load_session


VALID_KINDS = ("research", "decision", "reference", "knowledge")


def run_obsidian_sync_command(
    session_id: str,
    *,
    kind: Optional[str],
    vault_path: Optional[str],
    overwrite: bool,
    dry_run: bool,
    git_commit: bool = False,
    git_message: Optional[str] = None,
    project: Optional[str] = None,
    layout: Optional[str] = None,
) -> int:
    if kind is not None and kind not in VALID_KINDS:
        print(
            f"error: --kind must be one of {list(VALID_KINDS)}, got {kind!r}",
            file=sys.stderr,
        )
        return 1

    session = load_session(session_id)
    if session is None:
        print(
            f"error: workflow session {session_id!r} not found in local cache.",
            file=sys.stderr,
        )
        return 1

    pack_payload = (session.extra or {}).get("research_pack")
    if not pack_payload:
        print(
            f"error: session {session_id!r} has no research_pack in session.extra. "
            "Run the engineering-agent research flow first so a pack is collected.",
            file=sys.stderr,
        )
        return 1

    try:
        pack = pack_from_dict(pack_payload)
    except Exception as exc:  # noqa: BLE001 - surface parse failures plainly
        print(f"error: could not parse stored research_pack: {exc}", file=sys.stderr)
        return 1

    synthesis = None
    synthesis_payload = (session.extra or {}).get("research_synthesis")
    if synthesis_payload:
        try:
            synthesis = synthesis_from_dict(synthesis_payload)
        except Exception as exc:  # noqa: BLE001 - degrade gracefully for older payloads
            print(
                f"warning: could not parse stored research_synthesis ({exc}); "
                "exporting research note without synthesis sections.",
                file=sys.stderr,
            )

    try:
        vault_root = resolve_vault_root(override=vault_path)
    except ObsidianWriteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    note = render_research_note(
        pack,
        session=session,
        synthesis=synthesis,
        kind=kind,
        project=project,
        layout=layout,
    )

    try:
        result = write_note(
            note,
            vault_root,
            overwrite=overwrite,
            dry_run=dry_run,
        )
    except ObsidianWriteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        relative = result.target_path.relative_to(vault_root)
    except ValueError:
        relative = result.target_path

    if result.dry_run:
        print(f"dry-run: would write {result.target_path}")
        print(f"vault={vault_root} relative={relative}")
        if result.suffix_applied and result.original_target_path is not None:
            print(
                f"note: applied auto-suffix to avoid clobbering "
                f"{result.original_target_path}"
            )
        if git_commit:
            git_rc = _run_git_commit(
                vault_root=vault_root,
                target_path=result.target_path,
                relative=relative,
                session_id=session_id,
                kind=kind,
                git_message=git_message,
                dry_run=True,
            )
            if git_rc != 0:
                return git_rc
        return 0
    if not result.written:
        print(f"skipped: {result.target_path}")
        if result.skipped_reason:
            print(f"reason: {result.skipped_reason}")
        return 0
    print(f"wrote: {result.target_path}")
    print(f"vault={vault_root} relative={relative}")
    if result.suffix_applied and result.original_target_path is not None:
        print(
            f"note: applied auto-suffix to avoid clobbering "
            f"{result.original_target_path}"
        )
    if git_commit:
        git_rc = _run_git_commit(
            vault_root=vault_root,
            target_path=result.target_path,
            relative=relative,
            session_id=session_id,
            kind=kind,
            git_message=git_message,
            dry_run=False,
        )
        if git_rc != 0:
            return git_rc
    return 0


def _run_git_commit(
    *,
    vault_root,
    target_path,
    relative,
    session_id: str,
    kind: Optional[str],
    git_message: Optional[str],
    dry_run: bool,
) -> int:
    repo_root = find_git_repo_root(vault_root)
    if repo_root is None:
        print(
            f"error: --git-commit requested but vault root {vault_root} is not a "
            "git repository. Initialize it with `git init` or rerun without --git-commit.",
            file=sys.stderr,
        )
        return 1

    message = (git_message or "").strip() or _default_commit_message(
        session_id=session_id, kind=kind, relative=str(relative)
    )

    try:
        commit = commit_single_file(
            repo_root,
            target_path,
            message=message,
            dry_run=dry_run,
        )
    except ObsidianGitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if commit.dry_run:
        print(
            f"git: would commit {commit.target_path} to {commit.repo_root} "
            f'with message "{commit.message}"'
        )
        return 0
    if commit.no_changes:
        print(
            f"git: no changes to commit (file already at vault HEAD): "
            f"{commit.target_path}"
        )
        return 0
    sha_short = commit.commit_sha[:12] if commit.commit_sha else "(unknown sha)"
    print(f"git: committed {sha_short} {relative}")
    return 0


def _default_commit_message(
    *, session_id: str, kind: Optional[str], relative: str
) -> str:
    kind_part = f" ({kind})" if kind else ""
    return f"obsidian sync: {session_id}{kind_part} {relative}"
