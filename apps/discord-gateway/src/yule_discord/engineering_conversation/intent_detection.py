"""Compat shim — ``intent_detection`` relocated to ``agents/engineering_conversation/intent_detection``.

Aliases this module to the relocated submodule so that
``from ...discord.engineering_conversation.intent_detection import X`` and
``patch("...discord.engineering_conversation.intent_detection.Y")`` resolve to the
**same** module object (identity preserved).
"""

from yule_orchestrator.agents.engineering_conversation import intent_detection as _m
import sys

sys.modules[__name__] = _m
