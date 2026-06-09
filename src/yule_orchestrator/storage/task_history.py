"""Compatibility shim — moved to :mod:`yule_storage.task_history`.

Aliases the old import path onto the new module so existing imports and
test patches against ``yule_orchestrator.storage.task_history`` operate
on the *same* module object the storage package uses.
"""

from __future__ import annotations

import sys

from yule_storage import task_history as _module

sys.modules[__name__] = _module
