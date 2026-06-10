"""Compatibility shim — moved to `yule_planning.day_profile` (apps/planning-agent).

The canonical home of this module is now `yule_planning.day_profile`. This shim
aliases it under the old import path so existing
`yule_engineering.planning.day_profile` imports keep resolving to the same object.
New code should import from `yule_planning` directly.
"""

import sys

from yule_planning import day_profile as _module

sys.modules[__name__] = _module
