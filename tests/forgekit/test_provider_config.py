"""Multi-provider config contract + migration (forgekit brain).

Proves the new schema parses, the legacy single-provider config migrates without
breaking, and the multi-provider invariants are actually enforced (primary linked,
slot targets linked, fallback entries linked, implicit-local-fallback off by default).
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.policy import provider_config as pc


class LoadTests(unittest.TestCase):
    def test_full_schema_parses(self) -> None:
        cfg = pc.load_provider_config({
            "primary_provider": "claude",
            "linked_providers": ["claude", "codex", "gemini", "ollama"],
            "model_overrides": {"ollama": "gemma3:latest"},
            "slot_routing": {"research": "gemini", "execution": "codex"},
            "fallback_policy": {"implicit_local_fallback": True,
                                 "slot_fallback_orders": {"default_chat": ["claude", "gemini"]}},
            "budget_policy": {"per_provider": {"ollama": "local"}},
        })
        self.assertEqual(cfg.primary_provider, "claude")
        self.assertTrue(cfg.is_multi)
        self.assertEqual(cfg.slot_target("research"), "gemini")
        self.assertEqual(cfg.slot_target("synthesis"), "claude")  # unset → primary
        self.assertEqual(cfg.fallback_order("default_chat"), ("claude", "gemini"))
        self.assertTrue(cfg.implicit_local_fallback)
        self.assertEqual(cfg.model_for("ollama"), "gemma3:latest")
        self.assertFalse(cfg.migrated_from_legacy)

    def test_implicit_local_fallback_off_by_default(self) -> None:
        cfg = pc.load_provider_config({"primary_provider": "claude", "linked_providers": ["claude"]})
        self.assertFalse(cfg.implicit_local_fallback)  # OFF unless explicitly enabled

    def test_legacy_main_provider_migrates(self) -> None:
        cfg = pc.load_provider_config({"main_provider": "ollama", "model": "gemma3:latest"})
        self.assertEqual(cfg.primary_provider, "ollama")
        self.assertEqual(cfg.linked_providers, ("ollama",))
        self.assertTrue(cfg.migrated_from_legacy)
        self.assertFalse(cfg.implicit_local_fallback)  # migration never enables implicit

    def test_legacy_id_field_migrates(self) -> None:
        cfg = pc.load_provider_config({"id": "claude"})
        self.assertEqual(cfg.primary_provider, "claude")
        self.assertTrue(cfg.migrated_from_legacy)

    def test_primary_forced_into_linked(self) -> None:
        cfg = pc.load_provider_config({"primary_provider": "claude", "linked_providers": ["gemini"]})
        self.assertIn("claude", cfg.linked_providers)


class HasConfigTests(unittest.TestCase):
    def test_empty_is_not_configured(self) -> None:
        self.assertFalse(pc.has_brain_config(None))
        self.assertFalse(pc.has_brain_config({}))

    def test_new_and_legacy_are_configured(self) -> None:
        self.assertTrue(pc.has_brain_config({"primary_provider": "claude"}))
        self.assertTrue(pc.has_brain_config({"main_provider": "ollama"}))
        self.assertTrue(pc.has_brain_config({"linked_providers": ["gemini"]}))


class ValidateTests(unittest.TestCase):
    def test_valid_config(self) -> None:
        cfg = pc.load_provider_config({"primary_provider": "claude",
                                       "linked_providers": ["claude", "gemini"],
                                       "slot_routing": {"research": "gemini"}})
        self.assertEqual(pc.validate_provider_config(cfg), ())

    def test_missing_primary(self) -> None:
        cfg = pc.load_provider_config({"linked_providers": []})
        errs = pc.validate_provider_config(cfg)
        self.assertTrue(any("primary_provider" in e for e in errs))

    def test_slot_target_must_be_linked(self) -> None:
        # research routed to gemini but gemini not linked → error
        cfg = pc.load_provider_config({"primary_provider": "claude", "linked_providers": ["claude"],
                                       "slot_routing": {"research": "gemini"}})
        errs = pc.validate_provider_config(cfg)
        self.assertTrue(any("research" in e and "gemini" in e for e in errs))

    def test_unknown_slot_rejected(self) -> None:
        cfg = pc.load_provider_config({"primary_provider": "claude", "linked_providers": ["claude"],
                                       "slot_routing": {"nonsense": "claude"}})
        self.assertTrue(any("nonsense" in e for e in pc.validate_provider_config(cfg)))

    def test_fallback_entry_must_be_linked(self) -> None:
        cfg = pc.load_provider_config({"primary_provider": "claude", "linked_providers": ["claude"],
                                       "fallback_policy": {"slot_fallback_orders": {"default_chat": ["gemini"]}}})
        self.assertTrue(any("fallback" in e and "gemini" in e for e in pc.validate_provider_config(cfg)))

    def test_unknown_linked_provider_rejected(self) -> None:
        cfg = pc.load_provider_config({"primary_provider": "mystery", "linked_providers": ["mystery"]})
        self.assertTrue(any("mystery" in e for e in pc.validate_provider_config(cfg)))


if __name__ == "__main__":
    unittest.main()
