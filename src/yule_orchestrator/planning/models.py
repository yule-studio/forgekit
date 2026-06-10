"""Compatibility shim — moved to `yule_planning.models` (apps/planning-agent).

The canonical home of this module is now `yule_planning.models`. This shim
aliases it under the old import path so existing
`yule_orchestrator.planning.models` imports keep resolving to the same object.
New code should import from `yule_planning` directly.
"""

import sys

from yule_planning import models as _module

sys.modules[__name__] = _module
