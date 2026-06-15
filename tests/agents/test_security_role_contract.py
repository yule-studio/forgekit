"""Security-engineer cross-cutting reviewer contract (issue #185 follow-up, item A/E).

Locks the role contract, its wiring into the department manifest + grant table,
and the governance doc — so the security review subject cannot silently drift.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.harness import load_grant_table

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ROLE_DIR = _REPO_ROOT / "agents" / "engineering-agent" / "security-engineer"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class RoleContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = _json(_ROLE_DIR / "manifest.json")

    def test_role_files_exist(self) -> None:
        self.assertTrue((_ROLE_DIR / "manifest.json").is_file())
        self.assertTrue((_ROLE_DIR / "CLAUDE.md").is_file())

    def test_is_cross_cutting_reviewer(self) -> None:
        self.assertEqual(self.manifest["role_class"], "cross_cutting_reviewer")
        self.assertTrue(self.manifest["reviews_other_roles"])
        self.assertFalse(self.manifest["is_technical_approval_owner"])

    def test_covers_four_review_domains(self) -> None:
        domains = self.manifest["review_domains"]
        for key in ("backend", "frontend", "devops", "ai_agent"):
            self.assertIn(key, domains)
            self.assertTrue(domains[key], f"{key} domain must list checks")

    def test_backend_domain_specifics(self) -> None:
        backend = self.manifest["review_domains"]["backend"]
        for needle in (
            "authentication",
            "authorization",
            "idor_object_level_permission",
            "input_validation",
            "secret_exposure",
            "audit_logging",
        ):
            self.assertIn(needle, backend, needle)

    def test_frontend_treats_client_as_hostile(self) -> None:
        frontend = self.manifest["review_domains"]["frontend"]
        self.assertIn("treat_client_as_hostile_surface", frontend)
        self.assertIn("postmessage_origin_validation", frontend)
        self.assertIn("permission_ui_is_not_a_security_boundary", frontend)

    def test_ai_domain_specifics(self) -> None:
        ai = self.manifest["review_domains"]["ai_agent"]
        for needle in ("prompt_injection", "tool_overreach", "approval_gate_bypass", "data_exfiltration"):
            self.assertIn(needle, ai, needle)

    def test_devtools_anti_goal_present(self) -> None:
        joined = " ".join(self.manifest["anti_goals"])
        self.assertIn("개발자 도구", joined)
        self.assertIn("서버", joined)  # server-side enforcement is the alternative

    def test_intercept_triggers_non_empty(self) -> None:
        self.assertTrue(self.manifest["intercept_triggers"])

    def test_instruction_entry_points_to_role_claude(self) -> None:
        self.assertEqual(
            self.manifest["instruction_entry"],
            "agents/engineering-agent/security-engineer/CLAUDE.md",
        )


class WiringTests(unittest.TestCase):
    def test_department_manifest_lists_as_cross_cutting_not_member(self) -> None:
        dept = _json(_REPO_ROOT / "agents" / "engineering-agent" / "manifest.json")
        self.assertIn("security-engineer", dept["cross_cutting_reviewers"])
        # must NOT be a 7-role deliberation council seat
        self.assertNotIn("security-engineer", dept["members"])

    def test_grant_override_grants_security_review(self) -> None:
        table = load_grant_table()
        eff = table.effective_grants("engineering-agent/security-engineer")
        self.assertIsNotNone(eff)
        assert eff is not None
        self.assertTrue(eff.grants_command("/security-review"))

    def test_grant_table_still_valid(self) -> None:
        table = load_grant_table()
        self.assertEqual(table.validate(repo_root=_REPO_ROOT), [])


class GovernanceDocTests(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = (_REPO_ROOT / "docs" / "security-review.md").read_text(encoding="utf-8")

    def test_doc_exists_with_core_sections(self) -> None:
        for needle in (
            "cross-cutting",
            "intercept",
            "Inputs",
            "Outputs",
            "Boundaries",
            "security-engineer",
        ):
            self.assertIn(needle, self.doc, needle)

    def test_doc_states_devtools_anti_goal(self) -> None:
        self.assertIn("개발자 도구", self.doc)
        self.assertIn("클라이언트는 신뢰 경계가 아니다", self.doc)

    def test_entrypoints_cross_link_doc(self) -> None:
        agents_md = (_REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        root_claude = (_REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("docs/security-review.md", agents_md)
        self.assertIn("docs/security-review.md", root_claude)

    def test_doc_documents_auto_dispatch_wiring(self) -> None:
        # the auto-dispatch seam + false-positive/negative tradeoff are documented
        for needle in ("auto-dispatch", "assess_security_review", "false-positive", "security_status"):
            self.assertIn(needle, self.doc, needle)

    def test_slash_commands_doc_documents_hot_path(self) -> None:
        doc = (_REPO_ROOT / "docs" / "agent-slash-commands.md").read_text(encoding="utf-8")
        for needle in ("YULE_GRANT_ENFORCEMENT_ENABLED", "pre_dispatch_gate", "compact_boundary"):
            self.assertIn(needle, doc, needle)


if __name__ == "__main__":
    unittest.main()
