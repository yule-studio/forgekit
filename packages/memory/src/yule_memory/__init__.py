"""Local-first memory layer for engineering-agent.

Sources of truth this module indexes:

- Obsidian vault Markdown (research/decision/reference notes).
- Repo policy docs (``policies/**/*.md`` + agent ``CLAUDE.md``).
- Workflow session artifacts (the ``research_pack`` / ``research_synthesis``
  payload persisted in SQLite by ``research_persistence``).

The index lives in its own SQLite file (FTS5-backed) so retrieval is
fully local, deterministic, and testable without a vector store. The
schema and helpers are shaped so a future embeddings layer can be added
on top without rewriting callers.
"""

from .models import MemoryDocument, MemorySearchResult
from .indexer import (
    MEMORY_DB_ENV,
    MemoryIndex,
    open_memory_index,
    reindex_paths,
    reindex_workflow_sessions,
)
from .search import search


__all__ = [
    "MEMORY_DB_ENV",
    "MemoryDocument",
    "MemoryIndex",
    "MemorySearchResult",
    "open_memory_index",
    "reindex_paths",
    "reindex_workflow_sessions",
    "search",
]
