"""Smoke tests for the moved service-manifest primitive.

Imports are resolved *inside* each test off the live module rather than
at module top. The orchestrator suite
(tests/runtime/test_subprocess_supervisor.py) calls
``importlib.reload`` on this module to re-evaluate import-time env flags,
which rebinds ``ServiceSpec`` to a fresh class object. Pulling the names
at call time keeps ``isinstance`` checks consistent with whatever
``list_services`` currently returns.
"""

import pytest


def test_engineering_profile_is_listed():
    from yule_runtime.services import ServiceKind, ServiceSpec, list_services

    specs = list_services("engineering")
    assert specs, "engineering profile should expose at least one service"
    assert all(isinstance(s, ServiceSpec) for s in specs)
    # Every spec carries a ServiceKind.
    assert all(isinstance(s.kind, ServiceKind) for s in specs)


def test_list_services_unknown_profile_raises():
    from yule_runtime.services import list_services

    with pytest.raises(ValueError):
        list_services("does-not-exist")


def test_resolve_service_round_trips():
    from yule_runtime.services import list_services, resolve_service

    first = list_services("engineering")[0]
    resolved = resolve_service(first.service_id)
    assert resolved is not None
    assert resolved.service_id == first.service_id


def test_resolve_unknown_service_returns_none():
    from yule_runtime.services import resolve_service

    assert resolve_service("no-such-service") is None


def test_profiles_mapping_exposes_engineering():
    from yule_runtime.services import PROFILES

    assert "engineering" in PROFILES


def test_legacy_shim_is_same_module_object():
    import yule_orchestrator.runtime.services as shim
    import yule_runtime.services as real

    assert shim is real
    # Monkeypatching the shim's PROFILES affects what list_services reads
    # because they are the literal same module object.
    assert shim.PROFILES is real.PROFILES
