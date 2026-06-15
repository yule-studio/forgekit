"""Allowlist-based Claude Code artifact cleanup (issue #185 follow-up).

Goal (per the operator brief): NOT "delete every trace of past sessions" but
"after the summary is vaulted, *safely* reclaim space". Cleanup is allowlist
based and **deny-list-first** — anything not positively classified as
transient/generated is PRESERVED.

Three classifications:

  * ``DELETABLE``       — transient / regeneratable artifacts (pycache, test
    caches, ``*.tmp`` / ``*.log``, snapshot exports, files carrying an explicit
    ``YULE-CLEANUP-SAFE`` marker). Removed only in execute mode.
  * ``PRESERVE``        — audit / canonical artifacts that must never be
    deleted: ``*.sqlite3`` workflow stores (they carry real operator
    sessions), ``agent_ops_audit`` traces, vault canonical notes
    (``00-inbox`` / ``10-projects`` / ``20-areas`` …), prompt / decision /
    synthesis / approval bodies, ``.git`` and source/policy/test/docs files.
    Default for anything unmatched.
  * ``APPROVAL_NEEDED`` — regeneratable but tracked (generated harness dirs:
    ``.claude/skills`` / ``.agents/skills`` / ``*-plugin``) or large exports.
    Listed, never auto-deleted.

Modes: dry-run (default) reports only; execute requires BOTH ``execute=True``
*and* ``confirm=True`` (the "명시 승인 또는 강한 안전장치"). Only ``DELETABLE``
entries are ever removed; ``PRESERVE`` always wins over every other rule.

The receipt carries: scanned paths, matched rules, deleted count, reclaimed
bytes estimate, skipped/protected paths, approval-needed items.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

# Explicit opt-in marker a generator can drop into a file to mark it
# regeneratable/safe-to-delete even if it would otherwise be preserved.
CLEANUP_SAFE_MARKER = "YULE-CLEANUP-SAFE"


class Classification(str, Enum):
    DELETABLE = "deletable"
    PRESERVE = "preserve"
    APPROVAL_NEEDED = "approval_needed"


# Directory basenames handled as whole units.
_PRESERVE_DIR_NAMES = frozenset({".git", ".hg", ".svn"})
_DELETABLE_DIR_NAMES = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache_tmp"}
)

# Substrings that force PRESERVE regardless of any other rule (deny-list).
_PRESERVE_SUBSTRINGS: Tuple[str, ...] = (
    "agent_ops_audit",
    "execution_receipt",
    "/00-inbox/",
    "/10-projects/",
    "/20-areas/",
    "/30-resources/",
    "/40-archive/",
    "/decisions/",
    "/policies/",
    "/docs/",
    "/tests/",
    "/.git/",
    "/.github/",
    "/.ssh/",
    "/migrations/",
)
_PRESERVE_SUFFIXES: Tuple[str, ...] = (
    # operational data stores + their sqlite sidecars (deleting a -wal/-shm
    # sidecar corrupts the live DB — always preserve)
    ".sqlite3",
    ".sqlite",
    ".db",
    "-wal",
    "-shm",
    "-journal",
    # source / config / secrets — never transient
    ".py",
    ".env",
    ".pem",
    ".key",
    ".lock",
)
# Filename tokens that mark canonical/audit/secret bodies — preserve.
_PRESERVE_NAME_TOKENS: Tuple[str, ...] = (
    "prompt",
    "decision",
    "synthesis",
    "approval",
    "task-log",
    "agent_ops_audit",
    "credential",
    "secret",
    ".env",
)

_DELETABLE_SUFFIXES: Tuple[str, ...] = (
    ".tmp",
    ".temp",
    ".pyc",
    ".pyo",
    ".log",
)
_DELETABLE_NAME_SUBSTRINGS: Tuple[str, ...] = (
    "-snapshot-",
    ".generated.",
    "tmp-",
)

# Generated-but-tracked harness projection roots → approval needed.
_APPROVAL_SUBSTRINGS: Tuple[str, ...] = (
    "/.claude/skills/",
    "/.agents/skills/",
    "/.claude-plugin/",
    "/.codex-plugin/",
)
_LARGE_FILE_BYTES = 5 * 1024 * 1024  # 5 MiB unclassified file → approval needed


@dataclass(frozen=True)
class CleanupEntry:
    rel_path: str
    abs_path: str
    classification: Classification
    rule: str
    reason: str
    size_bytes: int
    is_dir: bool


@dataclass(frozen=True)
class CleanupReceipt:
    root: str
    scanned_count: int
    matched_rules: Tuple[Tuple[str, str, int], ...]  # (rule, classification, count)
    deletable: Tuple[CleanupEntry, ...]
    protected: Tuple[CleanupEntry, ...]
    approval_needed: Tuple[CleanupEntry, ...]
    deleted_count: int
    reclaimed_bytes: int
    executed: bool
    warnings: Tuple[str, ...] = ()

    @property
    def reclaimable_bytes(self) -> int:
        return sum(e.size_bytes for e in self.deletable)

    @property
    def status(self) -> str:
        if self.executed:
            return "executed"
        return "dry_run"

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "status": self.status,
            "scanned_count": self.scanned_count,
            "matched_rules": [
                {"rule": r, "classification": c, "count": n}
                for (r, c, n) in self.matched_rules
            ],
            "deletable_count": len(self.deletable),
            "deleted_count": self.deleted_count,
            "reclaimable_bytes": self.reclaimable_bytes,
            "reclaimed_bytes": self.reclaimed_bytes,
            "protected_count": len(self.protected),
            "approval_needed_count": len(self.approval_needed),
            "approval_needed": [e.rel_path for e in self.approval_needed],
            "warnings": list(self.warnings),
        }


def _entry_size(path: Path) -> int:
    try:
        if path.is_dir():
            total = 0
            for dirpath, _dirs, files in os.walk(path):
                for f in files:
                    fp = Path(dirpath) / f
                    try:
                        total += fp.stat().st_size
                    except OSError:
                        continue
            return total
        return path.stat().st_size
    except OSError:
        return 0


def classify(rel_path: str, *, is_dir: bool, size_bytes: int = 0) -> Tuple[Classification, str, str]:
    """Classify a path. Deny-list (PRESERVE) wins; default is PRESERVE.

    Returns ``(classification, rule, reason)``.
    """

    name = rel_path.rsplit("/", 1)[-1]
    lowered = ("/" + rel_path + "/").lower()
    name_l = name.lower()

    # 1. PRESERVE wins — audit / canonical / source.
    for token in _PRESERVE_NAME_TOKENS:
        if token in name_l:
            return Classification.PRESERVE, "preserve:name-token", f"name carries '{token}'"
    for sub in _PRESERVE_SUBSTRINGS:
        if sub in lowered:
            return Classification.PRESERVE, "preserve:path", f"under protected path '{sub}'"
    for suf in _PRESERVE_SUFFIXES:
        if name_l.endswith(suf):
            return Classification.PRESERVE, "preserve:suffix", f"protected suffix '{suf}'"

    # 2. Whole-dir deletable caches.
    if is_dir and name in _DELETABLE_DIR_NAMES:
        return Classification.DELETABLE, "deletable:cache-dir", f"regeneratable cache dir '{name}'"

    # 3. DELETABLE transient/generated files.
    if not is_dir:
        for suf in _DELETABLE_SUFFIXES:
            if name_l.endswith(suf):
                return Classification.DELETABLE, "deletable:suffix", f"transient suffix '{suf}'"
        for sub in _DELETABLE_NAME_SUBSTRINGS:
            if sub in name_l:
                return (
                    Classification.DELETABLE,
                    "deletable:name",
                    f"regeneratable artifact name contains '{sub}'",
                )

    # 4. APPROVAL_NEEDED — generated-but-tracked or large.
    for sub in _APPROVAL_SUBSTRINGS:
        if sub in lowered:
            return (
                Classification.APPROVAL_NEEDED,
                "approval:generated-harness",
                f"generated-but-tracked harness path '{sub}'",
            )
    if not is_dir and size_bytes >= _LARGE_FILE_BYTES:
        return (
            Classification.APPROVAL_NEEDED,
            "approval:large-file",
            f"large unclassified file ({size_bytes} bytes)",
        )

    # 5. Default — preserve (safe).
    return Classification.PRESERVE, "preserve:default", "unmatched — preserved by default"


def scan(root: Path, *, marker_optin: bool = True) -> List[CleanupEntry]:
    """Walk *root* and classify each entry. Does not delete anything."""

    root = Path(root)
    entries: List[CleanupEntry] = []
    if not root.exists():
        return entries

    for dirpath, dirnames, filenames in os.walk(root):
        dpath = Path(dirpath)
        # Prune PRESERVE dirs from recursion; emit DELETABLE cache dirs whole.
        pruned: List[str] = []
        for d in list(dirnames):
            full = dpath / d
            rel = _rel(full, root)
            if d in _PRESERVE_DIR_NAMES:
                dirnames.remove(d)
                continue
            if d in _DELETABLE_DIR_NAMES:
                size = _entry_size(full)
                cls, rule, reason = classify(rel, is_dir=True, size_bytes=size)
                entries.append(
                    CleanupEntry(rel, str(full), cls, rule, reason, size, True)
                )
                dirnames.remove(d)  # do not descend; handled as a unit
                continue
            pruned.append(d)
        dirnames[:] = pruned

        for f in filenames:
            full = dpath / f
            rel = _rel(full, root)
            try:
                size = full.stat().st_size
            except OSError:
                size = 0
            cls, rule, reason = classify(rel, is_dir=False, size_bytes=size)
            # Explicit opt-in marker can promote an otherwise-preserved file.
            if (
                marker_optin
                and cls is Classification.PRESERVE
                and not rule.startswith("preserve:suffix")
                and not rule.startswith("preserve:name-token")
                and not _path_is_protected(rel)
                and _has_marker(full)
            ):
                cls, rule, reason = (
                    Classification.DELETABLE,
                    "deletable:marker",
                    f"carries explicit {CLEANUP_SAFE_MARKER} marker",
                )
            entries.append(CleanupEntry(rel, str(full), cls, rule, reason, size, False))

    entries.sort(key=lambda e: e.rel_path)
    return entries


def run_cleanup(
    root: Path,
    *,
    execute: bool = False,
    confirm: bool = False,
    marker_optin: bool = True,
) -> CleanupReceipt:
    """Scan *root* and, only when ``execute and confirm``, delete DELETABLE entries.

    Dry-run (default) deletes nothing. Execute requires *both* flags — a single
    accidental ``execute=True`` is not enough to remove files.
    """

    root = Path(root)
    warnings: List[str] = []
    if not root.exists():
        warnings.append(f"scan root does not exist: {root}")
    entries = scan(root, marker_optin=marker_optin)

    deletable = tuple(e for e in entries if e.classification is Classification.DELETABLE)
    protected = tuple(e for e in entries if e.classification is Classification.PRESERVE)
    approval = tuple(e for e in entries if e.classification is Classification.APPROVAL_NEEDED)

    # Aggregate matched rules.
    counts: dict[Tuple[str, str], int] = {}
    for e in entries:
        key = (e.rule, e.classification.value)
        counts[key] = counts.get(key, 0) + 1
    matched_rules = tuple(
        sorted(((r, c, n) for (r, c), n in counts.items()), key=lambda t: (t[1], t[0]))
    )

    deleted_count = 0
    reclaimed_bytes = 0
    will_execute = bool(execute and confirm)
    if execute and not confirm:
        warnings.append("execute requested without confirm=True — refusing to delete (safety)")
    if will_execute:
        for e in deletable:
            target = Path(e.abs_path)
            try:
                if e.is_dir:
                    shutil.rmtree(target)
                else:
                    target.unlink()
                deleted_count += 1
                reclaimed_bytes += e.size_bytes
            except OSError as exc:
                warnings.append(f"failed to delete {e.rel_path}: {exc}")

    return CleanupReceipt(
        root=str(root),
        scanned_count=len(entries),
        matched_rules=matched_rules,
        deletable=deletable,
        protected=protected,
        approval_needed=approval,
        deleted_count=deleted_count,
        reclaimed_bytes=reclaimed_bytes,
        executed=will_execute,
        warnings=tuple(warnings),
    )


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _path_is_protected(rel: str) -> bool:
    cls, _rule, _reason = classify(rel, is_dir=False)
    return cls is Classification.PRESERVE and _rule != "preserve:default"


def _has_marker(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            return CLEANUP_SAFE_MARKER in fh.read(4096)
    except OSError:
        return False


__all__ = (
    "CLEANUP_SAFE_MARKER",
    "Classification",
    "CleanupEntry",
    "CleanupReceipt",
    "classify",
    "scan",
    "run_cleanup",
)
