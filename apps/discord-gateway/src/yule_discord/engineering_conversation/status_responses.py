"""Compat shim — ``status_responses`` relocated to ``agents/engineering_conversation/status_responses``.

Aliases this module to the relocated submodule so that
``from ...discord.engineering_conversation.status_responses import X`` and
``patch("...discord.engineering_conversation.status_responses.Y")`` resolve to the
**same** module object (identity preserved).
"""

from yule_orchestrator.agents.engineering_conversation import status_responses as _m
import sys

sys.modules[__name__] = _m
