"""Agent invocation contract + color/metadata governance (drift guards).

Pins: every role has a contract with a color token + vault lane; color tokens are
unique; only executor/platform may commit code; the registry matches on-disk role
manifests (no drift); the note frontmatter schema is complete; the 3 new roles
exist.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.governance import agent_contract_registry as reg
from yule_engineering.agents.governance import note_frontmatter as nf

_REPO = Path(__file__).resolve().parents[2]
_AGENTS = _REPO / "agents"

# Top-level department manifests that are not roles (skip in drift check).
_DEPT_LEVEL = {"engineering-agent"}


def _on_disk_roles() -> set:
    roles = set()
    for manifest in _AGENTS.glob("*/manifest.json"):
        dept = manifest.parent.name
        if dept == "planning-agent":  # planning-agent is itself a role
            roles.add(("planning-agent", "planning-agent"))
        # else: department-level manifest, not a role
    for manifest in _AGENTS.glob("*/*/manifest.json"):
        roles.add((manifest.parent.parent.name, manifest.parent.name))
    return roles


class CoverageTests(unittest.TestCase):
    def test_every_role_has_color_and_lane(self) -> None:
        for c in reg.all_contracts():
            self.assertTrue(c.color_token, c.role_id)
            self.assertTrue(c.obsidian_write_target, c.role_id)
            self.assertTrue(c.color_hex.startswith("#"))

    def test_color_tokens_and_hex_unique(self) -> None:
        contracts = reg.all_contracts()
        tokens = [c.color_token for c in contracts]
        hexes = [c.color_hex for c in contracts]
        self.assertEqual(len(set(tokens)), len(tokens), "color tokens must be unique")
        self.assertEqual(len(set(hexes)), len(hexes), "color hex must be unique per role")

    def test_only_executor_and_platform_commit(self) -> None:
        for c in reg.all_contracts():
            if c.can_commit:
                self.assertIn(c.contract_class, (reg.CLASS_EXECUTOR, reg.CLASS_PLATFORM), c.role_id)
            if c.contract_class not in (reg.CLASS_EXECUTOR, reg.CLASS_PLATFORM):
                self.assertFalse(c.can_write_code, c.role_id)
                self.assertFalse(c.can_commit, c.role_id)

    def test_advisory_reviewer_observer_are_vault_only(self) -> None:
        for c in reg.all_contracts():
            if c.contract_class in (reg.CLASS_ADVISORY, reg.CLASS_REVIEWER,
                                    reg.CLASS_OBSERVER, reg.CLASS_CURATOR, reg.CLASS_PRODUCT):
                self.assertTrue(c.can_write_vault)
                self.assertFalse(c.can_commit, c.role_id)


class DriftTests(unittest.TestCase):
    def test_registry_matches_on_disk_manifests(self) -> None:
        registry_roles = {(d, r) for d, r, _ in reg.ROLE_REGISTRY}
        on_disk = _on_disk_roles()
        new = {("engineering-agent", r) for r in reg.NEW_ROLES}
        # every on-disk role (minus brand-new ones not yet on disk) is in the registry
        missing = on_disk - registry_roles
        self.assertEqual(missing, set(), f"on-disk roles missing a contract: {missing}")
        # every registry role is either on disk or one of the 3 new roles
        extra = registry_roles - on_disk - new
        self.assertEqual(extra, set(), f"registry roles not on disk (and not new): {extra}")

    def test_new_roles_declared(self) -> None:
        self.assertEqual(
            set(reg.NEW_ROLES),
            {"platform-runtime-engineer", "knowledge-engineer", "ops-observer"},
        )


class FrontmatterTests(unittest.TestCase):
    def test_schema_complete_and_metadata_driven(self) -> None:
        c = reg.contract_for("product-manager")
        fm = nf.build_frontmatter(c, title="t", kind="decision", project="p", topic="upload")
        self.assertEqual(nf.validate_frontmatter(fm), ())
        # retrieval is metadata-driven: weight from kind, not color
        self.assertEqual(fm["retrieval_weight"], 1.0)
        self.assertEqual(fm["agent"], "product-agent/product-manager")
        self.assertIn("color_token", fm)  # present but a human aid, not the key signal

    def test_canonical_outweighs_status(self) -> None:
        self.assertGreater(nf.default_retrieval_weight("canonical"),
                           nf.default_retrieval_weight("status"))

    def test_missing_keys_detected(self) -> None:
        self.assertIn("agent", nf.validate_frontmatter({"title": "x"}))


if __name__ == "__main__":
    unittest.main()
