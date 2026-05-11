"""PluginRegistry tests (F11 / #102 MVP)."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.extension.manifest import HookEvent, PluginManifest
from yule_orchestrator.agents.extension.plugin_registry import PluginRegistry


def _make(plugin_id: str, hooks_provided=()) -> PluginManifest:
    return PluginManifest(
        id=plugin_id,
        name=plugin_id.title(),
        version="0.1.0",
        kind="guard",
        hooks_provided=tuple(hooks_provided),
        hooks_consumed=(),
        env_keys=(),
        autonomy_level="advisory",
        paste_guard_required=True,
        risk_class="LOW",
        module_path="",
    )


class PluginRegistryTests(unittest.TestCase):
    def test_register_and_get_round_trip(self) -> None:
        reg = PluginRegistry()
        m = _make("paste-guard", ("OUTBOUND_LLM",))
        reg.register(m)
        self.assertIs(reg.get("paste-guard"), m)
        self.assertIn("paste-guard", reg)
        self.assertEqual(len(reg), 1)

    def test_get_missing_raises_key_error(self) -> None:
        reg = PluginRegistry()
        with self.assertRaises(KeyError):
            reg.get("nope")

    def test_duplicate_registration_raises(self) -> None:
        reg = PluginRegistry()
        reg.register(_make("paste-guard", ("OUTBOUND_LLM",)))
        with self.assertRaises(ValueError):
            reg.register(_make("paste-guard", ("OUTBOUND_LLM",)))

    def test_plugins_for_hook_returns_providers_only(self) -> None:
        reg = PluginRegistry()
        reg.register(_make("paste-guard", ("OUTBOUND_LLM", "OUTBOUND_DISCORD")))
        reg.register(_make("hookify", ("PREFLIGHT",)))
        reg.register(_make("repo-map", ("PREFLIGHT",)))
        outbound = reg.plugins_for_hook(HookEvent.OUTBOUND_LLM)
        self.assertEqual([m.id for m in outbound], ["paste-guard"])
        preflight = reg.plugins_for_hook(HookEvent.PREFLIGHT)
        self.assertEqual([m.id for m in preflight], ["hookify", "repo-map"])

    def test_plugins_for_hook_requires_hook_event(self) -> None:
        reg = PluginRegistry()
        with self.assertRaises(TypeError):
            reg.plugins_for_hook("PREFLIGHT")  # type: ignore[arg-type]

    def test_register_validates_manifest(self) -> None:
        reg = PluginRegistry()
        bogus = PluginManifest(
            id="BAD ID",
            name="x",
            version="0.1.0",
            kind="guard",
            module_path="",
        )
        with self.assertRaises(Exception):
            reg.register(bogus)

    def test_all_is_sorted_by_id(self) -> None:
        reg = PluginRegistry()
        reg.register(_make("zeta"))
        reg.register(_make("alpha"))
        self.assertEqual([m.id for m in reg.all()], ["alpha", "zeta"])

    def test_contains_handles_non_string(self) -> None:
        reg = PluginRegistry()
        reg.register(_make("paste-guard"))
        self.assertFalse(123 in reg)
        self.assertTrue("paste-guard" in reg)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
