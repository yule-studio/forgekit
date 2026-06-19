"""Forward-compat shim — ``vault`` now lives in ``packages/nexus`` (Nexus core, WT3).

Canonical: :mod:`nexus.vault`. Aliases the old dotted path (and submodules) to the
canonical package via ``sys.modules`` (object identity preserved) so existing
importers keep working. New code imports :mod:`nexus.vault` directly. Owner matrix:
``docs/forgekit-architecture-ownership.md``.
"""

from __future__ import annotations

from forgekit_console import _compat
from nexus import vault as _canon

_compat.alias_package(__name__, _canon)
