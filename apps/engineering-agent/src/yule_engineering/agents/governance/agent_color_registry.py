"""Per-agent color + vault-lane registry — human-facing visual identity.

One shared Obsidian vault, but every department/role gets a stable **color token**
and a **write namespace (lane)** so a human can tell at a glance who wrote a note.
Colors are deterministic: each department owns a base hue, each role a shade
variation of it — so the family reads consistently and every role is still
distinct. Retrieval never keys on color (that's :mod:`agent_contract_registry` /
metadata); the token is a human signal only.
"""

from __future__ import annotations

import colorsys
from typing import Mapping, Tuple

# department id → (vault lane prefix, base hue 0..360, short code)
DEPARTMENTS: Mapping[str, Tuple[str, float, str]] = {
    "engineering-agent": ("30-engineering", 212.0, "eng"),
    "product-agent": ("20-product", 168.0, "prod"),
    "planning-agent": ("50-planning", 40.0, "plan"),
    "marketing-agent": ("60-marketing", 322.0, "mkt"),
    "hr-agent": ("70-people", 276.0, "hr"),
    "finance-agent": ("80-finance", 48.0, "fin"),
    "sales-cs-agent": ("90-revenue", 24.0, "rev"),
    "legal-agent": ("95-legal", 198.0, "legal"),
    # cross-cutting ops lane (ops-observer lives in engineering dept but writes here)
    "ops": ("40-ops", 0.0, "ops"),
}

# Roles whose notes live in the cross-cutting ops lane regardless of home dept.
_OPS_LANE_ROLES = frozenset({"ops-observer"})


def _hex(h_deg: float, s: float, light: float) -> str:
    r, g, b = colorsys.hls_to_rgb((h_deg % 360) / 360.0, light, s)
    return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))


def color_for(department: str, role: str, *, role_index: int = 0) -> Tuple[str, str]:
    """Return ``(color_token, hex)`` for a role.

    The token is ``<dept_short>-<role>``; the hex is the department hue with a
    lightness/saturation shade picked by *role_index* so sibling roles differ
    while sharing the family. Unknown department → a neutral grey token.
    """

    meta = DEPARTMENTS.get(department)
    if meta is None:
        return (f"misc-{role}", "#8a8a8a")
    _lane, hue, short = meta
    # Monotonic shade ramp (no wrap) so siblings never collide: lightness rises,
    # saturation falls, hue jitters slightly — same family, distinct per role.
    light = min(0.76, 0.40 + role_index * 0.035)
    sat = max(0.34, 0.70 - role_index * 0.02)
    hue_jitter = (hue + role_index * 3.0)
    return (f"{short}-{role}", _hex(hue_jitter, sat, light))


def lane_for(department: str, role: str) -> str:
    """Vault write namespace for a role: ``<lane-prefix>/<role>``."""

    if role in _OPS_LANE_ROLES:
        prefix = DEPARTMENTS["ops"][0]
    else:
        prefix = DEPARTMENTS.get(department, ("99-misc", 0.0, "misc"))[0]
    return f"{prefix}/{role}"


__all__ = ("DEPARTMENTS", "color_for", "lane_for")
