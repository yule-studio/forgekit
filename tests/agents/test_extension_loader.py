"""Manifest discovery + lazy module loader tests (F11.1 / #107)."""

from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.extension.loader import (
    ManifestDiscoveryError,
    discover_manifests,
    discover_manifests_with_report,
    load_plugin_module,
)
from yule_engineering.agents.extension.manifest import PluginManifest


_PLUGIN_VALID = {
    "id": "paste-guard",
    "name": "PasteGuard",
    "version": "0.1.0",
    "kind": "guard",
    "hooks_provided": ["OUTBOUND_LLM"],
    "hooks_consumed": [],
    "env_keys": [],
    "autonomy_level": "supervised",
    "paste_guard_required": False,
    "risk_class": "HIGH",
    "module_path": "yule_security.paste_guard",
}

_AGENT_VALID = {
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


def _write_manifest(root: Path, sub: str, payload: dict) -> None:
    entry = root / sub
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")


class DiscoverManifestsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.plugins_dir = self.root / "plugins"
        self.agents_dir = self.root / "agents"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_returns_empty_tuples_when_dirs_missing(self) -> None:
        plugins, agents = discover_manifests(
            plugins_dir=self.root / "missing-plugins",
            agents_dir=self.root / "missing-agents",
        )
        self.assertEqual(plugins, ())
        self.assertEqual(agents, ())

    def test_discovers_plugin_and_agent_manifests(self) -> None:
        _write_manifest(self.plugins_dir, "paste-guard", _PLUGIN_VALID)
        hookify = dict(_PLUGIN_VALID)
        hookify.update(
            {
                "id": "hookify",
                "name": "Hookify",
                "kind": "learning",
                "risk_class": "MEDIUM",
                "hooks_provided": ["PREFLIGHT", "COMPLETION"],
                "paste_guard_required": True,
                "autonomy_level": "advisory",
                "module_path": "yule_learning.mistake_ledger",
            }
        )
        _write_manifest(self.plugins_dir, "hookify", hookify)
        _write_manifest(self.agents_dir, "tech-lead", _AGENT_VALID)

        plugins, agents = discover_manifests(
            plugins_dir=self.plugins_dir,
            agents_dir=self.agents_dir,
        )

        self.assertEqual([m.id for m in plugins], ["hookify", "paste-guard"])  # sorted
        self.assertEqual([m.id for m in agents], ["tech-lead"])
        self.assertEqual(plugins[1].risk_class, "HIGH")

    def test_invalid_json_raises_discovery_error(self) -> None:
        entry = self.plugins_dir / "broken"
        entry.mkdir(parents=True)
        (entry / "manifest.json").write_text("{not json", encoding="utf-8")

        with self.assertRaises(ManifestDiscoveryError) as ctx:
            discover_manifests(plugins_dir=self.plugins_dir, agents_dir=self.agents_dir)
        self.assertIn("invalid JSON", str(ctx.exception))

    def test_non_object_payload_raises(self) -> None:
        entry = self.plugins_dir / "arrayish"
        entry.mkdir(parents=True)
        (entry / "manifest.json").write_text("[1, 2, 3]", encoding="utf-8")

        with self.assertRaises(ManifestDiscoveryError):
            discover_manifests(plugins_dir=self.plugins_dir, agents_dir=self.agents_dir)

    def test_validation_failure_is_wrapped_as_discovery_error(self) -> None:
        bad = dict(_PLUGIN_VALID)
        bad["risk_class"] = "ULTRA"  # not a valid RISK_CLASSES entry
        _write_manifest(self.plugins_dir, "paste-guard", bad)

        with self.assertRaises(ManifestDiscoveryError) as ctx:
            discover_manifests(plugins_dir=self.plugins_dir, agents_dir=self.agents_dir)
        self.assertIn("risk_class", str(ctx.exception))

    def test_directory_without_manifest_is_recorded_as_skipped(self) -> None:
        (self.plugins_dir / "wip-plugin").mkdir(parents=True)
        _write_manifest(self.agents_dir, "tech-lead", _AGENT_VALID)

        report = discover_manifests_with_report(
            plugins_dir=self.plugins_dir,
            agents_dir=self.agents_dir,
        )

        self.assertEqual(report.plugins, ())
        self.assertEqual([m.id for m in report.agents], ["tech-lead"])
        self.assertTrue(any("wip-plugin" in str(p) for p in report.skipped))


class LoadPluginModuleTests(unittest.TestCase):
    def _manifest(self, *, module_path: str) -> PluginManifest:
        return PluginManifest(
            id="fake",
            name="Fake",
            version="0.1.0",
            kind="guard",
            hooks_provided=(),
            hooks_consumed=(),
            env_keys=(),
            autonomy_level="advisory",
            paste_guard_required=True,
            risk_class="LOW",
            module_path=module_path,
        )

    def test_empty_module_path_refused(self) -> None:
        with self.assertRaises(ValueError):
            load_plugin_module(self._manifest(module_path=""))

    def test_lazy_import_resolves_registered_module(self) -> None:
        mod_name = "tests_fake_loader_module_for_hook_chain"
        fake = types.ModuleType(mod_name)
        fake.MARKER = "hello"
        sys.modules[mod_name] = fake
        try:
            module = load_plugin_module(self._manifest(module_path=mod_name))
            self.assertIs(module, fake)
            self.assertEqual(module.MARKER, "hello")
        finally:
            sys.modules.pop(mod_name, None)

    def test_import_failure_propagates(self) -> None:
        with self.assertRaises(ImportError):
            load_plugin_module(self._manifest(module_path="definitely.not.a.real.module.x9z"))

    def test_non_manifest_input_rejected(self) -> None:
        with self.assertRaises(TypeError):
            load_plugin_module({"module_path": "os"})  # type: ignore[arg-type]


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
