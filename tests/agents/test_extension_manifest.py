"""Manifest dataclass + validation tests (F11 / #102 MVP)."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.extension.manifest import (
    AUTONOMY_LEVELS,
    AgentManifest,
    HookEvent,
    ManifestValidationError,
    PLUGIN_KINDS,
    PluginManifest,
    RISK_CLASSES,
    load_agent_manifest_from_dict,
    load_plugin_manifest_from_dict,
    validate_agent_manifest,
    validate_plugin_manifest,
)


_VALID_PLUGIN_DICT = {
    "id": "paste-guard",
    "name": "PasteGuard",
    "version": "0.1.0",
    "kind": "guard",
    "hooks_provided": ["OUTBOUND_LLM", "OUTBOUND_DISCORD"],
    "hooks_consumed": [],
    "env_keys": [],
    "autonomy_level": "supervised",
    "paste_guard_required": False,
    "risk_class": "HIGH",
    "module_path": "yule_engineering.agents.security.paste_guard",
}

_VALID_AGENT_DICT = {
    "id": "tech-lead",
    "name": "Tech Lead",
    "role": "tech-lead",
    "version": "0.1.0",
    "capabilities": ["task-decomposition"],
    "plugins_required": ["paste-guard"],
    "prompt_template_ref": "engineering.tech_lead.v1",
    "github_app_env_prefix": "GITHUB_APP_TECH_LEAD",
    "autonomy_level": "advisory",
    "risk_class": "LOW",
    "module_path": "",
}


class HookEventTests(unittest.TestCase):
    def test_canonical_hook_set(self) -> None:
        expected = {
            "PREFLIGHT",
            "OUTBOUND_LLM",
            "OUTBOUND_DISCORD",
            "OUTBOUND_GITHUB",
            "OUTBOUND_VAULT",
            "COMPLETION",
            "POSTMORTEM",
        }
        self.assertEqual({m.name for m in HookEvent}, expected)


class PluginManifestLoaderTests(unittest.TestCase):
    def test_load_from_dict_returns_frozen_dataclass(self) -> None:
        manifest = load_plugin_manifest_from_dict(dict(_VALID_PLUGIN_DICT))
        self.assertIsInstance(manifest, PluginManifest)
        with self.assertRaises(Exception):
            manifest.id = "mutated"  # type: ignore[misc]
        self.assertEqual(manifest.hooks_provided, ("OUTBOUND_LLM", "OUTBOUND_DISCORD"))
        self.assertEqual(manifest.risk_class, "HIGH")

    def test_load_rejects_non_mapping(self) -> None:
        with self.assertRaises(ManifestValidationError):
            load_plugin_manifest_from_dict(["not", "a", "dict"])

    def test_load_rejects_missing_required(self) -> None:
        payload = dict(_VALID_PLUGIN_DICT)
        del payload["kind"]
        with self.assertRaises(ManifestValidationError):
            load_plugin_manifest_from_dict(payload)

    def test_load_rejects_unknown_hook(self) -> None:
        payload = dict(_VALID_PLUGIN_DICT)
        payload["hooks_provided"] = ["NOT_A_HOOK"]
        with self.assertRaises(ManifestValidationError):
            load_plugin_manifest_from_dict(payload)

    def test_load_rejects_bad_module_path(self) -> None:
        payload = dict(_VALID_PLUGIN_DICT)
        payload["module_path"] = "path/with/slash.py"
        with self.assertRaises(ManifestValidationError):
            load_plugin_manifest_from_dict(payload)

    def test_load_rejects_unknown_kind(self) -> None:
        payload = dict(_VALID_PLUGIN_DICT)
        payload["kind"] = "wizardry"
        with self.assertRaises(ManifestValidationError):
            load_plugin_manifest_from_dict(payload)

    def test_load_rejects_unknown_risk_class(self) -> None:
        payload = dict(_VALID_PLUGIN_DICT)
        payload["risk_class"] = "CATASTROPHIC"
        with self.assertRaises(ManifestValidationError):
            load_plugin_manifest_from_dict(payload)


class AgentManifestLoaderTests(unittest.TestCase):
    def test_load_from_dict_returns_frozen_dataclass(self) -> None:
        manifest = load_agent_manifest_from_dict(dict(_VALID_AGENT_DICT))
        self.assertIsInstance(manifest, AgentManifest)
        with self.assertRaises(Exception):
            manifest.role = "renamed"  # type: ignore[misc]
        self.assertEqual(manifest.plugins_required, ("paste-guard",))
        self.assertEqual(manifest.role, "tech-lead")

    def test_load_rejects_bad_role(self) -> None:
        payload = dict(_VALID_AGENT_DICT)
        payload["role"] = "Not Kebab"
        with self.assertRaises(ManifestValidationError):
            load_agent_manifest_from_dict(payload)

    def test_load_rejects_bad_version(self) -> None:
        payload = dict(_VALID_AGENT_DICT)
        payload["version"] = "v1"
        with self.assertRaises(ManifestValidationError):
            load_agent_manifest_from_dict(payload)


class ValidationDirectTests(unittest.TestCase):
    def test_validate_plugin_rejects_wrong_type(self) -> None:
        with self.assertRaises(ManifestValidationError):
            validate_plugin_manifest("not-a-manifest")  # type: ignore[arg-type]

    def test_validate_agent_rejects_wrong_type(self) -> None:
        with self.assertRaises(ManifestValidationError):
            validate_agent_manifest("not-a-manifest")  # type: ignore[arg-type]

    def test_constants_are_exhaustive(self) -> None:
        self.assertIn("HIGH", RISK_CLASSES)
        self.assertIn("supervised", AUTONOMY_LEVELS)
        self.assertIn("guard", PLUGIN_KINDS)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
