"""Grant runtime enforcement — ALLOW/ADVISORY/BLOCK (issue #185 follow-up, item C).

Locks the advisory-vs-block line described in
``docs/agent-slash-commands.md`` §"grant 강제".
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.harness import load_grant_table
from yule_engineering.agents.harness.grant_enforcement import (
    CapabilityKind,
    GrantVerdict,
    evaluate_capability,
    evaluate_command,
    evaluate_skill,
)


class CommandEnforcementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.table = load_grant_table()

    def test_granted_command_allows(self) -> None:
        d = evaluate_command(self.table, "engineering-agent", "/compact")
        self.assertEqual(d.verdict, GrantVerdict.ALLOW)
        self.assertTrue(d.allowed)

    def test_ungranted_but_grantable_is_advisory(self) -> None:
        # marketing-agent is not granted /diff, but /diff is a grantable builtin
        d = evaluate_command(self.table, "marketing-agent", "/diff")
        self.assertEqual(d.verdict, GrantVerdict.ADVISORY)
        self.assertTrue(d.advisory)

    def test_non_grantable_command_blocks(self) -> None:
        d = evaluate_command(self.table, "engineering-agent", "/model")
        self.assertEqual(d.verdict, GrantVerdict.BLOCK)
        self.assertTrue(d.blocked)

    def test_unknown_command_blocks(self) -> None:
        d = evaluate_command(self.table, "engineering-agent", "/does-not-exist")
        self.assertEqual(d.verdict, GrantVerdict.BLOCK)

    def test_unknown_actor_blocks(self) -> None:
        d = evaluate_command(self.table, "ghost-agent", "/compact")
        self.assertEqual(d.verdict, GrantVerdict.BLOCK)


class SkillEnforcementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.table = load_grant_table()

    def test_granted_skill_allows(self) -> None:
        d = evaluate_skill(self.table, "engineering-agent", "compact-to-vault")
        self.assertEqual(d.verdict, GrantVerdict.ALLOW)

    def test_ungranted_registered_skill_advisory(self) -> None:
        # marketing-agent is not granted skill-author, but it is registered
        d = evaluate_skill(self.table, "marketing-agent", "skill-author")
        self.assertEqual(d.verdict, GrantVerdict.ADVISORY)

    def test_unknown_skill_blocks(self) -> None:
        d = evaluate_skill(self.table, "engineering-agent", "no-such-skill")
        self.assertEqual(d.verdict, GrantVerdict.BLOCK)

    def test_role_override_grants_security_review(self) -> None:
        d = evaluate_command(
            self.table, "engineering-agent/security-engineer", "/security-review"
        )
        self.assertEqual(d.verdict, GrantVerdict.ALLOW)


class InferKindTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.table = load_grant_table()

    def test_slash_prefix_infers_command(self) -> None:
        d = evaluate_capability(self.table, "engineering-agent", "/compact")
        self.assertEqual(d.kind, CapabilityKind.COMMAND)

    def test_non_slash_infers_skill(self) -> None:
        d = evaluate_capability(self.table, "engineering-agent", "compact-to-vault")
        self.assertEqual(d.kind, CapabilityKind.SKILL)

    def test_surface_string_includes_verdict(self) -> None:
        d = evaluate_command(self.table, "engineering-agent", "/model")
        self.assertIn("BLOCK", d.surface())


if __name__ == "__main__":
    unittest.main()
