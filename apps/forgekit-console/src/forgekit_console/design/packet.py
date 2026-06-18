"""DesignReferencePacket + role projections (design WT3).

A design specialist reads the restricted raw source and compresses it into a
:class:`DesignReferencePacket`. Other roles NEVER read the raw asset — they read a
role-specific PROJECTION (a subset). The raw asset path is metadata only; raw
``.fig``/exports are never embedded in the packet or vault. When the source is
blocked (TCC), the packet is honest scaffolding (``access_state=blocked`` + empty
fields), never fabricated design data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

SOURCE_FIGMA = "figma"
SOURCE_FIG_EXPORT = "fig-export"
SOURCE_SCREENSHOT = "screenshot"
SOURCE_BACKUP_ASSET = "backup-asset"

SENSITIVITY_RESTRICTED = "restricted"


@dataclass(frozen=True)
class DesignReferencePacket:
    design_source_id: str
    source_type: str = SOURCE_BACKUP_ASSET
    visibility: str = SENSITIVITY_RESTRICTED
    raw_source_path: str = ""        # METADATA only — raw content never embedded
    access_state: str = "blocked"
    screen_list: Tuple[str, ...] = ()
    layout_rules: Tuple[str, ...] = ()
    component_inventory: Tuple[str, ...] = ()
    spacing_scale: Tuple[str, ...] = ()
    typography_rules: Tuple[str, ...] = ()
    color_tokens: Tuple[str, ...] = ()
    interaction_notes: Tuple[str, ...] = ()
    ux_risks: Tuple[str, ...] = ()
    do_not_change: Tuple[str, ...] = ()
    implementation_notes: Tuple[str, ...] = ()
    open_questions: Tuple[str, ...] = ()
    publishable: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "design_source_id": self.design_source_id, "source_type": self.source_type,
            "visibility": self.visibility, "raw_source_path": self.raw_source_path,
            "access_state": self.access_state,
            "screen_list": list(self.screen_list), "layout_rules": list(self.layout_rules),
            "component_inventory": list(self.component_inventory),
            "spacing_scale": list(self.spacing_scale),
            "typography_rules": list(self.typography_rules),
            "color_tokens": list(self.color_tokens),
            "interaction_notes": list(self.interaction_notes),
            "ux_risks": list(self.ux_risks), "do_not_change": list(self.do_not_change),
            "implementation_notes": list(self.implementation_notes),
            "open_questions": list(self.open_questions),
            "publishable": self.publishable, "note": self.note,
        }


def build_reference_packet(source) -> DesignReferencePacket:
    """Build a packet from a RestrictedDesignSource. Blocked → honest empty scaffold."""

    blocked = getattr(source, "access_state", "blocked") != "ok"
    note = ("design_source_blocked — raw 접근 불가(TCC). design role 이 export 후 채워야 함 "
            "(fake 디자인 데이터 없음)." if blocked else "design role 이 raw source 를 압축함")
    return DesignReferencePacket(
        design_source_id=getattr(source, "source_id", "figma-backup"),
        raw_source_path=getattr(source, "source_path", ""),
        access_state=getattr(source, "access_state", "blocked"),
        open_questions=("raw 접근 허용 또는 export 제공 필요",) if blocked else (),
        note=note,
    )


# --- role projections — subsets, never the raw source -----------------------
@dataclass(frozen=True)
class _Projection:
    role: str
    fields: dict

    def to_dict(self) -> dict:
        return {"role": self.role, **self.fields}


def _proj(role: str, packet: DesignReferencePacket, keys: Tuple[str, ...]) -> _Projection:
    d = packet.to_dict()
    fields = {k: d.get(k) for k in keys}
    fields["access_state"] = packet.access_state   # always carry honesty
    return _Projection(role, fields)


def ux_projection(p: DesignReferencePacket) -> _Projection:
    return _proj("ux-ui-designer", p, ("screen_list", "layout_rules", "spacing_scale",
                                       "interaction_notes", "ux_risks"))


def design_system_projection(p: DesignReferencePacket) -> _Projection:
    return _proj("design-systems-designer", p, ("component_inventory", "spacing_scale",
                                                "typography_rules", "color_tokens"))


def illustration_projection(p: DesignReferencePacket) -> _Projection:
    return _proj("illustration-brand-designer", p, ("color_tokens", "do_not_change"))


def frontend_projection(p: DesignReferencePacket) -> _Projection:
    return _proj("fe", p, ("component_inventory", "spacing_scale", "typography_rules",
                           "color_tokens", "implementation_notes", "do_not_change"))


def pm_projection(p: DesignReferencePacket) -> _Projection:
    return _proj("pm", p, ("screen_list", "ux_risks", "open_questions"))


def qa_projection(p: DesignReferencePacket) -> _Projection:
    return _proj("qa", p, ("screen_list", "interaction_notes", "do_not_change"))


_PROJECTORS = {
    "ux-ui-designer": ux_projection, "design-systems-designer": design_system_projection,
    "illustration-brand-designer": illustration_projection,
    "fe": frontend_projection, "pm": pm_projection, "qa": qa_projection,
}


def project_for(role: str, p: DesignReferencePacket):
    """Return the role's projection (NEVER the raw source). Unknown role → minimal."""

    fn = _PROJECTORS.get((role or "").strip())
    return fn(p) if fn else _proj(role or "?", p, ("screen_list",))


__all__ = (
    "SOURCE_FIGMA", "SOURCE_FIG_EXPORT", "SOURCE_SCREENSHOT", "SOURCE_BACKUP_ASSET",
    "SENSITIVITY_RESTRICTED", "DesignReferencePacket", "build_reference_packet",
    "ux_projection", "design_system_projection", "illustration_projection",
    "frontend_projection", "pm_projection", "qa_projection", "project_for",
)
