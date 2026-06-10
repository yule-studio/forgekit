"""Compatibility shim — moved to :mod:`yule_runtime.services`.

The service-manifest inventory now lives in the ``yule-runtime`` package.
This shim aliases the old import path
(``yule_engineering.runtime.services``) onto the new module so all
existing imports — and tests that monkeypatch ``services.PROFILES`` — keep
operating on the *same* module object that ``list_services`` /
``resolve_service`` read from.
"""

from __future__ import annotations

import sys

from yule_runtime import services as _services

sys.modules[__name__] = _services
