"""Forward-compat shim — ``providers`` now lives in ``packages/forgekit-provider``.

Canonical: :mod:`forgekit_provider.providers`. This shim aliases the old dotted path
(and every submodule) to the canonical package via ``sys.modules``, preserving
object identity so existing importers/tests keep working:

    from forgekit_console.providers import X        # → forgekit_provider.providers.X (same object)

New code should import :mod:`forgekit_provider.providers` directly. Owner matrix:
``docs/forgekit-architecture-ownership.md``.
"""

from __future__ import annotations

from forgekit_console import _compat
from forgekit_provider import providers as _canon

_compat.alias_package(__name__, _canon)
