"""Forward-compat shim — ``runtime`` now lives in ``packages/forgekit-runtime`` (WT2).

Canonical: :mod:`forgekit_runtime.runtime`. Aliases the old dotted path (and submodules) to
the canonical package via ``sys.modules`` (object identity preserved). New code imports
:mod:`forgekit_runtime.runtime` directly. Owner matrix: ``docs/forgekit-architecture-ownership.md``.
"""

from __future__ import annotations

from forgekit_console import _compat
from forgekit_runtime import runtime as _canon

_compat.alias_package(__name__, _canon)

# Wire the operator app's intake→packet bridge into the runtime core's handoff seam.
# This keeps forgekit-runtime free of any app import (packages → apps hard rail): the
# app injects its adapter. Best-effort: if handoff is unavailable the loop raises a
# clear error only when it actually tries to packetize.
try:  # pragma: no cover - thin wiring
    from forgekit_console.handoff import run_handoff as _run_handoff
    _canon.loop.register_handoff_runner(_run_handoff)
except Exception:  # noqa: BLE001
    pass
