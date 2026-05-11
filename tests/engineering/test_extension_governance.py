"""Governance tests for F11 / #102 — plugin + agent manifest catalogue.

These regression tests enforce two hard rails on the *shipped* manifest
catalogue (the JSON files under ``plugins/`` and ``agents/``):

  1. Every role in the 7-role engineering team has a manifest file and
     the manifest's ``role`` matches its directory name.
  2. Any plugin manifest whose ``risk_class`` is ``HIGH`` must declare
     it explicitly (no silent default) — catching the case where a
     reviewer accidentally drops the field on a sensitive plugin like
     PasteGuard.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.extension.manifest import (
    load_agent_manifest_from_dict,
    load_plugin_manifest_from_dict,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLUGINS_DIR = _REPO_ROOT / "plugins"
_AGENTS_DIR = _REPO_ROOT / "agents"

_EXPECTED_ROLES = (
    "tech-lead",
    "backend-engineer",
    "frontend-engineer",
    "qa-engineer",
    "devops-engineer",
    "ai-engineer",
    "product-designer",
)

_HIGH_RISK_PLUGIN_IDS = ("paste-guard",)


def _read_manifest_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


class ExtensionGovernanceTests(unittest.TestCase):
    def test_seven_role_agent_manifests_exist(self) -> None:
        for role in _EXPECTED_ROLES:
            manifest_path = _AGENTS_DIR / role / "manifest.json"
            self.assertTrue(
                manifest_path.is_file(),
                f"missing agent manifest for role '{role}' at {manifest_path}",
            )

    def test_agent_manifest_role_matches_directory(self) -> None:
        for role in _EXPECTED_ROLES:
            payload = _read_manifest_json(_AGENTS_DIR / role / "manifest.json")
            manifest = load_agent_manifest_from_dict(payload)
            self.assertEqual(
                manifest.role,
                role,
                f"agents/{role}/manifest.json role mismatch: {manifest.role!r}",
            )

    def test_three_seed_plugin_manifests_load(self) -> None:
        for plugin_id in ("paste-guard", "hookify", "repo-map"):
            payload = _read_manifest_json(_PLUGINS_DIR / plugin_id / "manifest.json")
            manifest = load_plugin_manifest_from_dict(payload)
            self.assertEqual(manifest.id, plugin_id)

    def test_high_risk_plugins_declare_risk_explicitly(self) -> None:
        for plugin_id in _HIGH_RISK_PLUGIN_IDS:
            raw = _read_manifest_json(_PLUGINS_DIR / plugin_id / "manifest.json")
            self.assertEqual(
                raw.get("risk_class"),
                "HIGH",
                f"plugin '{plugin_id}' must declare risk_class=HIGH explicitly",
            )

    def test_no_extra_role_directories_without_manifest(self) -> None:
        for entry in sorted(_AGENTS_DIR.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name not in _EXPECTED_ROLES:
                continue
            self.assertTrue(
                (entry / "manifest.json").is_file(),
                f"role dir '{entry.name}' exists but has no manifest.json",
            )

    def test_paste_guard_provides_all_outbound_hooks(self) -> None:
        payload = _read_manifest_json(_PLUGINS_DIR / "paste-guard" / "manifest.json")
        manifest = load_plugin_manifest_from_dict(payload)
        self.assertEqual(
            set(manifest.hooks_provided),
            {
                "OUTBOUND_LLM",
                "OUTBOUND_DISCORD",
                "OUTBOUND_GITHUB",
                "OUTBOUND_VAULT",
            },
            "PasteGuard must guard every outbound channel",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
