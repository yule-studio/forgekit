"""Compat shim — moved to :mod:`yule_core.tls` (packages/core).

This aliases the old import path (``yule_engineering.core.tls``) onto
the new module so all existing imports — and any monkeypatching of
module globals — keep operating on the *same* module object.
"""

from __future__ import annotations

import sys

from yule_core import tls as _module

sys.modules[__name__] = _module
