"""armory — ForgeKit's catalog / capability registry (RWT2).

**Armory** = "무엇이 있는가": the catalog of Skills / Loadouts / Weapons (and their
capability vocabulary) that Hephaistos forges a plan FROM. A standalone ForgeKit named
core (ForgeKit / Nexus / Hephaistos / Armory) — Hephaistos is the smith (resolve /
orchestration / work-packet), Armory is the inventory it reads. See ``docs/vision.md``
and ``docs/armory.md``.

- ``armory.models``  — the spec vocabulary (Skill/Loadout/Weapon/Rune + NexusSourceRef).
- ``armory.catalog`` — the catalog data + accessors (all_skills/all_loadouts/…).
- ``armory.candidate`` — intake → catalog promotion gate (contract validation).
- ``armory.adoption`` — ForgeKit 도입 효율 검토(8축 + 3축 검토 → adopt-now/collect-first/hold),
  with ``adoption_registry`` holding the evaluated external-candidate set.

Depends on nothing internal (leaf) → Hephaistos depends on Armory, never the reverse.
"""

from __future__ import annotations

from . import adoption, adoption_registry, candidate, catalog, models
from .adoption import (
    AdoptionReview,
    ReviewerVerdict,
    by_verdict,
    invalid_reviews,
    validate_review,
)
from .adoption_registry import adoption_registry as adoption_registry_reviews
from .candidate import ArmoryCandidate, PromotionResult, promote_candidate
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
    "candidate", "catalog", "models", "adoption", "adoption_registry",
    "all_skills", "all_loadouts", "all_weapons", "skill", "loadout", "weapon", "categories",
    "register_promoted", "clear_overlay", "promoted_skills",
    "ArmoryCandidate", "PromotionResult", "promote_candidate",
    "AdoptionReview", "ReviewerVerdict", "validate_review", "by_verdict", "invalid_reviews",
    "adoption_registry_reviews",
)
