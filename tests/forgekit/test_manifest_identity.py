"""Manifest ↔ identity registry consistency guard (drift prevention).

After backfill, every manifest that resolves to a canonical identity must carry the
registry-derived identity fields and have NO prefix drift. This locks the manifests
and the registry to one SSoT so they can't silently diverge.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.identity import registry as reg

_REPO = Path(__file__).resolve().parents[2]
_AGENTS = _REPO / "agents"

_REQUIRED = ("github_app_env_prefix", "git_author_name", "git_author_email",
             "vault_agent_id", "vault_css_class", "vault_color_token")


class ManifestIdentityTests(unittest.TestCase):
    def _matched_manifests(self):
        out = []
        for mf in sorted(_AGENTS.rglob("manifest.json")):
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            cid = (reg.canonical_id(str(data.get("id", "")).strip())
                   or reg.canonical_id(str(data.get("role", "")).strip()))
            if cid:
                out.append((mf, data, cid))
        return out

    def test_no_prefix_drift(self) -> None:
        scan = reg.scan_manifests(_AGENTS)
        self.assertEqual(scan["prefix_drift"], [], f"prefix drift: {scan['prefix_drift']}")
        self.assertGreaterEqual(len(scan["matched"]), 20)   # the backfilled set

    def test_matched_manifests_have_identity_fields(self) -> None:
        matched = self._matched_manifests()
        self.assertGreaterEqual(len(matched), 20)
        for mf, data, _cid in matched:
            for field in _REQUIRED:
                self.assertTrue(data.get(field), f"{mf.name} missing {field}")

    def test_manifest_identity_matches_registry(self) -> None:
        for mf, data, cid in self._matched_manifests():
            ident = reg.resolve_identity(cid)
            self.assertEqual(data.get("git_author_name"), ident.git_author_name, mf.name)
            self.assertEqual(data.get("vault_css_class"), ident.vault_cssclass, mf.name)


if __name__ == "__main__":
    unittest.main()
