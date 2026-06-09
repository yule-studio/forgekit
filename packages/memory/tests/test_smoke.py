"""Smoke tests for the extracted ``yule_memory`` package.

Covers the three layers end to end without any ``yule_orchestrator``
import: model construction, indexing a temp Markdown doc, and searching
it back out via the FTS5 index.
"""

from __future__ import annotations

from pathlib import Path

from yule_memory import (
    MEMORY_DB_ENV,
    MemoryDocument,
    MemoryIndex,
    MemorySearchResult,
    open_memory_index,
    reindex_paths,
    reindex_workflow_sessions,
    search,
)
from yule_memory.models import SOURCE_OBSIDIAN


def test_public_api_is_importable() -> None:
    assert MEMORY_DB_ENV == "YULE_MEMORY_DB_PATH"
    assert callable(open_memory_index)
    assert callable(reindex_paths)
    assert callable(reindex_workflow_sessions)
    assert callable(search)
    assert MemoryIndex is not None


def test_model_construction() -> None:
    doc = MemoryDocument(
        doc_id="obsidian:note.md",
        source_kind=SOURCE_OBSIDIAN,
        title="Hello Memory",
        body="alpha beta gamma",
    )
    assert doc.doc_id == "obsidian:note.md"
    assert doc.tags == ()
    assert dict(doc.extra) == {}

    result = MemorySearchResult(document=doc, score=1.0, snippet="…alpha…")
    assert result.document is doc
    assert result.score == 1.0


def test_index_and_search_roundtrip(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "research-widget.md"
    note.write_text(
        "---\ntitle: Widget Research\nkind: research\n---\n"
        "The widget pipeline uses a deterministic queue.\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "memory.sqlite3"
    with open_memory_index(db_path=db_path) as index:
        count = reindex_paths(
            paths=[vault],
            source_kind=SOURCE_OBSIDIAN,
            index=index,
            base_dir=vault,
        )
    assert count == 1

    hits = search("widget pipeline", db_path=db_path)
    assert hits, "expected at least one hit for an indexed term"
    top = hits[0]
    assert isinstance(top, MemorySearchResult)
    assert top.document.source_kind == SOURCE_OBSIDIAN
    assert top.document.note_kind == "research"


def test_search_empty_query_returns_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.sqlite3"
    with open_memory_index(db_path=db_path):
        pass
    assert search("   ", db_path=db_path) == []
