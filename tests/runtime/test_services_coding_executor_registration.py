"""runtime.services — coding_executor registration (#73 Phase 4)."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.runtime.services import (
    ENGINEERING_PROFILE,
    ServiceKind,
    ServiceSpec,
    list_services,
    resolve_service,
)


class CodingExecutorRegistrationTests(unittest.TestCase):
    def test_kind_enum_includes_coding_executor(self) -> None:
        self.assertEqual(ServiceKind.CODING_EXECUTOR.value, "coding_executor")

    def test_engineering_profile_has_coding_executor_spec(self) -> None:
        services = list_services("engineering")
        coding_specs = [s for s in services if s.kind == ServiceKind.CODING_EXECUTOR]
        self.assertEqual(len(coding_specs), 1)
        spec = coding_specs[0]
        self.assertEqual(spec.service_id, "eng-coding-executor")
        # Default OFF — opt-in required for live executor wiring.
        self.assertFalse(spec.auto_spawn)
        self.assertTrue(spec.is_implemented())  # not RESERVED_*

    def test_resolve_by_service_id(self) -> None:
        spec = resolve_service("eng-coding-executor")
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(spec.kind, ServiceKind.CODING_EXECUTOR)

    def test_other_specs_default_to_auto_spawn_true(self) -> None:
        # Ensure adding `auto_spawn` did not silently flip existing services.
        for spec in ENGINEERING_PROFILE:
            if spec.kind == ServiceKind.CODING_EXECUTOR:
                continue
            self.assertTrue(
                spec.auto_spawn,
                f"{spec.service_id} unexpectedly auto_spawn=False",
            )

    def test_engineering_profile_size_grew_by_one(self) -> None:
        # 12 → 13 (added eng-coding-executor). If this drifts the spec
        # changelog needs an update.
        self.assertEqual(len(ENGINEERING_PROFILE), 13)


if __name__ == "__main__":
    unittest.main()
