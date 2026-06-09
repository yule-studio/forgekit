"""Search interface over the local memory index.

Light wrapper around the FTS5 ``documents_fts`` virtual table built in
``indexer``. Translates one query string + optional metadata filters
into a ranked list of :class:`MemorySearchResult`.

The function is intentionally minimal — retrieval callers compose
multiple :func:`search` calls (e.g. role-shaped query → memory miss →
broader query) rather than baking complex policy into this layer.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .indexer import open_memory_index
from .models import MemoryDocument, MemorySearchResult


# Tokens that FTS5's MATCH parser interprets specially. We escape user
# input by quoting tokens, so a stray ``"`` from the user must be
# stripped (else MATCH errors out and the search returns nothing).
_FTS5_BAD_TOKEN_CHARS = re.compile(r"[\"]")


def search(
    query: str,
    *,
    limit: int = 10,
    source_kind: Optional[str] = None,
    role: Optional[str] = None,
    note_kind: Optional[str] = None,
    task_type: Optional[str] = None,
    db_path: Optional[Path] = None,
    repo_root: Optional[Path] = None,
) -> List[MemorySearchResult]:
    """Run an FTS5 search and return ranked hits.

    ``query`` is plain text; tokens are extracted and OR-joined so users
    can paste a sentence without thinking about FTS syntax. An empty
    query (after sanitisation) returns ``[]``. Filters are conjunctive —
    matching every non-None constraint.
    """

    fts_query = _build_fts_query(query)
    if not fts_query:
        return []

    where_clauses = ["documents.id = documents_fts.rowid"]
    params: List[object] = []
    where_clauses.append("documents_fts MATCH ?")
    params.append(fts_query)
    if source_kind is not None:
        where_clauses.append("documents.source_kind = ?")
        params.append(source_kind)
    if role is not None:
        where_clauses.append("documents.role = ?")
        params.append(role)
    if note_kind is not None:
        where_clauses.append("documents.note_kind = ?")
        params.append(note_kind)
    if task_type is not None:
        where_clauses.append("documents.task_type = ?")
        params.append(task_type)

    sql = f"""
        SELECT
            documents.doc_id,
            documents.source_kind,
            documents.title,
            documents.body,
            documents.path,
            documents.role,
            documents.task_type,
            documents.note_kind,
            documents.tags_json,
            documents.created_at,
            documents.updated_at,
            documents.extra_json,
            bm25(documents_fts) AS score,
            snippet(documents_fts, 1, '«', '»', '…', 12) AS snippet
        FROM documents
        JOIN documents_fts ON documents.id = documents_fts.rowid
        WHERE {' AND '.join(where_clauses)}
        ORDER BY score
        LIMIT ?
    """
    params.append(int(max(1, limit)))

    with open_memory_index(db_path=db_path, repo_root=repo_root) as index:
        cur = index.connection.execute(sql, params)
        rows = cur.fetchall()

    return [_row_to_result(row) for row in rows]


def _build_fts_query(query: str) -> str:
    cleaned = _FTS5_BAD_TOKEN_CHARS.sub(" ", query or "").strip()
    if not cleaned:
        return ""
    tokens = [tok for tok in cleaned.split() if tok]
    if not tokens:
        return ""
    quoted = [f'"{tok}"' for tok in tokens]
    return " OR ".join(quoted)


def _row_to_result(row: sqlite3.Row) -> MemorySearchResult:
    document = MemoryDocument(
        doc_id=row["doc_id"],
        source_kind=row["source_kind"],
        title=row["title"] or "",
        body=row["body"] or "",
        path=row["path"],
        role=row["role"],
        task_type=row["task_type"],
        note_kind=row["note_kind"],
        tags=tuple(_load_json_list(row["tags_json"])),
        created_at=_parse_iso(row["created_at"]),
        updated_at=_parse_iso(row["updated_at"]),
        extra=_load_json_object(row["extra_json"]),
    )
    return MemorySearchResult(
        document=document,
        score=float(row["score"]),
        snippet=row["snippet"] or "",
    )


def _load_json_list(value) -> Sequence[str]:
    if not value:
        return ()
    try:
        loaded = json.loads(value)
    except (TypeError, ValueError):
        return ()
    if not isinstance(loaded, list):
        return ()
    return tuple(str(v) for v in loaded)


def _load_json_object(value):
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {str(k): str(v) for k, v in loaded.items()}


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
