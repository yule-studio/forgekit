"""Personal brain — the auto-created, read-write default store.

No Obsidian dependency: notes are plain markdown with a tiny frontmatter block
(``key: value`` lines between ``---`` fences), so the brain is readable/writable
with nothing installed. Auto-init lays down a small folder skeleton; writes go
only here (enforced via :mod:`brain.policy`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple

from . import policy
from .models import LAYER_PERSONAL, PERSONAL_SKELETON, BrainNote

_KIND_DIR = {"note": "notes", "decision": "decisions", "inbox": "inbox"}
_INDEX_NAME = "index.md"


def _slugify(title: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in title.strip()]
    slug = "".join(keep)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "untitled"


def _frontmatter(note: BrainNote, created_at: str) -> str:
    tags = ", ".join(note.tags)
    return (
        "---\n"
        f"title: {note.title}\n"
        f"kind: {note.kind}\n"
        f"created_at: {created_at}\n"
        f"tags: {tags}\n"
        "brain_layer: personal\n"
        "---\n"
    )


@dataclass(frozen=True)
class PersonalBrain:
    base_dir: Path

    @property
    def index_path(self) -> Path:
        return self.base_dir / _INDEX_NAME

    def is_initialized(self) -> bool:
        return self.base_dir.is_dir() and self.index_path.exists()

    def write_note(self, note: BrainNote, *, created_at: str = "1970-01-01T00:00:00Z") -> Path:
        """Write *note* into the personal brain; returns the file path.

        The layer policy is asserted first — a personal-only write target means
        this is the single sanctioned write path in the whole brain.
        """

        policy.assert_writable(LAYER_PERSONAL)
        subdir = self.base_dir / _KIND_DIR.get(note.kind, "notes")
        subdir.mkdir(parents=True, exist_ok=True)
        path = subdir / f"{_slugify(note.title)}.md"
        path.write_text(_frontmatter(note, created_at) + "\n" + note.body.rstrip() + "\n", encoding="utf-8")
        return path

    def list_notes(self) -> Tuple[Path, ...]:
        if not self.base_dir.is_dir():
            return ()
        return tuple(sorted(p for p in self.base_dir.rglob("*.md") if p.name != _INDEX_NAME))

    def stats(self) -> Mapping[str, int]:
        notes = self.list_notes()
        by_kind = {kind: 0 for kind in _KIND_DIR}
        for p in notes:
            by_kind[_kind_of(p)] = by_kind.get(_kind_of(p), 0) + 1
        return {"total": len(notes), **by_kind}


def _kind_of(path: Path) -> str:
    parent = path.parent.name
    for kind, d in _KIND_DIR.items():
        if d == parent:
            return kind
    return "note"


def init_personal_brain(base_dir: Path, *, created_at: str = "1970-01-01T00:00:00Z") -> PersonalBrain:
    """Create the personal-brain skeleton + index if missing. Idempotent."""

    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    for sub in PERSONAL_SKELETON:
        (base / sub).mkdir(parents=True, exist_ok=True)
    brain = PersonalBrain(base_dir=base)
    if not brain.index_path.exists():
        brain.index_path.write_text(
            "---\n"
            "title: personal brain\n"
            "brain_layer: personal\n"
            f"created_at: {created_at}\n"
            "---\n\n"
            "# personal brain\n\n"
            "forgekit 의 기본 read-write 브레인입니다. 모든 write 는 여기로 갑니다.\n"
            "starter/shared pack 은 read-only — 직접 수정하지 않습니다.\n",
            encoding="utf-8",
        )
    return brain


def open_personal_brain(base_dir: Path) -> Optional[PersonalBrain]:
    """Return the brain if initialized, else None (no side effects)."""

    brain = PersonalBrain(base_dir=Path(base_dir))
    return brain if brain.is_initialized() else None


__all__ = (
    "PersonalBrain",
    "init_personal_brain",
    "open_personal_brain",
)
