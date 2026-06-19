"""Forward-compat shim — ``yule_runtime`` was renamed to ``yule_runtime_primitives``.

The package moved to disambiguate the 3x "runtime" naming (forgekit-runtime /
agent-runtime / runtime-primitives — see docs/package-topology.md). This shim aliases
the old dotted path (and every submodule) to the canonical package via ``sys.modules``,
preserving object identity so existing importers
(``from yule_runtime.circuit_breaker import X`` / ``import yule_runtime.services``) keep
working. New code should import ``yule_runtime_primitives`` directly.
"""

from __future__ import annotations

import sys

import yule_runtime_primitives as _canon
from yule_runtime_primitives import (  # noqa: F401  (re-export top-level surface)
    CircuitBreakerPolicy,
    CircuitBreakerRegistry,
    CircuitSnapshot,
    ENGINEERING_PROFILE,
    PROFILES,
    ServiceKind,
    ServiceSpec,
    list_services,
    resolve_service,
)
from yule_runtime_primitives import circuit_breaker, services, subprocess_supervisor

# alias each submodule under the old dotted path (object identity preserved) so that
# ``import yule_runtime.services``, ``from yule_runtime import services`` and bare
# ``yule_runtime.services`` attribute access all resolve to the canonical module.
sys.modules[__name__ + ".circuit_breaker"] = circuit_breaker
sys.modules[__name__ + ".services"] = services
sys.modules[__name__ + ".subprocess_supervisor"] = subprocess_supervisor

__all__ = _canon.__all__
