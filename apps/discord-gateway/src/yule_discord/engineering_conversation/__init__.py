"""Compat shim — ``engineering_conversation`` relocated to ``agents/``.

The engineering-agent free-form conversation layer moved from
``discord/engineering_conversation`` to
``yule_orchestrator.agents.engineering_conversation`` to break an
artificial ``agents ↔ discord`` import cycle (the module contained only
agents task-shaping / intent / research-bootstrap logic, no discord
transport).

This package facade re-exports the relocated public API. Submodule access
(``...discord.engineering_conversation.<sub>``) is served by per-module
shim files in this directory that ``sys.modules``-alias to the relocated
submodule, preserving identity for ``patch`` / ``is`` checks.
``discord → agents`` is the forward/legal direction; the cycle is broken
because no ``agents/`` file imports this discord path anymore.
"""

from __future__ import annotations

from yule_orchestrator.agents.engineering_conversation import *  # noqa: F401,F403
from yule_orchestrator.agents.engineering_conversation import (  # noqa: F401
    __all__,
    # Explicitly re-exported by the original facade though not in __all__.
    _suggest_task_type,
)
