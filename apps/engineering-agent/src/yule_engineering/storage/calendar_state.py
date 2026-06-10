"""Compatibility shim — moved to :mod:`yule_storage.calendar_state`.

Aliases the old import path onto the new module so existing imports and
test patches against ``yule_engineering.storage.calendar_state`` operate
on the *same* module object the storage package uses.
"""

from __future__ import annotations

import sys

from yule_storage import calendar_state as _module

sys.modules[__name__] = _module
