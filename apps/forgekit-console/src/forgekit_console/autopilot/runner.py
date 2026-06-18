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
ACTION_SOURCE_FORMAT = "source-format"  # whitespace-only normalize ONE source file (#240)
SAFE_ACTIONS: Tuple[str, ...] = (ACTION_NOTE, ACTION_DOCS_STUB, ACTION_FORMAT, ACTION_SOURCE_FORMAT)

# writes are confined to these repo-relative prefixes (never source trees blindly)
ALLOWED_WRITE_PREFIXES: Tuple[str, ...] = ("runs/", "docs/", "examples/")

# source-format may touch ONLY these extensions, and only under an explicitly enabled
# source prefix (off by default). The transform is whitespace-only + semantics-preserving
# + parse-verified, with rollback — never a semantic edit.
SOURCE_EXTENSIONS: Tuple[str, ...] = (".py",)


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


def _source_path_ok(rel_path: str, prefixes: Tuple[str, ...]) -> bool:
    """A source-format target must be a real file under an enabled source prefix,
    with an allowed extension, no traversal / absolute path."""

    rp = (rel_path or "").strip()
    if not rp or rp.startswith("/") or ".." in Path(rp).parts:
        return False
    norm = rp.replace("\\", "/")
    if not any(norm.endswith(ext) for ext in SOURCE_EXTENSIONS):
        return False
    return bool(prefixes) and any(norm.startswith(pre) for pre in prefixes)


def _whitespace_normalize(text: str) -> str:
    """Strip trailing whitespace per line + ensure exactly one final newline.
    Semantics-preserving for python (only insignificant trailing whitespace)."""

    body = "\n".join(line.rstrip() for line in text.split("\n"))
    return body.rstrip("\n") + "\n" if body.strip() else body


def _nonspace(text: str) -> str:
    """Every non-whitespace char, in order — equal before/after ⇒ only whitespace moved."""

    return "".join(text.split())


def _py_parses(text: str, path: str) -> bool:
    try:
        compile(text, path or "<source>", "exec")
        return True
    except (SyntaxError, ValueError):
        return False


@dataclass
class BoundedMutator:
    """Performs a REAL safe-class file mutation under hard caps. Verifies every write.

    ``source_prefixes`` opts in to ``source-format`` on real source files (#240) — a
    whitespace-only, semantics-preserving, parse-verified edit with rollback. It is OFF
    by default (empty): without an explicit source prefix, source trees stay untouched.
    ``source_verifier`` is the post-write gate (default: the file still parses); injectable
    so the rollback path is testable.
    """

    repo_root: Path
    max_files: int = 1
    max_diff_lines: int = 200
    source_prefixes: Tuple[str, ...] = ()
    source_verifier: Optional[object] = None   # (after_text, path) -> bool

    def __post_init__(self) -> None:
        self.repo_root = Path(self.repo_root)

    def validate(self, task: ExecTask) -> Tuple[bool, str]:
        if task.action not in SAFE_ACTIONS:
            return False, f"non-safe action: {task.action} (자동 실행 금지)"
        if task.action == ACTION_SOURCE_FORMAT:
            if not self.source_prefixes:
                return False, "source-format 비활성 (source_prefixes 미설정 — 소스 수정 기본 OFF)"
            if not _source_path_ok(task.rel_path, self.source_prefixes):
                return False, (f"source 경로 불허: {task.rel_path} "
                               f"(허용: {', '.join(self.source_prefixes) or '-'} · {', '.join(SOURCE_EXTENSIONS)})")
            return True, ""
        if not _path_ok(task.rel_path):
            return False, f"write 경로 불허: {task.rel_path} (허용 prefix: {', '.join(ALLOWED_WRITE_PREFIXES)})"
        approx = task.content.count("\n") + 1 if task.content else 1
        if approx > self.max_diff_lines:
            return False, f"diff {approx} > 한도 {self.max_diff_lines}"
        return True, ""

    def _execute_source_format(self, task: ExecTask) -> "ExecOutcome":
        """Whitespace-only normalize one source file — semantics-preserving + verified +
        rollback. NEVER changes non-whitespace content; if the result would, it refuses."""

        target = self.repo_root / task.rel_path
        if not target.exists():
            return ExecOutcome(False, action=task.action, path=task.rel_path,
                               refused_reason="대상 source 파일 없음")
        try:
            before = target.read_text(encoding="utf-8")
        except OSError as exc:
            return ExecOutcome(False, action=task.action, path=task.rel_path,
                               refused_reason=f"read 실패: {exc}")
        # only operate on a file that currently parses — never touch already-broken source.
        if not _py_parses(before, str(target)):
            return ExecOutcome(False, action=task.action, path=task.rel_path,
                               refused_reason="대상이 이미 파싱 불가 — skip (건드리지 않음)")
        after = _whitespace_normalize(before)
        # HARD GUARD: the edit must move only whitespace. If any non-whitespace char would
        # change, this is NOT safe-class → refuse (escalates to approval, no write).
        if _nonspace(after) != _nonspace(before):
            return ExecOutcome(False, action=task.action, path=task.rel_path,
                               refused_reason="non-whitespace 변경 감지 — safe-class 아님 (approval 필요)")
        if after == before:
            return ExecOutcome(False, action=task.action, path=task.rel_path,
                               refused_reason="변경 없음 (no-op)",
                               before_hash=_sha(before), after_hash=_sha(before))
        lines_changed = sum(1 for a, b in zip(after.split("\n"), before.split("\n")) if a != b) \
            + abs(after.count("\n") - before.count("\n"))
        if lines_changed > self.max_diff_lines:
            return ExecOutcome(False, action=task.action, path=task.rel_path,
                               refused_reason=f"diff {lines_changed} > 한도 {self.max_diff_lines}")
        try:
            target.write_text(after, encoding="utf-8")   # REAL source mutation
        except OSError as exc:
            return ExecOutcome(False, action=task.action, path=task.rel_path,
                               refused_reason=f"write 실패: {exc}")
        # VERIFY: re-read matches AND the post-write gate passes (default: still parses).
        verify = self.source_verifier or (lambda txt, p: _py_parses(txt, p))
        try:
            reread = target.read_text(encoding="utf-8")
            ok = (reread == after) and bool(verify(after, str(target)))
        except OSError:
            ok = False
        if not ok:
            # ROLLBACK — restore the exact original; report discarded (never fake success).
            try:
                target.write_text(before, encoding="utf-8")
            except OSError:
                pass
            return ExecOutcome(False, action=task.action, path=task.rel_path,
                               lines_changed=lines_changed, verified=False,
                               before_hash=_sha(before), after_hash=_sha(after),
                               refused_reason="verify 실패 — rollback (원본 복원, 커밋 안 함)")
        return ExecOutcome(executed=True, action=task.action, path=task.rel_path,
                           lines_changed=lines_changed, verified=True,
                           before_hash=_sha(before), after_hash=_sha(after))

    def execute(self, task: ExecTask) -> ExecOutcome:
        ok, reason = self.validate(task)
        if not ok:
            return ExecOutcome(False, action=task.action, path=task.rel_path, refused_reason=reason)
        if task.action == ACTION_SOURCE_FORMAT:
            return self._execute_source_format(task)
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
    "ACTION_NOTE", "ACTION_DOCS_STUB", "ACTION_FORMAT", "ACTION_SOURCE_FORMAT",
    "SAFE_ACTIONS", "ALLOWED_WRITE_PREFIXES", "SOURCE_EXTENSIONS",
    "ExecTask", "ExecOutcome", "BoundedMutator",
)
