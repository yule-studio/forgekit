"""Canonical agent identity registry — the single SSoT (pure).

Proves formal-id and alias resolution both reach the canonical identity, the
abbreviation→formal mapping lives only here, derivation rules (github prefix / git
author) are deterministic, projections are stable, and unknown ids fall back honestly.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.identity import registry as reg
from forgekit_console.identity.models import AgentIdentity, github_app_env_prefix


class ResolveTests(unittest.TestCase):
    def test_formal_id_resolves(self) -> None:
        ident = reg.resolve_identity("frontend-engineer")
        self.assertEqual(ident.canonical_id, "frontend-engineer")
        self.assertEqual(ident.vault_cssclass, "fk-fe")

    def test_alias_resolves_to_canonical(self) -> None:
        # the abbreviation maps to the FORMAL canonical id (single source)
        self.assertEqual(reg.canonical_id("fe"), "frontend-engineer")
        self.assertEqual(reg.resolve_identity("be").canonical_id, "backend-engineer")
        self.assertEqual(reg.resolve_identity("product-agent").canonical_id, "product-manager")

    def test_unknown_is_fallback(self) -> None:
        self.assertFalse(reg.is_known("nope"))
        self.assertEqual(reg.resolve_identity("nope").canonical_id, "forgekit")

    def test_no_duplicate_canonical_or_alias_collision(self) -> None:
        ids = [i.canonical_id for i in reg.all_identities()]
        self.assertEqual(len(ids), len(set(ids)))           # canonical ids unique
        # an alias never collides with a different canonical id
        for ident in reg.all_identities():
            for alias in ident.identity_aliases:
                self.assertEqual(reg.canonical_id(alias), ident.canonical_id)


class DerivationTests(unittest.TestCase):
    def test_github_prefix_rule(self) -> None:
        self.assertEqual(reg.resolve_identity("tech-lead").github_app_env_prefix,
                         "YULE_GITHUB_APP_TECH_LEAD_")
        self.assertEqual(reg.resolve_identity("design-systems-designer").github_app_env_prefix,
                         "YULE_GITHUB_APP_DESIGN_SYSTEMS_DESIGNER_")
        self.assertEqual(github_app_env_prefix("x", shared=True), "YULE_GITHUB_APP_SHARED_")

    def test_git_author_rule(self) -> None:
        gid = reg.git_identity_for("backend-engineer")
        self.assertEqual(gid["name"], "Forgekit Backend")
        self.assertEqual(gid["email"], "be@forgekit.local")

    def test_alias_input_projects_canonical(self) -> None:
        # an alias input must still project canonical metadata (not the abbreviation)
        v = reg.vault_identity_for("fe")
        self.assertEqual(v["agent_author"], "frontend-engineer")
        self.assertEqual(v["cssclass"], "fk-fe")
        g = reg.git_identity_for("fe")
        self.assertEqual(g["canonical_id"], "frontend-engineer")


class ExportTests(unittest.TestCase):
    def test_to_dict_machine_readable(self) -> None:
        d = reg.to_dict()
        self.assertIn("identities", d)
        self.assertIn("aliases", d)
        self.assertEqual(d["aliases"]["fe"], "frontend-engineer")
        # every identity row has the required fields
        for row in d["identities"]:
            for key in ("canonical_id", "github_app_env_prefix", "git_author_name",
                        "vault_cssclass", "vault_color"):
                self.assertTrue(row.get(key), f"missing {key} in {row['canonical_id']}")


if __name__ == "__main__":
    unittest.main()
