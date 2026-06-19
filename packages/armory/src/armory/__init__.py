"""armory — ForgeKit's catalog / capability registry (RWT2).

**Armory** = "무엇이 있는가": the catalog of Skills / Loadouts / Weapons (and their
capability vocabulary) that Hephaistos forges a plan FROM. A standalone ForgeKit named
core (ForgeKit / Nexus / Hephaistos / Armory) — Hephaistos is the smith (resolve /
orchestration / work-packet), Armory is the inventory it reads. See ``docs/vision.md``
and ``docs/armory.md``.

- ``armory.models``  — the spec vocabulary (Skill/Loadout/Weapon/Rune + NexusSourceRef).
- ``armory.catalog`` — the catalog data + accessors (all_skills/all_loadouts/…).

Depends on nothing internal (leaf) → Hephaistos depends on Armory, never the reverse.
"""

from __future__ import annotations

from . import catalog, models
from .catalog import (
    all_loadouts,
    all_skills,
    all_weapons,
    categories,
    loadout,
    skill,
    weapon,
)

__all__ = (
    "catalog", "models",
    "all_skills", "all_loadouts", "all_weapons", "skill", "loadout", "weapon", "categories",
)
