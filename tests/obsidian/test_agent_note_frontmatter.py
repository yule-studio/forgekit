"""Contract-stamped frontmatter helper for agent-authored vault notes.

Task 1 wiring: an agent writing a vault note stamps its invocation
contract identity (agent / role / department / obsidian_lane /
color_token / write_owner / retrieval_weight) onto the note frontmatter,
additively, without clobbering exporter-owned keys.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.governance.agent_contract_registry import contract_for
from yule_engineering.agents.obsidian import agent_note_frontmatter as anf


class ContractFrontmatterKeysTests(unittest.TestCase):
    def test_keys_match_contract_identity(self) -> None:
        role = "backend-engineer"
        contract = contract_for(role)
        keys = anf.contract_frontmatter_keys(role, kind="decision")

        self.assertEqual(set(keys), set(anf.CONTRACT_KEYS))
        self.assertEqual(keys["agent"], contract.agent_id)
        self.assertEqual(keys["role"], contract.role_id)
        self.assertEqual(keys["department"], contract.department_id)
        self.assertEqual(keys["obsidian_lane"], contract.obsidian_write_target)
        self.assertEqual(keys["color_token"], contract.color_token)
        self.assertEqual(keys["write_owner"], contract.role_id)

    def test_retrieval_weight_follows_kind(self) -> None:
        canonical = anf.contract_frontmatter_keys("backend-engineer", kind="canonical")
        status = anf.contract_frontmatter_keys("backend-engineer", kind="status")
        self.assertEqual(canonical["retrieval_weight"], 2.0)
        self.assertEqual(status["retrieval_weight"], 0.5)

    def test_explicit_retrieval_weight_override(self) -> None:
        keys = anf.contract_frontmatter_keys(
            "backend-engineer", kind="status", retrieval_weight=9.0
        )
        self.assertEqual(keys["retrieval_weight"], 9.0)


class StampContractFrontmatterTests(unittest.TestCase):
    def test_additive_merge_preserves_exporter_keys(self) -> None:
        base = {
            "title": "Upload pipeline 결정",
            "kind": "decision",
            "status": "draft",
            "roles": ["engineering-agent/backend-engineer"],
            "topic": "upload",
            "tags": ["backend"],
        }
        stamped = anf.stamp_contract_frontmatter(base, "backend-engineer")

        # Existing exporter keys untouched.
        for key, value in base.items():
            self.assertEqual(stamped[key], value)
        # Contract identity layered on top.
        contract = contract_for("backend-engineer")
        self.assertEqual(stamped["agent"], contract.agent_id)
        self.assertEqual(stamped["role"], contract.role_id)
        self.assertEqual(stamped["obsidian_lane"], contract.obsidian_write_target)
        self.assertEqual(stamped["color_token"], contract.color_token)
        self.assertEqual(stamped["write_owner"], contract.role_id)
        # kind drove the retrieval weight.
        self.assertEqual(stamped["retrieval_weight"], 1.0)

    def test_does_not_mutate_input(self) -> None:
        base = {"title": "t", "kind": "reference"}
        anf.stamp_contract_frontmatter(base, "backend-engineer")
        self.assertNotIn("agent", base)

    def test_no_overwrite_by_default(self) -> None:
        base = {"kind": "decision", "color_token": "preset-by-caller"}
        stamped = anf.stamp_contract_frontmatter(base, "backend-engineer")
        self.assertEqual(stamped["color_token"], "preset-by-caller")

    def test_overwrite_flag_replaces(self) -> None:
        base = {"kind": "decision", "color_token": "preset-by-caller"}
        stamped = anf.stamp_contract_frontmatter(
            base, "backend-engineer", overwrite=True
        )
        self.assertEqual(
            stamped["color_token"], contract_for("backend-engineer").color_token
        )

    def test_kind_falls_back_to_base(self) -> None:
        base = {"kind": "canonical"}
        stamped = anf.stamp_contract_frontmatter(base, "backend-engineer")
        self.assertEqual(stamped["retrieval_weight"], 2.0)


class BuildAgentNoteFrontmatterTests(unittest.TestCase):
    def test_full_frontmatter_validates(self) -> None:
        from yule_engineering.agents.governance import note_frontmatter as nf

        fm = anf.build_agent_note_frontmatter(
            "backend-engineer",
            title="제목",
            kind="decision",
            project="bkurs",
            topic="upload",
        )
        self.assertEqual(nf.validate_frontmatter(fm), ())
        self.assertEqual(fm["agent"], contract_for("backend-engineer").agent_id)


if __name__ == "__main__":
    unittest.main()
