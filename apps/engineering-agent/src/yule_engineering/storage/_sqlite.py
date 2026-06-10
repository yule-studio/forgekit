"""Compatibility shim — moved to :mod:`yule_storage._sqlite`.

Aliases the old import path onto the new module so existing
``from yule_engineering.storage._sqlite import SQLITE_WRITE_LOCK`` keeps
resolving to the *same* lock object the storage package uses.
"""

from __future__ import annotations

import sys

from yule_storage import _sqlite as _module

sys.modules[__name__] = _module
