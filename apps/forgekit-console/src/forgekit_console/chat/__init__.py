"""Forward-compat shim — ``chat`` now lives in ``packages/forgekit-provider``.

Canonical: :mod:`forgekit_provider.chat`. This shim aliases the old dotted path
(and every submodule) to the canonical package via ``sys.modules``, preserving
object identity so existing importers/tests keep working:

    from forgekit_console.chat import X        # → forgekit_provider.chat.X (same object)

New code should import :mod:`forgekit_provider.chat` directly. Owner matrix:
``docs/forgekit-architecture-ownership.md``.
"""

from __future__ import annotations

from forgekit_console import _compat
from forgekit_provider import chat as _canon

_compat.alias_package(__name__, _canon)
