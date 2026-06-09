"""Compatibility shim — re-exports ``yule_memory.models``.

Data models moved to the standalone ``yule-memory`` package. This module
re-exports every public name (dataclasses + source/note-kind constants)
so existing ``from yule_orchestrator.memory.models import ...`` imports
keep resolving to the identical objects.
"""

from yule_memory.models import (
    NOTE_KIND_DECISION,
    NOTE_KIND_REFERENCE,
    NOTE_KIND_RESEARCH,
    SOURCE_OBSIDIAN,
    SOURCE_POLICY,
    SOURCE_WORKFLOW,
    MemoryDocument,
    MemorySearchResult,
)


__all__ = [
    "NOTE_KIND_DECISION",
    "NOTE_KIND_REFERENCE",
    "NOTE_KIND_RESEARCH",
    "SOURCE_OBSIDIAN",
    "SOURCE_POLICY",
    "SOURCE_WORKFLOW",
    "MemoryDocument",
    "MemorySearchResult",
]
