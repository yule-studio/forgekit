"""Design discomfort → improvement packet (design WT4).

Takes a UX/UI/brand discomfort (from a reference, screenshot, repo UI, or operator
note) and structures it — not "예쁜지/별로인지" but WHY it's a user problem — then
routes it to the right design specialist and promotes it to a typed packet:

* ux-ui-designer            → screen/flow/spacing issues → FrontendImplementationPacket
* design-systems-designer   → token/component consistency → DesignSystemFixPacket
* illustration-brand-designer → visual identity/brand → PMPacket/brand note

Reuses :mod:`selfimprove` for the packet shape + :mod:`uiref` for reference-awareness.
Pure → testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .roles import ROLE_DESIGN_SYSTEMS, ROLE_ILLUSTRATION_BRAND, ROLE_UX_UI

PACKET_FRONTEND_IMPL = "FrontendImplementationPacket"
PACKET_DESIGN_SYSTEM_FIX = "DesignSystemFixPacket"
PACKET_PM = "PMPacket"
PACKET_REPO_IMPROVEMENT = "RepoImprovementPacket"

_SYSTEM = ("토큰", "token", "컴포넌트", "component", "일관성", "consistency", "naming", "variant")
_VISUAL = ("색", "color", "아이콘", "icon", "브랜드", "brand", "일러스트", "hero", "mascot", "로고")
# everything else (spacing/flow/layout/a11y) → ux-ui


@dataclass(frozen=True)
class DesignDiscomfort:
    user_discomfort: str
    why_it_matters: str = ""
    affected_flow: str = ""
    visual_issue: str = ""
    ux_issue: str = ""
    system_issue: str = ""
    recommended_owner: str = ROLE_UX_UI

    def to_dict(self) -> dict:
        return {
            "user_discomfort": self.user_discomfort, "why_it_matters": self.why_it_matters,
            "affected_flow": self.affected_flow, "visual_issue": self.visual_issue,
            "ux_issue": self.ux_issue, "system_issue": self.system_issue,
            "recommended_owner": self.recommended_owner,
        }


def analyze_discomfort(text: str, *, affected_flow: str = "") -> DesignDiscomfort:
    """Structure a raw discomfort into owner + issue type (user-value framing)."""

    low = (text or "").lower()
    if any(k in low for k in _SYSTEM):
        owner, issue = ROLE_DESIGN_SYSTEMS, "system_issue"
        why = "디자인 시스템 불일치 — FE handoff 품질/유지보수 저하"
    elif any(k in low for k in _VISUAL):
        owner, issue = ROLE_ILLUSTRATION_BRAND, "visual_issue"
        why = "시각 정체성/브랜드 일관성 훼손 — 신뢰/인지 저하"
    else:
        owner, issue = ROLE_UX_UI, "ux_issue"
        why = "사용 흐름/간격/접근성 마찰 — 작업 완료율/만족도 저하"
    kwargs = {"user_discomfort": text, "why_it_matters": why,
              "affected_flow": affected_flow, "recommended_owner": owner, issue: text}
    return DesignDiscomfort(**kwargs)


def _packet_kind(owner: str) -> str:
    return {
        ROLE_DESIGN_SYSTEMS: PACKET_DESIGN_SYSTEM_FIX,
        ROLE_ILLUSTRATION_BRAND: PACKET_PM,
        ROLE_UX_UI: PACKET_FRONTEND_IMPL,
    }.get(owner, PACKET_REPO_IMPROVEMENT)


def promote_to_packet(discomfort: DesignDiscomfort, *, reference=None):
    """Promote a structured discomfort to a typed improvement packet (reference-aware)."""

    from ..selfimprove import make_packet
    from ..uiref import figma_reference

    ref = reference or figma_reference()
    kind = _packet_kind(discomfort.recommended_owner)
    ref_note = "" if ref.usable else f" (reference: {ref.kind})"
    pkt = make_packet(
        discomfort.user_discomfort,
        why=discomfort.why_it_matters + ref_note,
        area="ui-design", change=f"{discomfort.recommended_owner} 가 {kind} 로 처리",
        owner=discomfort.recommended_owner, origin="design-discomfort",
        discomfort=discomfort.user_discomfort)
    return kind, pkt


__all__ = (
    "PACKET_FRONTEND_IMPL", "PACKET_DESIGN_SYSTEM_FIX", "PACKET_PM", "PACKET_REPO_IMPROVEMENT",
    "DesignDiscomfort", "analyze_discomfort", "promote_to_packet",
)
