"""service inventory — A-M6.0 unit tests.

Pin the engineering profile shape so adding/removing a service is
visible in code review (the inventory list is small but
load-bearing — typo in service_id breaks both `runtime up` and
`run-service`).
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.runtime.services import (
    ENGINEERING_PROFILE,
    PROFILES,
    ServiceKind,
    list_services,
    resolve_service,
)


class EngineeringProfileTests(unittest.TestCase):
    def test_engineering_profile_lists_all_required_services(self) -> None:
        ids = {spec.service_id for spec in ENGINEERING_PROFILE}
        # Spec: 11 implemented + 1 reserved gateway placeholder.
        required = {
            "eng-supervisor-watch",
            "eng-research-worker",
            "eng-role-tech-lead",
            "eng-role-backend-engineer",
            "eng-role-qa-engineer",
            "eng-role-devops-engineer",
            "eng-role-ai-engineer",
            "eng-role-frontend-engineer",
            "eng-role-product-designer",
            "eng-approval-worker",
            "eng-obsidian-writer",
            "eng-discord-gateway",
        }
        self.assertEqual(ids, required)

    def test_role_workers_carry_role_filter(self) -> None:
        for spec in ENGINEERING_PROFILE:
            if spec.kind == ServiceKind.ROLE_WORKER:
                self.assertIsNotNone(
                    spec.role,
                    f"{spec.service_id} ROLE_WORKER missing role filter",
                )
                # service_id ends with the role for clarity.
                self.assertTrue(spec.service_id.endswith(spec.role or ""))

    def test_non_role_workers_have_no_role_filter(self) -> None:
        for spec in ENGINEERING_PROFILE:
            if spec.kind != ServiceKind.ROLE_WORKER:
                self.assertIsNone(spec.role)

    def test_service_ids_are_unique(self) -> None:
        ids = [spec.service_id for spec in ENGINEERING_PROFILE]
        self.assertEqual(len(ids), len(set(ids)))

    def test_reserved_gateway_is_not_implemented(self) -> None:
        gateway = next(
            spec for spec in ENGINEERING_PROFILE
            if spec.service_id == "eng-discord-gateway"
        )
        self.assertEqual(
            gateway.kind, ServiceKind.RESERVED_DISCORD_GATEWAY
        )
        self.assertFalse(gateway.is_implemented())

    def test_other_services_are_implemented(self) -> None:
        for spec in ENGINEERING_PROFILE:
            if spec.kind == ServiceKind.RESERVED_DISCORD_GATEWAY:
                continue
            self.assertTrue(
                spec.is_implemented(),
                f"{spec.service_id} should be implemented",
            )


class ProfileLookupTests(unittest.TestCase):
    def test_unknown_profile_raises(self) -> None:
        with self.assertRaises(ValueError):
            list_services("nonexistent-profile")

    def test_resolve_service_finds_engineering_entries(self) -> None:
        spec = resolve_service("eng-role-backend-engineer")
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(spec.role, "backend-engineer")

    def test_resolve_unknown_service_returns_none(self) -> None:
        self.assertIsNone(resolve_service("eng-no-such-service"))

    def test_engineering_profile_in_registry(self) -> None:
        self.assertIn("engineering", PROFILES)
        self.assertEqual(PROFILES["engineering"], ENGINEERING_PROFILE)


if __name__ == "__main__":
    unittest.main()
