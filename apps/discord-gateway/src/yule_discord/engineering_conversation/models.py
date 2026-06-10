"""Compat shim — ``models`` relocated to ``agents/engineering_conversation/models``.

Aliases this module to the relocated submodule so that
``from ...discord.engineering_conversation.models import X`` and
``patch("...discord.engineering_conversation.models.Y")`` resolve to the
**same** module object (identity preserved).
"""

from yule_orchestrator.agents.engineering_conversation import models as _m
import sys

sys.modules[__name__] = _m
