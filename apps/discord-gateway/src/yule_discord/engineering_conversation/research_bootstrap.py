"""Compat shim — ``research_bootstrap`` relocated to ``agents/engineering_conversation/research_bootstrap``.

Aliases this module to the relocated submodule so that
``from ...discord.engineering_conversation.research_bootstrap import X`` and
``patch("...discord.engineering_conversation.research_bootstrap.Y")`` resolve to the
**same** module object (identity preserved).
"""

from yule_engineering.agents.engineering_conversation import research_bootstrap as _m
import sys

sys.modules[__name__] = _m
