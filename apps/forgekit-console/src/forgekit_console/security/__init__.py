"""Forward-compat shim — ``security`` now lives in ``packages/forgekit-runtime`` (WT2).

Canonical: :mod:`forgekit_runtime.security`. Aliases the old dotted path (and submodules) to
the canonical package via ``sys.modules`` (object identity preserved). New code imports
:mod:`forgekit_runtime.security` directly. Owner matrix: ``docs/forgekit-architecture-ownership.md``.
"""

from __future__ import annotations

from forgekit_console import _compat
from forgekit_runtime import security as _canon

_compat.alias_package(__name__, _canon)
