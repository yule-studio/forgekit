"""Smoke tests for the extracted ``yule_core`` package.

Verifies the public surface imports without any ``yule_orchestrator``
dependency, and that the ``yule_orchestrator.core`` compat shims alias
the *same* module objects / functions (so monkeypatch/reload callers in
the 13 external importers keep working).
"""

from __future__ import annotations

import yule_core
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


def test_public_api_is_importable() -> None:
    assert callable(load_env_files)
    assert callable(load_agent_context)
    assert callable(render_context)
    assert callable(local_tz)
    assert callable(local_tz_name)
    assert callable(now_local)
    assert callable(to_local)
    assert callable(apply_ca_bundle_fallback)
    assert callable(resolve_ca_bundle)
    assert ContextError is not None
    assert ContextDocument is not None
    assert LoadedContext is not None
    assert TLSCABundle is not None


def test_all_surface_matches_init() -> None:
    expected = {
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
    }
    assert set(yule_core.__all__) == expected
    for name in expected:
        assert hasattr(yule_core, name), name


def test_timezone_now_local_is_tz_aware() -> None:
    assert now_local().tzinfo is not None


def test_shim_module_identity() -> None:
    """The old import paths must alias the *same* module objects."""
    from yule_orchestrator import core as shim_core
    from yule_orchestrator.core import timezone as shim_timezone
    from yule_orchestrator.core import env_loader as shim_env_loader
    from yule_orchestrator.core import tls as shim_tls
    from yule_orchestrator.core import context_loader as shim_context_loader

    assert shim_timezone is yule_core.timezone
    assert shim_env_loader is yule_core.env_loader
    assert shim_tls is yule_core.tls
    assert shim_context_loader is yule_core.context_loader

    # Re-exported callables resolve to the identical objects.
    assert shim_core.now_local is yule_core.now_local
    assert shim_core.load_env_files is yule_core.load_env_files
    assert shim_core.resolve_ca_bundle is yule_core.resolve_ca_bundle
    assert shim_timezone.now_local is yule_core.now_local
