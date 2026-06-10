"""Compatibility shim — moved to :mod:`yule_integrations.calendar.naver_caldav`.

Aliases the old import path onto the new module so existing imports and
test patches against ``yule_engineering.integrations.calendar.naver_caldav``
operate on the *same* module object the integrations package uses.
"""

from __future__ import annotations

import sys

from yule_integrations.calendar import naver_caldav as _module

sys.modules[__name__] = _module
