"""Compatibility shim — platform core utils now live in ``yule_core``.

Environment loading, timezone helpers, TLS/CA-bundle fallback, and agent
context loading were extracted into the standalone ``yule-core`` package
(a pure stdlib leaf with no ``yule_orchestrator`` imports). This module
re-exports the same public names so every existing
``from yule_orchestrator.core import ...`` keeps resolving to the
identical objects. The submodule shims
(``core.{context_loader,env_loader,timezone,tls}``) alias the moved
modules via ``sys.modules`` so monkeypatch/reload tests stay valid.
"""

from yule_core import (
    ContextDocument,
    ContextError,
    LoadedContext,
    TLSCABundle,
    apply_ca_bundle_fallback,
    load_agent_context,
    load_env_files,
    local_tz,
    local_tz_name,
    now_local,
    render_context,
    resolve_ca_bundle,
    to_local,
)


__all__ = [
    "ContextError",
    "ContextDocument",
    "LoadedContext",
    "TLSCABundle",
    "apply_ca_bundle_fallback",
    "load_agent_context",
    "load_env_files",
    "local_tz",
    "local_tz_name",
    "now_local",
    "resolve_ca_bundle",
    "render_context",
    "to_local",
]
