"""Design (WT1+) — restricted design source gate + design role split + packets.

Raw design assets (the desktop Figma backup) are a restricted, read-only source that
ONLY design-family roles may access; everyone else uses projections/packets. Access is
honest about macOS TCC blocking — never a fake read, raw never copied into repo/vault.
"""

from __future__ import annotations

from .discomfort import (
    DesignDiscomfort,
    analyze_discomfort,
    promote_to_packet,
)
from .packet import (
    DesignReferencePacket,
    build_reference_packet,
    project_for,
)
from .source import (
    ACCESS_BLOCKED,
    ACCESS_MISSING,
    ACCESS_OK,
    DESIGN_ROLES,
    RAW_DESIGN_BACKUP_PATH,
    RestrictedDesignSource,
    access_request,
    access_runbook,
    probe_access,
    register_design_backup,
)

__all__ = (
    "ACCESS_OK", "ACCESS_BLOCKED", "ACCESS_MISSING", "DESIGN_ROLES",
    "RAW_DESIGN_BACKUP_PATH", "RestrictedDesignSource",
    "access_request", "access_runbook", "probe_access", "register_design_backup",
    "DesignReferencePacket", "build_reference_packet", "project_for",
    "DesignDiscomfort", "analyze_discomfort", "promote_to_packet",
)
