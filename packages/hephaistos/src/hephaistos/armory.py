"""Forward-compat shim — the catalog moved to ``packages/armory`` (RWT2).

Canonical: :mod:`armory.catalog`. Hephaistos kept ``from . import armory`` call sites
(``armory.all_skills()`` etc.); this re-exports the catalog accessors so they keep
working. New code should import :mod:`armory` (the package) directly. See ``docs/armory.md``.
"""

from __future__ import annotations

from armory.catalog import (  # noqa: F401
    all_loadouts,
    all_skills,
    all_weapons,
    categories,
    clear_overlay,
    loadout,
    promoted_skills,
    register_promoted,
    skill,
    weapon,
)

__all__ = ("all_skills", "all_loadouts", "all_weapons", "skill", "loadout", "weapon",
           "categories", "register_promoted", "clear_overlay", "promoted_skills")
