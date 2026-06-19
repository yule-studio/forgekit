"""Forward-compat shim — Hephaistos now lives in ``packages/hephaistos`` (ForgeKit core, WT3).

Canonical: :mod:`hephaistos`. Aliases the old dotted path (and every submodule:
``models`` / ``armory`` / ``resolver`` / ``projection`` / ``verifier`` / ``nexus_read`` /
``nexus_ops``) to the canonical package via ``sys.modules`` (object identity preserved) so
existing importers keep working. New code imports :mod:`hephaistos` directly. Owner matrix:
``docs/forgekit-architecture-ownership.md``.
"""

from __future__ import annotations

from forgekit_console import _compat
import hephaistos as _canon

_compat.alias_package(__name__, _canon)
