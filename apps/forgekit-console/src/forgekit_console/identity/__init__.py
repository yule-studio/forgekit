"""Forward-compat shim — agent identity now lives in ``packages/forgekit-config``.

Canonical: :mod:`forgekit_config.identity` (ForgeKit config core, WT2). This shim
aliases the old dotted path (and every submodule: ``models`` / ``registry`` /
``attribution``) to the canonical package via ``sys.modules``, preserving object
identity so existing importers keep working:

    from forgekit_console.identity import attribution   # → forgekit_config.identity.attribution
    from forgekit_console.identity import registry       # same module object

New code should import :mod:`forgekit_config.identity` directly. Owner matrix:
``docs/forgekit-architecture-ownership.md``.
"""

from __future__ import annotations

from forgekit_console import _compat
from forgekit_config import identity as _canon

_compat.alias_package(__name__, _canon)
