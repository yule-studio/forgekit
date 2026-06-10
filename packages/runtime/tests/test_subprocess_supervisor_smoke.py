"""Smoke tests for the moved subprocess-supervisor primitive.

The supervisor's restart loop is exercised in detail by the
orchestrator-level suite (tests/runtime/test_subprocess_supervisor.py).
Here we only assert the module imports cleanly through the new package
path, wires onto its sibling primitives, and that the legacy shim
resolves to the same object.
"""


def test_supervisor_imports_and_wires_siblings():
    from yule_runtime import subprocess_supervisor as sup
    from yule_runtime.circuit_breaker import CircuitBreakerRegistry
    from yule_runtime.services import list_services

    # The supervisor pulls these in from its sibling modules.
    assert sup.CircuitBreakerRegistry is CircuitBreakerRegistry
    assert sup.list_services is list_services


def test_legacy_shim_is_same_module_object():
    import yule_orchestrator.runtime.subprocess_supervisor as shim
    import yule_runtime.subprocess_supervisor as real

    assert shim is real
