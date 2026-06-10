"""yule_runtime — cleanly-movable runtime primitives.

This package holds the low-coupling runtime PRIMITIVES extracted from
``yule_engineering.runtime``: the circuit breaker, the service manifest
inventory, and the subprocess supervisor restart loop.

Dependency rule (enforced by review):
``yule_runtime`` must NOT import specific agent internals
(``yule_engineering.agents.*``), discord internals
(``yule_engineering.discord.*``), or memory internals
(``yule_engineering.memory.*``). It depends only on the stdlib and on
its own sibling modules. Modules that violate this rule stay in
``yule_engineering.runtime`` (see README TODO list).
"""

from .circuit_breaker import (
    CircuitBreakerPolicy,
    CircuitBreakerRegistry,
    CircuitSnapshot,
)
from .services import (
    ENGINEERING_PROFILE,
    PROFILES,
    ServiceKind,
    ServiceSpec,
    list_services,
    resolve_service,
)

__all__ = (
    "CircuitBreakerPolicy",
    "CircuitBreakerRegistry",
    "CircuitSnapshot",
    "ENGINEERING_PROFILE",
    "PROFILES",
    "ServiceKind",
    "ServiceSpec",
    "list_services",
    "resolve_service",
)
