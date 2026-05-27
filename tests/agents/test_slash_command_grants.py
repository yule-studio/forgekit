"""Issue #185 — slash-command / skill grant table governance.

Locks down ``agents/grants/slash-command-grants.json`` so that:

  * the table is internally consistent (every granted built-in / skill
    exists in the catalog, autonomy levels are valid, spec files exist);
  * every corporate department (org-chart SSoT) has a grant entry and a
    real ``agents/<dept>/`` directory;
  * the highlighted cross-cutting flow (``/compact`` + ``compact-to-vault``)
    is granted to every department;
  * interactive-only / operator-only built-ins are never granted;
  * role overrides merge as designed.

본 test 가 통과한다 = 슬래시 명령어 grant governance 가 살아 있다.
"""

from __future__ import annotations

import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.harness import load_grant_table

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Corporate departments (corporate-org-chart.md SSoT). A department that
# silently drops out of the grant table should fail here.
_EXPECTED_DEPARTMENTS = (
    "engineering-agent",
    "product-agent",
    "marketing-agent",
    "hr-agent",
    "finance-agent",
    "sales-cs-agent",
    "legal-agent",
    "planning-agent",
)

# Commands that must never be granted (interactive / operator-only UI).
_NON_GRANTABLE = ("/config", "/model", "/agents", "/plugin", "/mcp", "/permissions")


class SlashCommandGrantTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.table = load_grant_table()

    def test_table_is_internally_consistent(self) -> None:
        problems = self.table.validate(repo_root=_REPO_ROOT)
        self.assertEqual(problems, [], msg="\n".join(problems))

    def test_every_department_covered_and_real(self) -> None:
        for dept in _EXPECTED_DEPARTMENTS:
            with self.subTest(dept=dept):
                self.assertIn(
                    dept,
                    self.table.departments,
                    f"{dept} missing from grant table",
                )
                self.assertTrue(
                    (_REPO_ROOT / "agents" / dept).is_dir(),
                    f"agents/{dept}/ directory missing",
                )
        # no stray departments beyond the org-chart set
        self.assertEqual(
            set(self.table.departments),
            set(_EXPECTED_DEPARTMENTS),
            "grant table departments drifted from org-chart set",
        )

    def test_compact_flow_granted_everywhere(self) -> None:
        for dept in _EXPECTED_DEPARTMENTS:
            with self.subTest(dept=dept):
                self.assertTrue(
                    self.table.is_command_granted(dept, "/compact"),
                    f"{dept} should be granted /compact",
                )
                self.assertTrue(
                    self.table.is_skill_granted(dept, "compact-to-vault"),
                    f"{dept} should be granted compact-to-vault",
                )

    def test_non_grantable_commands_never_granted(self) -> None:
        for dept in _EXPECTED_DEPARTMENTS:
            for command in _NON_GRANTABLE:
                with self.subTest(dept=dept, command=command):
                    self.assertFalse(
                        self.table.is_command_granted(dept, command),
                        f"{dept} must not be granted {command}",
                    )

    def test_custom_skill_specs_exist(self) -> None:
        for skill_id, skill in self.table.custom_skills.items():
            with self.subTest(skill=skill_id):
                self.assertTrue(
                    (_REPO_ROOT / skill.spec).is_file(),
                    f"{skill_id} spec missing: {skill.spec}",
                )

    def test_role_override_merges(self) -> None:
        eff = self.table.effective_grants("engineering-agent/tech-lead")
        self.assertIsNotNone(eff)
        assert eff is not None  # narrow for type-checkers
        self.assertTrue(
            eff.grants_skill("skill-author"),
            "tech-lead override should grant skill-author",
        )
        # department-level grants still flow through the override merge
        self.assertTrue(eff.grants_command("/compact"))

    def test_unknown_actor_returns_none(self) -> None:
        self.assertIsNone(self.table.effective_grants("nonexistent-agent"))


if __name__ == "__main__":
    unittest.main()
