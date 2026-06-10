"""Compat shim — ``response_formatters`` relocated to ``agents/engineering_conversation/response_formatters``.

Aliases this module to the relocated submodule so that
``from ...discord.engineering_conversation.response_formatters import X`` and
``patch("...discord.engineering_conversation.response_formatters.Y")`` resolve to the
**same** module object (identity preserved).
"""

from yule_orchestrator.agents.engineering_conversation import response_formatters as _m
import sys

sys.modules[__name__] = _m
