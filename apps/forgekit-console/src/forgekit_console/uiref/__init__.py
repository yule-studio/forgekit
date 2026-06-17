"""UI reference seam (WT5) — Figma/reference-aware UX improvement, honest connection state."""

from __future__ import annotations

from .reference import (
    FIGMA_NOT_CONNECTED,
    REFERENCE_MISSING,
    STATE_LIVE,
    STATE_MISSING,
    STATE_SCAFFOLD,
    UIReference,
    figma_connect_runbook,
    figma_reference,
    operator_note_reference,
    ui_discomfort_to_packet,
)

__all__ = (
    "FIGMA_NOT_CONNECTED", "REFERENCE_MISSING",
    "STATE_LIVE", "STATE_SCAFFOLD", "STATE_MISSING",
    "UIReference", "figma_reference", "operator_note_reference",
    "figma_connect_runbook", "ui_discomfort_to_packet",
)
