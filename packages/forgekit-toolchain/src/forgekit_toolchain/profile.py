"""loadout → toolchain profile, and detected-requirements → profile.

A Hephaistos *loadout* already declares what env it assumes ("local JDK 21",
"python 3.13", "node LTS") and which weapons it needs (openjdk/gradle/node…). This
turns that into a concrete :class:`ToolchainProfile` of pinned tool versions, so the
same loadout that resolves skills/weapons also fixes the language runtime versions.

Pure — given a loadout it produces a profile; it does not touch the env.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .models import ToolRequirement, ToolchainProfile, SRC_LOADOUT

# weapon id (armory) → mise-style runtime tool name. Only runtimes mise can manage;
# infra weapons (docker/mysql/redis) are NOT toolchain runtimes and are skipped here.
_WEAPON_TO_TOOL: Dict[str, str] = {
    "openjdk": "java", "java": "java",
    "python": "python", "uv": "uv",
    "node": "node", "npm": "node",          # npm ships with node — pin node only
    "go": "go", "ruby": "ruby",
    "terraform": "terraform", "kubectl": "kubectl",
    "gradle": "gradle",
}

# runtimes we pin a version for (others present-only). mise resolves the actual build.
_VERSIONED = frozenset({"java", "python", "node", "go", "ruby", "terraform"})

# extract a version from an environment assumption phrase. "LTS"/"latest" stay symbolic
# (honest — we pass mise's own alias through rather than inventing a number).
_VER_RE = re.compile(r'(\d+(?:\.\d+){0,2})')
_ALIAS_RE = re.compile(r'\b(lts|latest|stable)\b', re.IGNORECASE)


def _version_for(tool: str, assumptions: Tuple[str, ...]) -> str:
    keys = {
        "java": ("jdk", "java"), "python": ("python", "py"),
        "node": ("node",), "go": ("go", "golang"),
        "ruby": ("ruby",), "terraform": ("terraform",),
    }.get(tool, (tool,))
    for phrase in assumptions:
        low = phrase.lower()
        if not any(k in low for k in keys):
            continue
        m = _VER_RE.search(phrase)
        if m:
            return m.group(1)
        a = _ALIAS_RE.search(phrase)
        if a:
            return a.group(1).lower()
    return ""


def profile_for_loadout(loadout_id: str) -> Optional[ToolchainProfile]:
    """Build a toolchain profile from a loadout's weapons + env assumptions.

    Returns ``None`` for an unknown loadout (honest — no fabricated profile).
    """

    from armory import catalog  # lazy: armory is a sibling package

    lo = catalog.loadout(loadout_id) if hasattr(catalog, "loadout") else None
    if lo is None:
        return None
    assumptions = tuple(getattr(lo, "environment_assumptions", ()) or ())
    seen: Dict[str, ToolRequirement] = {}
    for wid in tuple(getattr(lo, "required_weapons", ()) or ()):
        tool = _WEAPON_TO_TOOL.get(wid)
        if not tool or tool in seen:
            continue
        ver = _version_for(tool, assumptions) if tool in _VERSIONED else ""
        seen[tool] = ToolRequirement(tool, ver, SRC_LOADOUT, source_file=f"loadout:{loadout_id}",
                                     raw=wid)
    return ToolchainProfile(
        name=loadout_id, origin=f"loadout:{loadout_id}", tools=tuple(seen.values()),
        notes=getattr(lo, "notes", "") or "")


def profile_from_requirements(name: str, reqs: List[ToolRequirement], *, origin: str = ""
                              ) -> ToolchainProfile:
    """Wrap detected repo-local requirements as a profile (detection is the origin)."""

    return ToolchainProfile(name=name, origin=origin or f"detected:{name}", tools=tuple(reqs))


def merge_profiles(detected: ToolchainProfile, loadout: Optional[ToolchainProfile]
                   ) -> ToolchainProfile:
    """Repo-local detection wins; loadout fills tools the repo didn't pin (recommend)."""

    if loadout is None:
        return detected
    have = {t.tool for t in detected.tools}
    extra = tuple(t for t in loadout.tools if t.tool not in have)
    return ToolchainProfile(
        name=detected.name or loadout.name,
        origin=f"{detected.origin}+{loadout.origin}",
        tools=tuple(detected.tools) + extra,
        notes=loadout.notes)


__all__ = ("profile_for_loadout", "profile_from_requirements", "merge_profiles")
