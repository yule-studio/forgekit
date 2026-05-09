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
    ENV_CODING_EXECUTOR_AUTOSPAWN,
    PROFILES,
    ServiceKind,
    build_engineering_profile,
    is_coding_executor_autospawn_enabled,
    list_services,
    resolve_service,
)


class EngineeringProfileTests(unittest.TestCase):
    def test_engineering_profile_lists_all_required_services(self) -> None:
        ids = {spec.service_id for spec in ENGINEERING_PROFILE}
        # Spec: 11 always-on workers + 1 opt-in coding executor (#73)
        # + 1 discord gateway = 13 total. ``eng-coding-executor`` is
        # ``auto_spawn=False`` so ``yule runtime up`` skips it without
        # explicit operator opt-in (live executor wiring + push creds).
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
            "eng-coding-executor",
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

    def test_gateway_is_implemented_after_m6_1b_2(self) -> None:
        # M6.1b-2 flipped the gateway from RESERVED_DISCORD_GATEWAY
        # to DISCORD_GATEWAY — ``yule run-service eng-discord-gateway``
        # now spawns the engineering gateway via ``runtime.run_service``.
        gateway = next(
            spec for spec in ENGINEERING_PROFILE
            if spec.service_id == "eng-discord-gateway"
        )
        self.assertEqual(gateway.kind, ServiceKind.DISCORD_GATEWAY)
        self.assertTrue(gateway.is_implemented())

    def test_all_engineering_services_are_implemented(self) -> None:
        for spec in ENGINEERING_PROFILE:
            self.assertTrue(
                spec.is_implemented(),
                f"{spec.service_id} should be implemented after M6.1b-2",
            )


class CodingExecutorAutoSpawnTests(unittest.TestCase):
    """#73 Round 2 — env-driven opt-in for ``eng-coding-executor``.

    Default (env unset / falsey) → ``auto_spawn=False`` so ``runtime up``
    skips the executor. Truthy ``YULE_CODING_EXECUTOR_AUTOSPAWN`` flips
    the spec to ``auto_spawn=True``.
    """

    def _coding_executor_spec(self, profile):
        return next(
            spec for spec in profile if spec.service_id == "eng-coding-executor"
        )

    def test_default_when_env_unset(self) -> None:
        profile = build_engineering_profile(env={})
        self.assertFalse(self._coding_executor_spec(profile).auto_spawn)

    def test_default_when_env_falsey(self) -> None:
        for value in ("", "false", "no", "off", "0", "  False  "):
            with self.subTest(value=value):
                profile = build_engineering_profile(
                    env={ENV_CODING_EXECUTOR_AUTOSPAWN: value}
                )
                self.assertFalse(self._coding_executor_spec(profile).auto_spawn)

    def test_truthy_env_flips_to_auto_spawn(self) -> None:
        for value in ("1", "true", "TRUE", "yes", "on"):
            with self.subTest(value=value):
                profile = build_engineering_profile(
                    env={ENV_CODING_EXECUTOR_AUTOSPAWN: value}
                )
                self.assertTrue(self._coding_executor_spec(profile).auto_spawn)

    def test_other_specs_unaffected_when_env_set(self) -> None:
        # Flipping the executor flag must not change auto_spawn on
        # any other service. Hard rail: opt-in is scoped to one row.
        profile = build_engineering_profile(
            env={ENV_CODING_EXECUTOR_AUTOSPAWN: "true"}
        )
        for spec in profile:
            if spec.service_id == "eng-coding-executor":
                self.assertTrue(spec.auto_spawn)
            else:
                self.assertTrue(spec.auto_spawn)

    def test_helper_returns_truthy_state(self) -> None:
        self.assertTrue(
            is_coding_executor_autospawn_enabled(
                env={ENV_CODING_EXECUTOR_AUTOSPAWN: "true"}
            )
        )
        self.assertFalse(is_coding_executor_autospawn_enabled(env={}))

    def test_unrelated_env_var_does_not_enable(self) -> None:
        # Detecting GitHub App env / push creds does NOT enable auto-spawn.
        # Operator must set the exact flag.
        profile = build_engineering_profile(
            env={
                "YULE_GITHUB_APP_ID": "123456",
                "YULE_GITHUB_APP_INSTALLATION_ID": "789",
            }
        )
        self.assertFalse(self._coding_executor_spec(profile).auto_spawn)


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
