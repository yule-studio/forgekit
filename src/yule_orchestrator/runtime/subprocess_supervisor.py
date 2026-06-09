"""Compatibility shim — moved to :mod:`yule_runtime.subprocess_supervisor`.

The subprocess supervisor restart loop now lives in the ``yule-runtime``
package. This shim aliases the old import path
(``yule_orchestrator.runtime.subprocess_supervisor``) onto the new module
so all existing imports — and tests that monkeypatch supervisor module
globals — keep operating on the *same* module object.
"""

from __future__ import annotations

import sys

from yule_runtime import subprocess_supervisor as _subprocess_supervisor

sys.modules[__name__] = _subprocess_supervisor
