"""Data models for the memory index.

A :class:`MemoryDocument` is what we put *into* the index — one row per
note/policy/session artifact. A :class:`MemorySearchResult` is what
callers get *out* — a result hit with score and snippet.

Both types are local-first dataclasses with no DB or framework
dependencies; the indexer/search layers translate them to/from SQLite
rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Optional, Tuple


# Stable identifiers for the source kinds the indexer recognises.  Used
# both for SQL filtering and as an external contract for retrieval
# callers (``yule memory search --kind ...``).
SOURCE_OBSIDIAN = "obsidian"
SOURCE_POLICY = "policy"
SOURCE_WORKFLOW = "workflow"


# Note kinds carried in frontmatter (research/decision/reference) — kept
# loose because policy docs and workflow artifacts may not have a kind.
NOTE_KIND_RESEARCH = "research"
NOTE_KIND_DECISION = "decision"
NOTE_KIND_REFERENCE = "reference"


@dataclass(frozen=True)
class MemoryDocument:
    """One indexable record.

    ``doc_id`` must be globally unique and stable across reindex cycles
    so the FTS5 ``content_rowid`` mapping can be reused (we use string
    keys like ``obsidian:Agents/Engineering/Research/note.md``).
    """

    doc_id: str
    source_kind: str
    title: str
    body: str
    path: Optional[str] = None
    role: Optional[str] = None
    task_type: Optional[str] = None
    note_kind: Optional[str] = None
    tags: Tuple[str, ...] = field(default_factory=tuple)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    extra: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MemorySearchResult:
    """One search hit.

    ``score`` is the FTS5 rank (lower = better — sqlite ``bm25()``).
    ``snippet`` is the formatted excerpt around the match. ``document``
    carries the metadata so callers can pivot on role/task_type/etc.
    """

    document: MemoryDocument
    score: float
    snippet: str
