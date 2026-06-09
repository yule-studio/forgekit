"""Compat shim — ``_legacy`` submodule relocated to ``agents/``.

Aliases this module name to the relocated
``yule_orchestrator.agents.engineering_team_runtime._legacy`` so that
``from ...discord.engineering_team_runtime._legacy import X`` and any
``patch("...discord.engineering_team_runtime._legacy.Y")`` keep resolving
to the **same** module object (identity preserved).
"""

from yule_orchestrator.agents.engineering_team_runtime import _legacy as _m
import sys

sys.modules[__name__] = _m
