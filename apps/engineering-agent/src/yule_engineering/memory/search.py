"""Compatibility shim — re-exports ``yule_memory.search``.

The FTS5 search interface moved to the standalone ``yule-memory``
package. This module re-exports :func:`search` so existing
``from yule_engineering.memory.search import search`` imports keep
resolving to the identical object.
"""

from yule_memory.search import search  # noqa: F401


__all__ = ["search"]
