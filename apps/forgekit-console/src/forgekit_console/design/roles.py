"""Design role split (design WT2) — a design TEAM, not one role.

Three specialists + an orchestrator, with non-overlapping responsibilities and an
explicit boundary against frontend-engineer (FE implements; design specifies):

* ``ux-ui-designer``            — flows / IA / screen structure / spacing / interaction
  / accessibility · screen-level fixes.
* ``design-systems-designer``   — components / tokens / variants / library consistency
  / naming / design-to-code (FE handoff quality).
* ``illustration-brand-designer`` — hero / mascot / icon / visual language / brand.
* ``design-lead``               — orchestrator: synthesises raw source, finalises
  packet/projection, hands off to PM/gateway/tech-lead.

Pure data (role contracts) → testable; consumed by packets/projections (WT3+).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Tuple

ROLE_UX_UI = "ux-ui-designer"
ROLE_DESIGN_SYSTEMS = "design-systems-designer"
ROLE_ILLUSTRATION_BRAND = "illustration-brand-designer"
ROLE_DESIGN_LEAD = "design-lead"


@dataclass(frozen=True)
class DesignRole:
    id: str
    label: str
    responsibilities: Tuple[str, ...]
    inputs: Tuple[str, ...]
    outputs: Tuple[str, ...]
    owns: Tuple[str, ...]
    not_owns: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label,
                "responsibilities": list(self.responsibilities),
                "inputs": list(self.inputs), "outputs": list(self.outputs),
                "owns": list(self.owns), "not_owns": list(self.not_owns)}


DESIGN_ROLES: Mapping[str, DesignRole] = {
    ROLE_UX_UI: DesignRole(
        ROLE_UX_UI, "UX/UI Designer",
        responsibilities=("사용자 흐름", "정보 구조", "화면 구조", "spacing/layout",
                          "interaction", "accessibility/usability"),
        inputs=("restricted design source", "current repo UI", "operator notes"),
        outputs=("UXProjection", "screen-level 개선안"),
        owns=("flow", "ia", "screen-structure", "spacing", "interaction", "a11y"),
        not_owns=("component-library", "brand-visual", "frontend-구현")),
    ROLE_DESIGN_SYSTEMS: DesignRole(
        ROLE_DESIGN_SYSTEMS, "Design Systems Designer",
        responsibilities=("components", "tokens", "variants", "library consistency",
                          "naming/layout rules", "design-to-code handoff quality"),
        inputs=("restricted design source", "FE codebase 구조"),
        outputs=("DesignSystemProjection", "DesignSystemFixPacket"),
        owns=("components", "tokens", "variants", "naming", "design-to-code"),
        not_owns=("screen-flow", "brand-illustration", "frontend-구현")),
    ROLE_ILLUSTRATION_BRAND: DesignRole(
        ROLE_ILLUSTRATION_BRAND, "Illustration/Brand Designer",
        responsibilities=("hero/mascot/icon", "visual language", "marketing/branding",
                          "stylistic consistency"),
        inputs=("restricted design source", "brand guidelines"),
        outputs=("IllustrationProjection",),
        owns=("hero", "mascot", "icon", "visual-language", "brand"),
        not_owns=("flow", "component-tokens", "frontend-구현")),
    ROLE_DESIGN_LEAD: DesignRole(
        ROLE_DESIGN_LEAD, "Design Lead",
        responsibilities=("raw source 종합", "packet/projection 최종 정리",
                          "PM/gateway/tech-lead 핸드오프"),
        inputs=("3 specialist 산출물", "restricted design source"),
        outputs=("DesignReferencePacket", "역할별 projection 묶음"),
        owns=("synthesis", "handoff", "packet-finalize"),
        not_owns=("frontend-구현",)),
}

# the boundary the directive insists on: design specifies, FE implements.
FE_BOUNDARY = "design 역할은 frontend-engineer 구현을 대신하지 않는다 — 명세/projection 까지만"


def role(role_id: str) -> DesignRole:
    return DESIGN_ROLES[role_id]


def owns_overlap() -> Tuple[str, ...]:
    """Any ownership token claimed by >1 role (should be empty — non-overlapping)."""

    seen: dict = {}
    for r in DESIGN_ROLES.values():
        for o in r.owns:
            seen[o] = seen.get(o, 0) + 1
    return tuple(o for o, n in seen.items() if n > 1)


__all__ = (
    "ROLE_UX_UI", "ROLE_DESIGN_SYSTEMS", "ROLE_ILLUSTRATION_BRAND", "ROLE_DESIGN_LEAD",
    "DesignRole", "DESIGN_ROLES", "FE_BOUNDARY", "role", "owns_overlap",
)
