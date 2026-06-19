"""RWT1 guard — yule_runtime renamed to yule_runtime_primitives; old path is a shim.

CI-run (root ``tests/`` tree). Proves the runtime-primitives rename held:
- the canonical package is ``yule_runtime_primitives`` (disambiguates the 3x "runtime"
  naming: forgekit-runtime / agent-runtime / runtime-primitives);
- the old ``yule_runtime`` import path still resolves to the SAME module + submodule
  objects (forward-compat shim), so engineering-agent consumers keep working unchanged
  during the gradual migration.
"""

from __future__ import annotations

import unittest


class RuntimePrimitivesRenameTests(unittest.TestCase):
    def test_canonical_package_imports(self) -> None:
        import yule_runtime_primitives as C
        from yule_runtime_primitives.circuit_breaker import CircuitBreakerRegistry
        from yule_runtime_primitives.services import list_services, resolve_service
        from yule_runtime_primitives import subprocess_supervisor  # noqa: F401

        self.assertTrue(C.__name__ == "yule_runtime_primitives")
        self.assertTrue(callable(list_services) and callable(resolve_service))
        self.assertTrue(CircuitBreakerRegistry is not None)

    def test_old_path_is_a_compat_shim_with_identity(self) -> None:
        import yule_runtime
        import yule_runtime_primitives as C

        # submodule object identity preserved across both paths
        self.assertIs(yule_runtime.circuit_breaker, C.circuit_breaker)
        self.assertIs(yule_runtime.services, C.services)
        self.assertIs(yule_runtime.subprocess_supervisor, C.subprocess_supervisor)

    def test_all_import_styles_resolve_via_shim(self) -> None:
        from yule_runtime.circuit_breaker import CircuitBreakerRegistry  # from X.Y import Z
        from yule_runtime.services import list_services
        from yule_runtime import subprocess_supervisor as sup  # from X import Y
        import yule_runtime.services as svc  # import X.Y as Z
        import yule_runtime_primitives as C

        self.assertIs(svc, C.services)
        self.assertIs(sup, C.subprocess_supervisor)
        self.assertTrue(callable(list_services))
        self.assertTrue(CircuitBreakerRegistry is C.circuit_breaker.CircuitBreakerRegistry)


if __name__ == "__main__":
    unittest.main()
