"""Forward-compat shim — ``help_surface`` now lives under ``agents``.

The canonical module moved to
:mod:`yule_orchestrator.agents.engineering_conversation.help_surface` as part
of breaking the ``agents → discord`` import cycle. This shim aliases the new
module under the old discord path so existing importers
(``discord/commands``, ``discord/bot/_legacy``, tests) keep working, with
object identity preserved.
"""

from __future__ import annotations

import sys

from yule_orchestrator.agents.engineering_conversation import help_surface as _m

sys.modules[__name__] = _m
