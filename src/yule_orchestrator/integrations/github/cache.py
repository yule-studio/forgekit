"""Compatibility shim — moved to :mod:`yule_integrations.github.cache`.

Aliases the old import path onto the new module so existing imports and
test patches against ``yule_orchestrator.integrations.github.cache``
operate on the *same* module object the integrations package uses.
"""

from __future__ import annotations

import sys

from yule_integrations.github import cache as _module

sys.modules[__name__] = _module
