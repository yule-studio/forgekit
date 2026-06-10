"""Compatibility shim — moved to `yule_planning.github_label_policy` (apps/planning-agent).

The canonical home of this module is now `yule_planning.github_label_policy`. This shim
aliases it under the old import path so existing
`yule_engineering.planning.github_label_policy` imports keep resolving to the same object.
New code should import from `yule_planning` directly.
"""

import sys

from yule_planning import github_label_policy as _module

sys.modules[__name__] = _module
