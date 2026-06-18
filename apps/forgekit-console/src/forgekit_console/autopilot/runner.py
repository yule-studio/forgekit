"""Bounded execution runner (WT3) — REAL safe-class mutation, hard-capped + verified.

This is the teeth: autopilot's "execute" actually changes a file (it is NOT a no-op).
But it is bounded by construction:

* only **safe-class** actions (note / docs-stub / trailing-whitespace format),
* only paths under an **allowed write prefix** (``runs/`` / ``docs/`` / ``examples/``) —
  source trees and anything with ``..`` / absolute paths are rejected,
* **file + diff caps** enforced before the write,
* every write is **verified** (re-read) — verify fail → halt, do NOT report success.

It never auto-commits, never touches deploy/secret/infra, never writes outside the
caps. ``executed=True`` is set ONLY after a real write that verified. Pure stdlib.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

ACTION_NOTE = "note"            # write a managed note/runbook (md)
ACTION_DOCS_STUB = "docs-stub"  # append a docs stub
ACTION_FORMAT = "format"        # strip trailing whitespace on a target (bounded)
SAFE_ACTIONS: Tuple[str, ...] = (ACTION_NOTE, ACTION_DOCS_STUB, ACTION_FORMAT)

# writes are confined to these repo-relative prefixes (never source trees blindly)
ALLOWED_WRITE_PREFIXES: Tuple[str, ...] = ("runs/", "docs/", "examples/")


@dataclass(frozen=True)
class ExecTask:
    action: str
    rel_path: str            # repo-relative target
    content: str = ""        # for note/docs-stub
    summary: str = ""


@dataclass(frozen=True)
class ExecOutcome:
    executed: bool
    action: str = ""
    path: str = ""
    lines_changed: int = 0
    verified: bool = False
    refused_reason: str = ""
    before_hash: str = ""
    after_hash: str = ""

    def to_dict(self) -> dict:
        return {
            "executed": self.executed, "action": self.action, "path": self.path,
            "lines_changed": self.lines_changed, "verified": self.verified,
            "refused_reason": self.refused_reason,
            "before_hash": self.before_hash, "after_hash": self.after_hash,
        }


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]


def _path_ok(rel_path: str) -> bool:
    rp = (rel_path or "").strip()
    if not rp or rp.startswith("/") or ".." in Path(rp).parts:
        return False
    norm = rp.replace("\\", "/")
    return any(norm.startswith(pre) for pre in ALLOWED_WRITE_PREFIXES)


@dataclass
class BoundedMutator:
    """Performs a REAL safe-class file mutation under hard caps. Verifies every write."""

    repo_root: Path
    max_files: int = 1
    max_diff_lines: int = 200

    def __post_init__(self) -> None:
        self.repo_root = Path(self.repo_root)

    def validate(self, task: ExecTask) -> Tuple[bool, str]:
        if task.action not in SAFE_ACTIONS:
            return False, f"non-safe action: {task.action} (자동 실행 금지)"
        if not _path_ok(task.rel_path):
            return False, f"write 경로 불허: {task.rel_path} (허용 prefix: {', '.join(ALLOWED_WRITE_PREFIXES)})"
        approx = task.content.count("\n") + 1 if task.content else 1
        if approx > self.max_diff_lines:
            return False, f"diff {approx} > 한도 {self.max_diff_lines}"
        return True, ""

    def execute(self, task: ExecTask) -> ExecOutcome:
        ok, reason = self.validate(task)
        if not ok:
            return ExecOutcome(False, action=task.action, path=task.rel_path, refused_reason=reason)
        target = self.repo_root / task.rel_path
        try:
            before = target.read_text(encoding="utf-8") if target.exists() else ""
        except OSError:
            before = ""
        if task.action == ACTION_FORMAT:
            after = "\n".join(line.rstrip() for line in before.split("\n"))
        else:  # note / docs-stub → write/append the content
            after = task.content if task.action == ACTION_NOTE else (before + "\n" + task.content)
        if after == before:
            # nothing to change — honest no-op (executed=False, not a fake success)
            return ExecOutcome(False, action=task.action, path=task.rel_path,
                               refused_reason="변경 없음 (no-op)", before_hash=_sha(before),
                               after_hash=_sha(before))
        lines_changed = sum(1 for a, b in zip(after.split("\n"), before.split("\n")) if a != b) \
            + abs(after.count("\n") - before.count("\n"))
        if lines_changed > self.max_diff_lines:
            return ExecOutcome(False, action=task.action, path=task.rel_path,
                               refused_reason=f"diff {lines_changed} > 한도 {self.max_diff_lines}")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(after, encoding="utf-8")   # REAL mutation
        except OSError as exc:
            return ExecOutcome(False, action=task.action, path=task.rel_path,
                               refused_reason=f"write 실패: {exc}")
        # VERIFY — re-read and confirm the write landed exactly.
        try:
            verified = target.read_text(encoding="utf-8") == after
        except OSError:
            verified = False
        return ExecOutcome(
            executed=verified, action=task.action, path=task.rel_path,
            lines_changed=lines_changed, verified=verified,
            before_hash=_sha(before), after_hash=_sha(after),
            refused_reason="" if verified else "verify 실패 — 결과 불일치",
        )


__all__ = (
    "ACTION_NOTE", "ACTION_DOCS_STUB", "ACTION_FORMAT", "SAFE_ACTIONS",
    "ALLOWED_WRITE_PREFIXES", "ExecTask", "ExecOutcome", "BoundedMutator",
)
