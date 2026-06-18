"""Hephaistos — ForgeKit's skill-forging core.

Reads the request, forges an equip plan (agent + skills + loadout + weapons + Nexus
source refs + Work Packet draft) from the Armory, and verifies the local loadout. Pure
core; the console is a projection layer. Nexus is a planned read seam (not_connected).
"""

from .resolver import explain_lines, resolve
from .verifier import readiness_lines, verify_loadout

__all__ = ("resolve", "explain_lines", "verify_loadout", "readiness_lines")
