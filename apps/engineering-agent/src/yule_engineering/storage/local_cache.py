"""Compatibility shim — moved to :mod:`yule_storage.local_cache`.

Aliases the old import path onto the new module so existing imports and
test patches against ``yule_engineering.storage.local_cache`` (e.g.
``_reset_cleanup_schedule_for_tests``) operate on the *same* module
object the storage package uses.
"""

from __future__ import annotations

import sys

from yule_storage import local_cache as _module

sys.modules[__name__] = _module
