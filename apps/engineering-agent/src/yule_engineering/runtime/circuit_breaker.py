"""Compatibility shim — moved to :mod:`yule_runtime.circuit_breaker`.

The circuit breaker primitive now lives in the ``yule-runtime`` package.
This shim aliases the old import path
(``yule_engineering.runtime.circuit_breaker``) onto the new module so
all existing imports — and any monkeypatching of module globals — keep
operating on the *same* module object.
"""

from __future__ import annotations

import sys

from yule_runtime import circuit_breaker as _circuit_breaker

sys.modules[__name__] = _circuit_breaker
