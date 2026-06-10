"""Compatibility shim — re-exports ``yule_memory.indexer``.

The SQLite/FTS5 indexer moved to the standalone ``yule-memory`` package.
This module re-exports the public API plus the private helpers that
existing tests reach for (e.g. ``_document_from_workflow_session``) so
``from yule_orchestrator.memory.indexer import ...`` keeps resolving to
the identical objects.
"""

from yule_memory.indexer import (  # noqa: F401
    DEFAULT_DB_RELATIVE,
    MEMORY_DB_ENV,
    MemoryIndex,
    WorkflowSessionLike,
    _document_from_markdown_file,
    _document_from_workflow_session,
    open_memory_index,
    reindex_paths,
    reindex_workflow_sessions,
)


__all__ = [
    "DEFAULT_DB_RELATIVE",
    "MEMORY_DB_ENV",
    "MemoryIndex",
    "WorkflowSessionLike",
    "open_memory_index",
    "reindex_paths",
    "reindex_workflow_sessions",
]
