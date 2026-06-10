"""Runtime entrypoints for the always-on engineering orchestrator.

A-M6.0 wiring: ``runtime.services`` defines the service inventory,
``runtime.run_service`` is the single-worker entrypoint that both
``yule run-service`` (CLI) and systemd unit use, and
``runtime.subprocess_supervisor`` is the parent process that
``yule runtime up`` runs in dev / single-host environments.
"""

from yule_runtime.services import (
    ENGINEERING_PROFILE,
    PROFILES,
    ServiceKind,
    ServiceSpec,
    list_services,
    resolve_service,
)


__all__ = (
    "ENGINEERING_PROFILE",
    "PROFILES",
    "ServiceKind",
    "ServiceSpec",
    "list_services",
    "resolve_service",
)
