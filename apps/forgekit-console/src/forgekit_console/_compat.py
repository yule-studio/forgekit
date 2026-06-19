"""Forward-compat helper for the WT2~WT4 ForgeKit core extraction.

When a console subpackage (e.g. ``forgekit_console.policy``) moves out to a
``packages/*`` owner (e.g. ``forgekit_provider.policy``), the old dotted path must
keep resolving to the SAME module objects so existing importers and tests
(``from forgekit_console.policy import routing``) work unchanged. ``alias_package``
points the old package name — and every submodule under it — at the canonical
package via ``sys.modules``, preserving object identity.

Usage in the old path's ``__init__.py`` (now a thin shim)::

    from forgekit_console import _compat
    from forgekit_provider import policy as _canon
    _compat.alias_package(__name__, _canon)

New code should import the canonical ``forgekit_provider.*`` path directly. Owner
matrix: ``docs/forgekit-architecture-ownership.md``.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from types import ModuleType


def alias_package(old_fullname: str, canonical: ModuleType) -> ModuleType:
    """Alias ``old_fullname`` (and all submodules) to *canonical*, identity-preserved.

    Returns the canonical module so a shim ``__init__`` can ``= alias_package(...)``
    if it likes. Recurses into subpackages so nested old paths resolve too.
    """

    sys.modules[old_fullname] = canonical
    pkg_path = getattr(canonical, "__path__", None)
    if pkg_path is None:
        return canonical  # plain module, nothing to walk
    for info in pkgutil.iter_modules(pkg_path):
        sub_canon = importlib.import_module(f"{canonical.__name__}.{info.name}")
        old_sub = f"{old_fullname}.{info.name}"
        if info.ispkg:
            alias_package(old_sub, sub_canon)
        else:
            sys.modules[old_sub] = sub_canon
    return canonical
