"""Forward-compat shim — console data models now live in ``packages/forgekit-contracts``.

Canonical: :mod:`forgekit_contracts.models` (ForgeKit contracts core, WT2). This shim
aliases the old path to the canonical module via ``sys.modules`` (object identity
preserved) so existing importers keep working:

    from ..models import KIND_INFO          # → forgekit_contracts.models.KIND_INFO
    from forgekit_console.models import ...  # same module object

New code should import :mod:`forgekit_contracts.models` directly. Owner matrix:
``docs/forgekit-architecture-ownership.md``.
"""

from __future__ import annotations

from forgekit_console import _compat
from forgekit_contracts import models as _canon

_compat.alias_package(__name__, _canon)
