"""UI reference seam (repo-autopilot WT5) — Figma/reference-aware, honest about state.

UI improvement can use a design reference, but the connection state is ALWAYS honest:

* ``live``    — a live Figma/MCP reference (frames/tokens/components) — used if present.
* ``scaffold``— an operator-provided screenshot / exported asset / design note (low-cost).
* ``missing`` — nothing connected → ``figma_not_connected`` / ``reference_missing``; we do
  NOT read a ``.fig`` as text or fabricate a design read. A runbook says how to connect.

``ui_discomfort_to_packet`` produces a reference-aware improvement packet: it compares
against the reference when one exists, else records the discomfort + the missing-reference
state. Pure → testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

STATE_LIVE = "live"
STATE_SCAFFOLD = "scaffold"
STATE_MISSING = "missing"

# honest sub-states surfaced to the operator
FIGMA_NOT_CONNECTED = "figma_not_connected"
REFERENCE_MISSING = "reference_missing"


@dataclass(frozen=True)
class UIReference:
    """A design reference + its honest connection state. No fake-read of .fig."""

    state: str = STATE_MISSING
    kind: str = REFERENCE_MISSING       # figma-live / screenshot / operator-note / …
    detail: str = ""
    frames: Tuple[str, ...] = ()        # only populated for a LIVE reference

    @property
    def usable(self) -> bool:
        return self.state in (STATE_LIVE, STATE_SCAFFOLD)

    def to_dict(self) -> dict:
        return {"state": self.state, "kind": self.kind, "detail": self.detail,
                "frames": list(self.frames)}


def figma_reference(*, connected: bool = False, frames: Tuple[str, ...] = ()) -> UIReference:
    """A Figma reference. In this stage live MCP/API is NOT wired → not-connected (honest)."""

    if connected and frames:
        return UIReference(STATE_LIVE, "figma-live", "live Figma 연결", frames)
    return UIReference(STATE_MISSING, FIGMA_NOT_CONNECTED,
                       "Figma MCP/API 미연결 — 연결 시 frame/token/component 비교 강화")


def operator_note_reference(note: str) -> UIReference:
    """A low-cost operator-provided design note / screenshot description (scaffold)."""

    return UIReference(STATE_SCAFFOLD, "operator-note", note[:120] if note else "operator note")


def figma_connect_runbook() -> str:
    return (
        "# Runbook — Figma reference 연결\n\n"
        "- Figma MCP server 또는 REST 토큰을 operator 가 설정(자격은 secret manager).\n"
        "- 연결되면 frame/page/component hierarchy · tokens · code-connect 를 읽어 비교.\n"
        "- 미연결 시 forgekit 는 .fig 를 텍스트로 읽는 척하지 않음 — reference_missing 로 표기.\n"
        "- 저비용 대안: screenshot/exported asset/operator design note(scaffold).\n"
    )


def ui_discomfort_to_packet(discomfort: str, reference: Optional[UIReference] = None):
    """A reference-aware UI improvement packet (compares if usable, else honest missing)."""

    from ..selfimprove import make_packet

    ref = reference or figma_reference()
    if ref.usable:
        why = f"design reference({ref.kind}) 대비 불일치/마찰"
        change = "reference 와 비교해 spacing/layout/token 보정"
    else:
        why = f"UI 마찰 — {ref.kind}({ref.detail})"
        change = "reference 연결 후 비교 권장 (지금은 휴리스틱 보정 제안)"
    return make_packet(discomfort, why=why, area="ui", change=change,
                       owner="fe", origin="ui-reference", discomfort=discomfort)


__all__ = (
    "STATE_LIVE", "STATE_SCAFFOLD", "STATE_MISSING",
    "FIGMA_NOT_CONNECTED", "REFERENCE_MISSING",
    "UIReference", "figma_reference", "operator_note_reference",
    "figma_connect_runbook", "ui_discomfort_to_packet",
)
