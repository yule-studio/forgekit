"""SQLite/FTS5-backed memory indexer.

Schema:

- ``documents`` table — one row per :class:`MemoryDocument`. Stores
  metadata as plain columns so callers can filter without joining.
- ``documents_fts`` virtual table (FTS5) — searchable text columns
  (title, body) keyed to ``documents.rowid`` via ``content_rowid``.

The indexer is **idempotent**: calling :func:`reindex_paths` /
:func:`reindex_workflow_sessions` twice produces the same index state.
``upsert_document`` deletes any prior row sharing the same ``doc_id``
before inserting, so frontmatter or body changes are picked up.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Optional, Sequence, Tuple

from .models import (
    MemoryDocument,
    NOTE_KIND_DECISION,
    NOTE_KIND_REFERENCE,
    NOTE_KIND_RESEARCH,
    SOURCE_OBSIDIAN,
    SOURCE_POLICY,
    SOURCE_WORKFLOW,
)


MEMORY_DB_ENV = "YULE_MEMORY_DB_PATH"
DEFAULT_DB_RELATIVE = Path(".cache") / "yule" / "memory.sqlite3"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class MemoryIndex:
    """Thin wrapper around the index SQLite connection.

    Use as a context manager (``with open_memory_index() as idx: ...``)
    so writes auto-commit on exit and the connection closes cleanly.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        _ensure_schema(self._conn)

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def upsert_document(self, doc: MemoryDocument) -> None:
        """Insert or replace one document by ``doc_id``.

        ``tags`` is stored as a JSON array; ``extra`` likewise. FTS5
        index entries are removed and rewritten so partial updates can't
        leave stale tokens behind.
        """

        if not doc.doc_id:
            raise ValueError("MemoryDocument.doc_id must not be empty")

        cur = self._conn.cursor()
        cur.execute(
            "DELETE FROM documents WHERE doc_id = ?",
            (doc.doc_id,),
        )
        cur.execute(
            """
            INSERT INTO documents (
                doc_id, source_kind, title, body, path, role, task_type,
                note_kind, tags_json, created_at, updated_at, extra_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc.doc_id,
                doc.source_kind,
                doc.title,
                doc.body,
                doc.path,
                doc.role,
                doc.task_type,
                doc.note_kind,
                json.dumps(list(doc.tags), ensure_ascii=False),
                _iso_or_none(doc.created_at),
                _iso_or_none(doc.updated_at),
                json.dumps(dict(doc.extra), ensure_ascii=False),
            ),
        )
        rowid = cur.lastrowid
        cur.execute(
            "INSERT INTO documents_fts (rowid, title, body) VALUES (?, ?, ?)",
            (rowid, doc.title or "", doc.body or ""),
        )

    def delete_by_source(self, source_kind: str) -> int:
        """Remove all docs with the given ``source_kind``. Returns count.

        Used by reindex helpers to wipe the previous slice before a
        fresh ingest, so deleted notes don't linger as ghost hits.
        """

        cur = self._conn.cursor()
        cur.execute("SELECT rowid FROM documents WHERE source_kind = ?", (source_kind,))
        rowids = [r[0] for r in cur.fetchall()]
        if not rowids:
            return 0
        cur.execute(
            f"DELETE FROM documents_fts WHERE rowid IN ({','.join('?' * len(rowids))})",
            rowids,
        )
        cur.execute(
            f"DELETE FROM documents WHERE rowid IN ({','.join('?' * len(rowids))})",
            rowids,
        )
        return cur.rowcount

    def count_documents(self, source_kind: Optional[str] = None) -> int:
        cur = self._conn.cursor()
        if source_kind is None:
            cur.execute("SELECT COUNT(*) FROM documents")
        else:
            cur.execute(
                "SELECT COUNT(*) FROM documents WHERE source_kind = ?",
                (source_kind,),
            )
        return int(cur.fetchone()[0])


@contextmanager
def open_memory_index(
    db_path: Optional[Path] = None,
    *,
    repo_root: Optional[Path] = None,
) -> Iterator[MemoryIndex]:
    """Open the memory index, yielding a :class:`MemoryIndex` wrapper.

    Resolution order for the SQLite path:
      1. explicit ``db_path`` argument
      2. ``YULE_MEMORY_DB_PATH`` env var
      3. ``<repo_root>/.cache/yule/memory.sqlite3``
      4. ``<cwd>/.cache/yule/memory.sqlite3``
    """

    resolved = _resolve_db_path(db_path=db_path, repo_root=repo_root)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(resolved)
    conn.row_factory = sqlite3.Row
    try:
        index = MemoryIndex(conn)
        yield index
        conn.commit()
    finally:
        conn.close()


def reindex_paths(
    *,
    paths: Sequence[Path],
    source_kind: str,
    index: MemoryIndex,
    base_dir: Optional[Path] = None,
) -> int:
    """Reindex every ``.md`` file rooted at one of *paths* under one ``source_kind``.

    Wipes the existing slice for ``source_kind`` first so deletions on
    disk are reflected. Returns the number of documents indexed.
    """

    index.delete_by_source(source_kind)
    indexed = 0
    for root in paths:
        root_path = Path(root)
        if not root_path.exists():
            continue
        if root_path.is_file():
            iterable: Iterable[Path] = [root_path]
        else:
            iterable = sorted(root_path.rglob("*.md"))
        for md_path in iterable:
            try:
                doc = _document_from_markdown_file(
                    md_path=md_path,
                    source_kind=source_kind,
                    base_dir=base_dir,
                )
            except OSError:
                continue
            if doc is None:
                continue
            index.upsert_document(doc)
            indexed += 1
    return indexed


def reindex_workflow_sessions(
    *,
    sessions: Iterable["WorkflowSessionLike"],
    index: MemoryIndex,
) -> int:
    """Reindex workflow session artifacts.

    Each session's research_pack / synthesis is unpacked into a single
    indexable document. Sessions without research artifacts are skipped
    so the index stays focused on retrievable content.
    """

    index.delete_by_source(SOURCE_WORKFLOW)
    indexed = 0
    for session in sessions:
        doc = _document_from_workflow_session(session)
        if doc is None:
            continue
        index.upsert_document(doc)
        indexed += 1
    return indexed


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)


def _document_from_markdown_file(
    *,
    md_path: Path,
    source_kind: str,
    base_dir: Optional[Path],
) -> Optional[MemoryDocument]:
    text = md_path.read_text(encoding="utf-8", errors="replace")
    frontmatter, body = _split_frontmatter(text)
    title = (
        _frontmatter_value(frontmatter, "title")
        or _first_heading(body)
        or md_path.stem
    )
    note_kind = _normalize_note_kind(_frontmatter_value(frontmatter, "kind"))
    role = _normalize_role(_first_role(_frontmatter_value(frontmatter, "roles")))
    task_type = _frontmatter_value(frontmatter, "task_type")
    tags = tuple(_split_yaml_list(_frontmatter_value(frontmatter, "tags")))
    created_at = _parse_iso_datetime(_frontmatter_value(frontmatter, "created_at"))
    relative = _relative_path(md_path, base_dir)
    doc_id = f"{source_kind}:{relative}"
    # Project memory-policy section 4 reuse-boost markers + topic (recall-policy
    # section 4 topic-aware recall) into extra (read-side only).
    extra: dict = {}
    for marker in ("canonical", "reusable", "status", "topic"):
        value = _frontmatter_value(frontmatter, marker)
        if value not in (None, ""):
            extra[marker] = str(value)
    return MemoryDocument(
        doc_id=doc_id,
        source_kind=source_kind,
        title=title.strip() or md_path.stem,
        body=body.strip(),
        path=str(relative),
        role=role,
        task_type=task_type,
        note_kind=note_kind,
        tags=tags,
        created_at=created_at,
        updated_at=_mtime_to_datetime(md_path),
        extra=extra,
    )


class WorkflowSessionLike:  # pragma: no cover - structural typing helper
    """Duck-typed protocol for workflow session inputs.

    We don't depend on the concrete dataclass to keep the indexer
    decoupled from agents.workflow_state — any object with the same
    attributes works.
    """

    session_id: str
    prompt: str
    task_type: str
    executor_role: Optional[str]
    extra: Mapping[str, object]
    created_at: datetime
    updated_at: datetime


def _document_from_workflow_session(session: WorkflowSessionLike) -> Optional[MemoryDocument]:
    extra = dict(getattr(session, "extra", None) or {})
    pack = extra.get("research_pack")
    synthesis = extra.get("research_synthesis")
    synthesis_text = extra.get("research_synthesis_text")
    if not pack and not synthesis and not synthesis_text:
        return None

    title_parts = []
    if isinstance(pack, dict):
        pack_title = pack.get("title")
        if pack_title:
            title_parts.append(str(pack_title))
    if not title_parts and getattr(session, "prompt", None):
        title_parts.append(str(session.prompt))
    title = " — ".join(title_parts) or f"session {getattr(session, 'session_id', '?')}"

    body_chunks: list[str] = []
    if isinstance(pack, dict):
        if pack.get("summary"):
            body_chunks.append(str(pack.get("summary")))
        urls = pack.get("urls") or []
        if urls:
            body_chunks.append("URLs:\n" + "\n".join(f"- {u}" for u in urls))
        for source in pack.get("sources") or []:
            if not isinstance(source, dict):
                continue
            row = " · ".join(
                str(part)
                for part in (
                    source.get("author_role"),
                    source.get("title"),
                    source.get("source_url"),
                )
                if part
            )
            if row:
                body_chunks.append(row)
    if isinstance(synthesis, dict):
        consensus = synthesis.get("consensus")
        if consensus:
            body_chunks.append(f"## 합의안\n{consensus}")
        for label, key in (
            ("## 해야 할 일", "todos"),
            ("## 더 조사할 것", "open_research"),
            ("## 사용자 결정 필요", "user_decisions_needed"),
        ):
            items = synthesis.get(key) or []
            if items:
                body_chunks.append(label + "\n" + "\n".join(f"- {it}" for it in items))
    elif isinstance(synthesis_text, str) and synthesis_text:
        body_chunks.append(synthesis_text)

    note_kind = NOTE_KIND_DECISION if synthesis else NOTE_KIND_RESEARCH
    tags_value = pack.get("tags") if isinstance(pack, dict) else None
    tags = tuple(_split_yaml_list(tags_value))

    return MemoryDocument(
        doc_id=f"{SOURCE_WORKFLOW}:{getattr(session, 'session_id', '')}",
        source_kind=SOURCE_WORKFLOW,
        title=title,
        body="\n\n".join(body_chunks).strip(),
        path=None,
        role=getattr(session, "executor_role", None),
        task_type=getattr(session, "task_type", None),
        note_kind=note_kind,
        tags=tags,
        created_at=getattr(session, "created_at", None),
        updated_at=getattr(session, "updated_at", None),
        extra={"session_id": str(getattr(session, "session_id", ""))},
    )


def _split_frontmatter(text: str) -> tuple[str, str]:
    text = text.lstrip("\ufeff")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return "", text
    return match.group(1), match.group(2)


def _frontmatter_value(frontmatter: str, key: str) -> Optional[str]:
    if not frontmatter:
        return None
    pattern = re.compile(rf"^{re.escape(key)}\s*:\s*(.*?)$", re.MULTILINE)
    match = pattern.search(frontmatter)
    if not match:
        return None
    raw = match.group(1).strip()
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        raw = raw[1:-1]
    return raw or None


def _first_role(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = value.strip().strip("[]")
    if not cleaned:
        return None
    parts = [p.strip().strip("\"'") for p in cleaned.split(",")]
    parts = [p for p in parts if p]
    return parts[0] if parts else None


def _split_yaml_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    if not text:
        return []
    text = text.strip("[]")
    parts = [p.strip().strip("\"'") for p in text.split(",")]
    return [p for p in parts if p]


def _normalize_note_kind(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    normalized = value.strip().lower()
    if normalized in {"decision", "decisions"}:
        return NOTE_KIND_DECISION
    if normalized in {"reference", "references"}:
        return NOTE_KIND_REFERENCE
    if normalized in {"research", "researches"}:
        return NOTE_KIND_RESEARCH
    return normalized or None


def _normalize_role(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = value.strip()
    return cleaned or None


def _first_heading(body: str) -> Optional[str]:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return None


def _relative_path(path: Path, base_dir: Optional[Path]) -> Path:
    if base_dir is None:
        return Path(path.name)
    try:
        return path.resolve().relative_to(base_dir.resolve())
    except ValueError:
        return Path(path.name)


def _iso_or_none(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    return str(value)


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    cleaned = value.strip().rstrip("Z")
    if not cleaned:
        return None
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def _mtime_to_datetime(path: Path) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None


def _resolve_db_path(
    *,
    db_path: Optional[Path],
    repo_root: Optional[Path],
) -> Path:
    if db_path is not None:
        return Path(db_path).expanduser()
    env_path = os.getenv(MEMORY_DB_ENV)
    if env_path and env_path.strip():
        return Path(env_path).expanduser()
    if repo_root is not None:
        return Path(repo_root) / DEFAULT_DB_RELATIVE
    yule_repo = os.getenv("YULE_REPO_ROOT")
    base_dir = Path(yule_repo) if yule_repo else Path.cwd()
    return base_dir / DEFAULT_DB_RELATIVE


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id TEXT NOT NULL UNIQUE,
            source_kind TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            path TEXT,
            role TEXT,
            task_type TEXT,
            note_kind TEXT,
            tags_json TEXT,
            created_at TEXT,
            updated_at TEXT,
            extra_json TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_documents_source_kind ON documents (source_kind)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_documents_role ON documents (role)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_documents_note_kind ON documents (note_kind)"
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
            title, body, content='documents', content_rowid='id', tokenize='unicode61'
        )
        """
    )
    conn.commit()
