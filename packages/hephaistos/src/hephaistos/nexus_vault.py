"""Nexus root / Obsidian vault bootstrap — honest inspection + opt-in scaffold (no fake).

`connection_status` (in :mod:`hephaistos.nexus_read`) answers "is a root set and readable?". This
module answers the richer **vault** questions an operator bootstrap needs: is the connected root an
actual Obsidian vault (a real ``.obsidian/`` dir), is it empty, how many notes does it hold, and does
it have the ForgeKit KB layout — then optionally **scaffolds** the missing layout dirs.

Honesty rails:
- never claims Obsidian where there's no ``.obsidian/`` (``is_obsidian`` is the real check, not assumed);
- ``scaffold`` with ``create=False`` only *reports* the gap; ``create=True`` makes the missing KB dirs
  and reports exactly what it created vs found — it NEVER creates ``.obsidian`` (won't fake a vault);
- bounded note count (won't walk an unbounded tree).

Pure stdlib (Path only). Lives beside the nexus reader (package-topology: nexus_read/ops are the
hephaistos-side reader); no new cross-package dependency.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

# vault states (honest superset of the connection states, vault-aware).
VAULT_NOT_CONNECTED = "not_connected"   # no root configured
VAULT_MISSING = "missing"               # root set but path absent
VAULT_BLOCKED = "blocked"               # present but unreadable (permission/TCC)
VAULT_EMPTY = "empty"                    # readable but no markdown notes yet
VAULT_CONNECTED = "connected"            # readable with notes

# the ForgeKit knowledge-base layout (the vault folders curated notes live under).
KB_LAYOUT: Tuple[str, ...] = ("00-inbox", "10-projects", "20-areas", "30-resources")

_NOTE_SCAN_CAP = 2000   # bound the note count walk — large vaults report "<cap>+" honestly


@dataclass(frozen=True)
class VaultInspection:
    """Honest verdict for a (maybe-absent) Nexus vault root."""

    state: str
    root: str = ""
    is_obsidian: bool = False           # a real ``.obsidian/`` dir is present
    note_count: int = 0                 # markdown notes found (bounded; see note_capped)
    note_capped: bool = False           # True if the walk hit the scan cap
    present_dirs: Tuple[str, ...] = ()  # KB_LAYOUT dirs that exist
    missing_dirs: Tuple[str, ...] = ()  # KB_LAYOUT dirs absent
    reason: str = ""

    @property
    def connected(self) -> bool:
        return self.state in (VAULT_EMPTY, VAULT_CONNECTED)

    def to_dict(self) -> dict:
        return {
            "state": self.state, "root": self.root, "is_obsidian": self.is_obsidian,
            "note_count": self.note_count, "note_capped": self.note_capped,
            "present_dirs": list(self.present_dirs), "missing_dirs": list(self.missing_dirs),
            "reason": self.reason,
        }


def _count_notes(root: Path) -> Tuple[int, bool]:
    """Bounded count of ``*.md`` files under *root*. (count, capped)."""

    n = 0
    try:
        for _dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.endswith(".md"):
                    n += 1
                    if n >= _NOTE_SCAN_CAP:
                        return n, True
    except OSError:
        return n, False
    return n, False


def inspect_vault(root: Optional[Path]) -> VaultInspection:
    """Inspect a Nexus vault root honestly. ``root=None`` → not_connected (no fake)."""

    if root is None:
        return VaultInspection(VAULT_NOT_CONNECTED, reason="nexus_root 미설정")
    root = Path(root)
    try:
        exists = root.exists()
        readable = exists and os.access(root, os.R_OK)
    except OSError:
        exists = readable = False
    if not exists:
        return VaultInspection(VAULT_MISSING, str(root), reason="설정된 root 경로가 존재하지 않음")
    if not readable:
        return VaultInspection(VAULT_BLOCKED, str(root), reason="root 읽기 불가(permission/TCC)")
    is_obsidian = (root / ".obsidian").is_dir()
    present = tuple(d for d in KB_LAYOUT if (root / d).is_dir())
    missing = tuple(d for d in KB_LAYOUT if d not in present)
    count, capped = _count_notes(root)
    state = VAULT_CONNECTED if count > 0 else VAULT_EMPTY
    vault_word = "Obsidian vault" if is_obsidian else "markdown root"
    reason = (f"{vault_word} · notes {count}{'+' if capped else ''} · "
              f"KB layout {len(present)}/{len(KB_LAYOUT)}")
    return VaultInspection(state, str(root), is_obsidian, count, capped, present, missing, reason)


@dataclass(frozen=True)
class ScaffoldResult:
    """What a scaffold pass found/created. ``created`` is empty when ``create=False``."""

    root: str
    created: Tuple[str, ...] = ()
    existing: Tuple[str, ...] = ()
    ok: bool = True
    reason: str = ""

    def to_dict(self) -> dict:
        return {"root": self.root, "created": list(self.created),
                "existing": list(self.existing), "ok": self.ok, "reason": self.reason}


def scaffold_vault(root: Optional[Path], *, create: bool = False) -> ScaffoldResult:
    """Report (and optionally create) the ForgeKit KB layout dirs under *root*.

    ``create=False`` → report only (created stays empty). ``create=True`` → mkdir the missing
    KB dirs, reporting created vs existing. NEVER touches ``.obsidian`` (won't fake a vault).
    Honest failure on a missing/unwritable root."""

    if root is None:
        return ScaffoldResult("", ok=False, reason="nexus_root 미설정 — 먼저 연결하세요")
    root = Path(root)
    if not root.exists():
        return ScaffoldResult(str(root), ok=False, reason="root 경로 없음 — scaffold 불가(위조 안 함)")
    existing = [d for d in KB_LAYOUT if (root / d).is_dir()]
    missing = [d for d in KB_LAYOUT if d not in existing]
    if not create:
        return ScaffoldResult(str(root), existing=tuple(existing),
                              reason=f"미생성 {len(missing)}개: {', '.join(missing) or '없음'} "
                                     f"(`create=True` 로 생성)")
    created: List[str] = []
    for d in missing:
        try:
            (root / d).mkdir(parents=True, exist_ok=True)
            created.append(d)
        except OSError:
            return ScaffoldResult(str(root), tuple(created), tuple(existing), ok=False,
                                  reason=f"{d} 생성 실패(권한 확인)")
    return ScaffoldResult(str(root), tuple(created), tuple(existing),
                          reason=f"생성 {len(created)} · 기존 {len(existing)} (.obsidian 미생성)")


__all__ = (
    "VAULT_NOT_CONNECTED", "VAULT_MISSING", "VAULT_BLOCKED", "VAULT_EMPTY", "VAULT_CONNECTED",
    "KB_LAYOUT", "VaultInspection", "inspect_vault", "ScaffoldResult", "scaffold_vault",
)
