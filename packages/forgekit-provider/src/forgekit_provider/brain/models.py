"""Brain models + the brain-layer policy constants — pure, stdlib.

The brain has four layers with distinct read/write rules:

  * ``personal``  — read-write. The default (and only) write target.
  * ``starter``   — read-only. A pack *built from* a source vault, never edited.
  * ``source``    — local-only source vault; the build input for a starter pack,
    never read directly at runtime (we read the built pack instead).
  * ``working``   — the per-session/project working set injected into a run;
    a minimal projection, not a store.

These are declarative so the policy + tests assert them without touching disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Tuple

LAYER_PERSONAL = "personal"
LAYER_STARTER = "starter"
LAYER_SOURCE = "source"
LAYER_WORKING = "working"

ALL_LAYERS: Tuple[str, ...] = (LAYER_PERSONAL, LAYER_STARTER, LAYER_SOURCE, LAYER_WORKING)

# Which layers accept writes. Only the personal brain does.
_WRITABLE = frozenset({LAYER_PERSONAL})
# Which layers the runtime reads from directly. NOT source (read its built pack).
_RUNTIME_READABLE = frozenset({LAYER_PERSONAL, LAYER_STARTER, LAYER_WORKING})

# Personal brain folder skeleton created on init.
PERSONAL_SKELETON: Tuple[str, ...] = ("notes", "decisions", "inbox")


@dataclass(frozen=True)
class BrainNote:
    """One markdown note (frontmatter + body) in the personal brain."""

    title: str
    body: str
    kind: str = "note"  # note | decision | inbox
    tags: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PackEntry:
    """One indexed document inside a starter pack manifest."""

    rel_path: str
    title: str
    bytes: int
    digest_chars: int
    tags: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PackManifest:
    """The read-only starter pack manifest — what was built, from where."""

    source_path: str
    built_at: str
    doc_count: int
    total_bytes: int
    entries: Tuple[PackEntry, ...] = ()
    meta: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kind": "forgekit-starter-pack",
            "source_path": self.source_path,
            "built_at": self.built_at,
            "doc_count": self.doc_count,
            "total_bytes": self.total_bytes,
            "entries": [
                {
                    "rel_path": e.rel_path,
                    "title": e.title,
                    "bytes": e.bytes,
                    "digest_chars": e.digest_chars,
                    "tags": list(e.tags),
                }
                for e in self.entries
            ],
            "meta": dict(self.meta),
        }


__all__ = (
    "LAYER_PERSONAL", "LAYER_STARTER", "LAYER_SOURCE", "LAYER_WORKING", "ALL_LAYERS",
    "PERSONAL_SKELETON",
    "BrainNote", "PackEntry", "PackManifest",
)
