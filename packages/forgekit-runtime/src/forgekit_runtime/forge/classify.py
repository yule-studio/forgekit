"""Forge-plan risk classification — safe / risky / destructive (governance backbone).

A Hephaistos :class:`ResolvedForgePlan` is "what to equip + the work packet". Before it
can run through the execution gate, its risk has to be derived from the SAME inputs an
operator would weigh — and from MORE than the packet's own ``approval_level`` (which the
resolver currently hardcodes to L2). We take the STRICTEST of:

* the work packet's declared ``approval_level`` (L2/L3/L4),
* the **weapon safety** of every required weapon (an armory ``risky`` weapon bumps the
  plan to at least risky — installing/using a risky tool is not a safe-class act),
* the goal/forbidden-scope **wording** (deploy/secret/schema/auth → destructive), reusing
  the one classifier ladder (:mod:`forgekit_runtime.autopilot.approval`).

Safe-by-rejection: an unknown weapon is treated as risky, not silently safe. The weapon
safety resolver is injectable so the classifier stays pure + testable; the default reads
the armory catalog.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from ..autopilot import approval as A

# action classes (mirror decision_lane.enforcement)
SAFE = "safe"
RISKY = "risky"
DESTRUCTIVE = "destructive"

_LEVEL_ORDER = {A.L2_INTERNAL_APPROVE: 0, A.L3_USER_APPROVE: 1, A.L4_RESTRICTED: 2}
_CLASS_BY_LEVEL = {A.L2_INTERNAL_APPROVE: SAFE, A.L3_USER_APPROVE: RISKY,
                   A.L4_RESTRICTED: DESTRUCTIVE}

# armory weapon safety values (kept local to avoid a hard armory import at module load)
_WEAPON_RISKY = "risky"


@dataclass(frozen=True)
class ForgeClassification:
    """The derived risk of a forge plan + WHY (explainable, evidence-able)."""

    action_class: str                       # safe / risky / destructive
    approval_level: str                     # autopilot.approval L*
    risky_weapons: Tuple[str, ...] = ()
    unknown_weapons: Tuple[str, ...] = ()
    reasons: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"action_class": self.action_class, "approval_level": self.approval_level,
                "risky_weapons": list(self.risky_weapons),
                "unknown_weapons": list(self.unknown_weapons), "reasons": list(self.reasons)}


def _default_weapon_safety(weapon_id: str) -> Optional[str]:
    """Look a weapon's safety up in the armory catalog (lazy import). None if unknown."""

    try:
        from armory import weapon as _weapon
    except Exception:  # noqa: BLE001 — armory absent → caller treats as unknown (risky)
        return None
    spec = _weapon(weapon_id)
    return spec.safety if spec is not None else None


def _max_level(a: str, b: str) -> str:
    return a if _LEVEL_ORDER.get(a, 1) >= _LEVEL_ORDER.get(b, 1) else b


def classify_forge_plan(
    plan,
    *,
    weapon_safety: Optional[Callable[[str], Optional[str]]] = None,
) -> ForgeClassification:
    """Derive the strictest risk class for *plan* from packet level + weapons + wording."""

    resolve_safety = weapon_safety or _default_weapon_safety
    reasons = []

    packet = getattr(plan, "packet_draft", None)
    level = getattr(packet, "approval_level", "") or A.L2_INTERNAL_APPROVE
    if level not in _LEVEL_ORDER:
        level = A.L2_INTERNAL_APPROVE
    reasons.append(f"packet approval_level={level}")

    # wording: the GOAL drives the restricted/risky ladder. We deliberately do NOT feed
    # ``forbidden_scope`` in — that field NAMES deploy/schema/auth as the guardrail the
    # executor must not cross, so including it would misclassify every plan as destructive.
    goal = getattr(plan, "request", "") or getattr(packet, "goal", "")
    text_level = A.classify_level(goal)
    if _LEVEL_ORDER.get(text_level, 0) > _LEVEL_ORDER.get(level, 0):
        reasons.append(f"goal wording → {text_level}")
    level = _max_level(level, text_level)

    # weapon safety: a risky/unknown weapon bumps the plan to at least risky
    risky_weapons = []
    unknown_weapons = []
    for w in getattr(plan, "required_weapons", ()) or ():
        safety = resolve_safety(w)
        if safety is None:
            unknown_weapons.append(w)
        elif safety == _WEAPON_RISKY:
            risky_weapons.append(w)
    if risky_weapons:
        reasons.append(f"risky weapons: {', '.join(risky_weapons)}")
        level = _max_level(level, A.L3_USER_APPROVE)
    if unknown_weapons:
        # safe-by-rejection: an unknown weapon is not auto-safe
        reasons.append(f"unknown weapons(보수적 risky): {', '.join(unknown_weapons)}")
        level = _max_level(level, A.L3_USER_APPROVE)

    return ForgeClassification(
        action_class=_CLASS_BY_LEVEL.get(level, RISKY), approval_level=level,
        risky_weapons=tuple(risky_weapons), unknown_weapons=tuple(unknown_weapons),
        reasons=tuple(reasons))


__all__ = ("SAFE", "RISKY", "DESTRUCTIVE", "ForgeClassification", "classify_forge_plan")
