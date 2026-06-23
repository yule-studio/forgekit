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

from . import candidate, catalog, models
from .candidate import (
    ADOPT_NOW,
    COLLECT_FIRST,
    HOLD,
    AdoptionResult,
    AdoptionReview,
    ArmoryCandidate,
    AxisReview,
    PromotionResult,
    adopt_candidate,
    promote_candidate,
)
from .catalog import (
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

__all__ = (
    "candidate", "catalog", "models",
    "all_skills", "all_loadouts", "all_weapons", "skill", "loadout", "weapon", "categories",
    "register_promoted", "clear_overlay", "promoted_skills",
    "ArmoryCandidate", "PromotionResult", "promote_candidate",
    "AdoptionReview", "AxisReview", "AdoptionResult", "adopt_candidate",
    "ADOPT_NOW", "COLLECT_FIRST", "HOLD",
)
