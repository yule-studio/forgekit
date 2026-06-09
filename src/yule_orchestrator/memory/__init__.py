"""Compatibility shim — memory layer now lives in ``yule_memory``.

The local-first memory index (SQLite/FTS5 indexer + search + models) was
extracted into the standalone ``yule-memory`` package so it carries no
runtime/agent/discord dependencies. This module re-exports the same
public names so every existing
``from yule_orchestrator.memory import ...`` keeps resolving to the
identical objects.

``retrieval`` stays in this package (not in ``yule_memory``) because it
depends on ``..agents.deliberation`` — moving it would break the
dependency rule that ``yule_memory`` must not import agent internals.
"""

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
