"""Issue #185 — harness projection drift guard.

The committed harness artifacts (``.claude/skills``, ``.agents/skills``,
``.claude-plugin/plugin.json``, ``.codex-plugin/plugin.json``) are GENERATED
from the registry SSoT by ``scripts/sync_harness_skills.py``. This test
re-runs the generator in memory and fails if the committed output drifts —
the same guarantee as the script's ``--check`` mode, enforced in CI.
"""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.harness import load_grant_table

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "sync_harness_skills.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("sync_harness_skills", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HarnessProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_script_module()
        cls.table = load_grant_table()
        cls.artifacts = cls.mod.build_artifacts(cls.table)

    def test_committed_artifacts_match_ssot(self) -> None:
        drift = self.mod.check_artifacts(_REPO_ROOT, self.artifacts)
        self.assertEqual(
            drift,
            [],
            msg=(
                "harness artifacts drifted from SSoT; run "
                "`python3 scripts/sync_harness_skills.py`:\n  - "
                + "\n  - ".join(drift)
            ),
        )

    def test_every_custom_skill_projected_to_both_harnesses(self) -> None:
        for skill_id in self.table.custom_skills:
            for base in (".claude/skills", ".agents/skills"):
                rel = f"{base}/{skill_id}/SKILL.md"
                with self.subTest(artifact=rel):
                    self.assertIn(rel, self.artifacts)
                    self.assertTrue((_REPO_ROOT / rel).is_file())

    def test_projected_skill_points_at_ssot(self) -> None:
        rel = ".claude/skills/compact-to-vault/SKILL.md"
        content = (_REPO_ROOT / rel).read_text(encoding="utf-8")
        self.assertIn("name: compact-to-vault", content)
        self.assertIn("DO NOT EDIT", content)
        self.assertIn(
            "agents/engineering-agent/skills/compact-to-vault.md", content
        )

    def test_plugin_manifests_list_all_skills(self) -> None:
        for rel in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
            with self.subTest(manifest=rel):
                data = json.loads((_REPO_ROOT / rel).read_text(encoding="utf-8"))
                self.assertEqual(
                    sorted(data["skills"]), sorted(self.table.custom_skills)
                )
                self.assertEqual(data["ssot"], "agents/grants/slash-command-grants.json")


if __name__ == "__main__":
    unittest.main()
