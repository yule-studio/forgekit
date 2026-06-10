"""Phase 3B — role policy registry tests."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.runtime.policies import (
    RolePolicy,
    all_role_policies,
    role_policy_for,
)


class RolePolicyLookupTests(unittest.TestCase):
    def test_gateway_policy_has_no_memory_role_filter(self) -> None:
        policy = role_policy_for("gateway")
        self.assertEqual(policy.short_name, "gateway")
        self.assertIsNone(policy.memory_role_filter)

    def test_full_role_id_resolves(self) -> None:
        policy = role_policy_for("engineering-agent/tech-lead")
        self.assertEqual(policy.short_name, "tech-lead")
        self.assertEqual(policy.memory_role_filter, "tech-lead")

    def test_short_role_id_resolves_to_engineering_agent(self) -> None:
        policy = role_policy_for("backend-engineer")
        self.assertEqual(policy.short_name, "backend-engineer")
        self.assertEqual(policy.memory_role_filter, "backend-engineer")

    def test_unknown_role_falls_back_to_default(self) -> None:
        policy = role_policy_for("ops-foo")
        self.assertEqual(policy.short_name, "default")
        self.assertIsNone(policy.memory_role_filter)

    def test_blank_role_returns_default(self) -> None:
        policy = role_policy_for("")
        self.assertEqual(policy.short_name, "default")

    def test_all_policies_lists_eight(self) -> None:
        # gateway + 7 engineering-agent member roles.
        self.assertEqual(len(all_role_policies()), 8)
        names = {p.short_name for p in all_role_policies()}
        self.assertEqual(
            names,
            {
                "gateway",
                "tech-lead",
                "ai-engineer",
                "backend-engineer",
                "frontend-engineer",
                "product-designer",
                "qa-engineer",
                "devops-engineer",
            },
        )

    def test_each_policy_has_description(self) -> None:
        for policy in all_role_policies():
            self.assertGreater(len(policy.description), 0, policy.short_name)


if __name__ == "__main__":
    unittest.main()
