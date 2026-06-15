"""Provider / plugin taxonomy invariants (vendor-neutral SSoT → projection).

Pins the separation documented in docs/plugin-taxonomy.md +
docs/provider-capability-matrix.md so the structure can't silently drift:

  * runtime plugins (plugins/<id>/manifest.json) are vendor-neutral hook
    providers with a known `kind` + `hooks_provided` vocabulary;
  * skill grant `harness` tokens are *projection targets* only (claude/codex,
    gemini reserved) — never a backend name like `ollama`;
  * backends live in the agent manifest participants/integrations; Ollama is a
    backend (participant), not a harness projection target nor a runtime plugin;
  * the projection generator routes by a `HARNESS_TARGETS` registry (no silent
    mislabel of unknown targets);
  * the two taxonomy docs exist with their key sections.
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

_REPO_ROOT = Path(__file__).resolve().parents[2]

_PLUGIN_KINDS = {"guard", "learning", "delivery", "exploration"}
_HOOK_TOKENS = {
    "PREFLIGHT", "COMPLETION", "POSTMORTEM",
    "OUTBOUND_LLM", "OUTBOUND_DISCORD", "OUTBOUND_GITHUB", "OUTBOUND_VAULT",
}
# Projection targets (harness). Backends (ollama) are NOT projection targets.
_PROJECTION_TARGETS = {"claude", "codex", "gemini"}
_KNOWN_PROVIDERS = {"anthropic", "openai", "google", "local", "github"}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class RuntimePluginTaxonomyTests(unittest.TestCase):
    def _manifests(self):
        for m in sorted((_REPO_ROOT / "plugins").glob("*/manifest.json")):
            yield m, _load_json(m)

    def test_required_fields_and_vocab(self) -> None:
        seen = 0
        for path, man in self._manifests():
            seen += 1
            with self.subTest(plugin=path.parent.name):
                for field in ("id", "kind", "hooks_provided", "module_path"):
                    self.assertIn(field, man, f"{path.parent.name} missing {field}")
                self.assertIn(man["kind"], _PLUGIN_KINDS, man["kind"])
                for hook in man["hooks_provided"]:
                    self.assertIn(hook, _HOOK_TOKENS, hook)
        self.assertGreaterEqual(seen, 5, "expected several runtime plugins")

    def test_runtime_plugins_are_vendor_neutral(self) -> None:
        # a runtime plugin must not pin itself to a single LLM provider
        for path, man in self._manifests():
            with self.subTest(plugin=path.parent.name):
                self.assertNotIn("provider", man)
                self.assertNotIn("harness", man)


class HarnessProjectionTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.grants = _load_json(_REPO_ROOT / "agents" / "grants" / "slash-command-grants.json")

    def test_skill_harness_are_projection_targets_only(self) -> None:
        for skill_id, meta in self.grants["custom_skills"].items():
            for tok in meta.get("harness", []):
                with self.subTest(skill=skill_id, token=tok):
                    self.assertIn(tok, _PROJECTION_TARGETS, f"{tok} is not a projection target")
                    self.assertNotEqual(tok, "ollama", "ollama is a backend, not a harness target")

    def test_builtin_harness_are_projection_targets(self) -> None:
        for cmd, meta in self.grants["builtin_commands"].items():
            for tok in meta.get("harness", []):
                with self.subTest(command=cmd, token=tok):
                    self.assertIn(tok, _PROJECTION_TARGETS)


class BackendSeparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = _load_json(_REPO_ROOT / "agents" / "engineering-agent" / "manifest.json")

    def test_known_providers(self) -> None:
        for entry in self.manifest.get("participants", []) + self.manifest.get("integrations", []):
            with self.subTest(backend=entry.get("id")):
                self.assertIn(entry.get("provider"), _KNOWN_PROVIDERS)

    def test_ollama_is_backend_not_projection_or_plugin(self) -> None:
        ids = {e.get("id") for e in self.manifest.get("participants", [])}
        self.assertIn("ollama", ids)  # backend participant
        # not a runtime plugin
        self.assertFalse((_REPO_ROOT / "plugins" / "ollama").exists())
        # not a harness projection target
        self.assertNotIn("ollama", _PROJECTION_TARGETS)


class ProjectionGeneratorRegistryTests(unittest.TestCase):
    def _sync_module(self):
        spec = importlib.util.spec_from_file_location(
            "sync_harness_skills", _REPO_ROOT / "scripts" / "sync_harness_skills.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_registry_targets_are_known(self) -> None:
        mod = self._sync_module()
        targets = set(mod.HARNESS_TARGETS)
        self.assertIn("claude", targets)
        self.assertIn("codex", targets)
        self.assertTrue(targets <= _PROJECTION_TARGETS, targets)
        for t, layout in mod.HARNESS_TARGETS.items():
            self.assertIn("label", layout)
            self.assertIn("skills_dir", layout)
            self.assertIn("plugin_dir", layout)

    def test_artifacts_only_for_registered_targets(self) -> None:
        from yule_engineering.agents.harness import load_grant_table

        mod = self._sync_module()
        artifacts = mod.build_artifacts(load_grant_table())
        prefixes = tuple(
            layout["skills_dir"] for layout in mod.HARNESS_TARGETS.values()
        ) + tuple(layout["plugin_dir"] for layout in mod.HARNESS_TARGETS.values())
        for rel in artifacts:
            self.assertTrue(rel.startswith(prefixes), f"stray artifact target: {rel}")


class TaxonomyDocsTests(unittest.TestCase):
    def test_docs_exist_with_key_sections(self) -> None:
        tax = (_REPO_ROOT / "docs" / "plugin-taxonomy.md").read_text(encoding="utf-8")
        mat = (_REPO_ROOT / "docs" / "provider-capability-matrix.md").read_text(encoding="utf-8")
        for needle in ("backend (runner)", "harness plugin", "Ollama 는 backend", "Gemini projection"):
            self.assertIn(needle, tax, needle)
        for needle in ("capability × provider", "Ollama", "공백", "Gemini"):
            self.assertIn(needle, mat, needle)


if __name__ == "__main__":
    unittest.main()
