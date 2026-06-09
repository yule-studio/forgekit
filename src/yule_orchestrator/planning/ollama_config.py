"""Compatibility shim — moved to `yule_planning.ollama_config` (apps/planning-agent).

The canonical home of this module is now `yule_planning.ollama_config`. This shim
aliases it under the old import path so existing
`yule_orchestrator.planning.ollama_config` imports keep resolving to the same object.
New code should import from `yule_planning` directly.
"""

import sys

from yule_planning import ollama_config as _module

sys.modules[__name__] = _module
