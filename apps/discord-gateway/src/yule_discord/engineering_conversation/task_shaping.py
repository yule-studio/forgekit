"""Compat shim — ``task_shaping`` relocated to ``agents/engineering_conversation/task_shaping``.

Aliases this module to the relocated submodule so that
``from ...discord.engineering_conversation.task_shaping import X`` and
``patch("...discord.engineering_conversation.task_shaping.Y")`` resolve to the
**same** module object (identity preserved).
"""

from yule_engineering.agents.engineering_conversation import task_shaping as _m
import sys

sys.modules[__name__] = _m
