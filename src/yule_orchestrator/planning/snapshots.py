"""Compatibility shim — moved to `yule_planning.snapshots` (apps/planning-agent).

The canonical home of this module is now `yule_planning.snapshots`. This shim
aliases it under the old import path so existing
`yule_orchestrator.planning.snapshots` imports keep resolving to the same object.
New code should import from `yule_planning` directly.
"""

import sys

from yule_planning import snapshots as _module

sys.modules[__name__] = _module
