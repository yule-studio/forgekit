"""Forward-compat shim — ``research_forum`` now lives under ``agents``.

The canonical package moved to
:mod:`yule_orchestrator.agents.research.forum` as part of breaking the
``agents → discord`` import cycle. This shim aliases the new package (and
each of its submodules) under the old discord path so existing
discord-side importers and tests
(``from yule_orchestrator.discord.research_forum import X`` /
``from ..research_forum import X``) keep working with object identity
preserved.
"""

from __future__ import annotations

import sys

from yule_orchestrator.agents.research import forum as _pkg
from yule_orchestrator.agents.research.forum import (  # noqa: F401
    config as _config,
    formatters as _formatters,
    posting as _posting,
    prefixes as _prefixes,
)

# Alias each submodule under the old dotted path so
# ``import yule_orchestrator.discord.research_forum.config`` (and
# ``is``/reload/monkeypatch against it) resolve to the canonical objects.
sys.modules[__name__ + ".config"] = _config
sys.modules[__name__ + ".formatters"] = _formatters
sys.modules[__name__ + ".posting"] = _posting
sys.modules[__name__ + ".prefixes"] = _prefixes

# Replace this shim package object with the canonical package itself so
# the whole public surface (``__all__``) re-exports identically and the
# two import paths share one module object.
sys.modules[__name__] = _pkg
