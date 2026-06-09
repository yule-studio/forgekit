"""Compatibility shim — moved to :mod:`yule_integrations.github.pulls`.

Aliases the old import path onto the new module so existing imports and
test patches against ``yule_orchestrator.integrations.github.pulls``
operate on the *same* module object the integrations package uses.
"""

from __future__ import annotations

import sys

from yule_integrations.github import pulls as _module

sys.modules[__name__] = _module
