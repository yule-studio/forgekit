"""Hephaistos — ForgeKit's skill-forging core.

Reads the request, forges an equip plan (agent + skills + loadout + weapons + Nexus
source refs + Work Packet draft) from the Armory, and verifies the local loadout. Pure
core; the console is a projection layer. Nexus is a planned read seam (not_connected).

Owner: ``packages/hephaistos`` (ForgeKit core, WT3) — moved out of
``forgekit_console.hephaistos`` (now a compat shim). A standalone ForgeKit pillar, not a
console module and not a single slash command. ``armory`` lives here as ``hephaistos.armory``
for now; promoting it to ``packages/armory`` needs ``models`` split (armory types vs
forge-output types) to avoid a cycle — a deferred follow-up. Only outward dep:
``forgekit-config`` (paths). Owner matrix: ``docs/forgekit-architecture-ownership.md``.
"""

from .execution import EquipStatus, ExecutionPlan, forge_execution_plan
from .ponytail import PonytailVerdict, ponytail_review
from .resolver import explain_lines, resolve
from .verifier import readiness_lines, verify_loadout

__all__ = ("resolve", "explain_lines", "verify_loadout", "readiness_lines",
           "forge_execution_plan", "ExecutionPlan", "EquipStatus",
           "ponytail_review", "PonytailVerdict")
