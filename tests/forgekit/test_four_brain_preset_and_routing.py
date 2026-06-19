"""CWT2/CWT3 guard — four-brain preset writes a real config + free-text routes to the
default_chat actual live lane (gemini), not the primary brain (claude).

CI-run / pure. Covers the two functional fixes:
- ``/provider preset four-brain`` persists primary=claude + 4 linked + slot_routing +
  fallback orders + model_overrides, and survives a reload (persistence);
- with the operator's slot_routing passed as overrides, the EffectivePolicy's
  routing_target resolves to the default_chat slot (gemini) — the submit head — so a
  free-text submit no longer heads to claude and dies as unsupported_in_console;
- claude/codex stay routing/brain participants (declared) while gemini/ollama are the
  live transport (actual).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401


class FourBrainPresetTests(unittest.TestCase):
    def _apply(self):
        from forgekit_provider.policy import provider_surface as ps
        home = Path(tempfile.mkdtemp())
        cfgp = home / "config.json"
        ok, msg = ps.apply_preset("four-brain", path=cfgp)
        return ok, msg, cfgp

    def test_preset_writes_real_config(self) -> None:
        ok, _, cfgp = self._apply()
        self.assertTrue(ok)
        cfg = json.loads(cfgp.read_text())
        self.assertEqual(cfg["primary_provider"], "claude")
        self.assertEqual(cfg["linked_providers"], ["claude", "codex", "gemini", "ollama"])
        self.assertEqual(cfg["slot_routing"]["default_chat"], "gemini")
        self.assertEqual(cfg["slot_routing"]["execution"], "codex")
        self.assertEqual(cfg["model_overrides"]["ollama"], "gemma3:latest")
        self.assertEqual(cfg["fallback_policy"]["slot_fallback_orders"]["default_chat"], ["gemini", "ollama"])
        self.assertFalse(cfg["fallback_policy"]["implicit_local_fallback"])

    def test_persists_across_reload(self) -> None:
        from forgekit_provider.policy import provider_config as pc, routing as rt
        _, _, cfgp = self._apply()
        # "restart" — reload from disk and resolve routing
        brain = pc.load_provider_config(json.loads(cfgp.read_text()))
        res = rt.resolve_routing(brain, pc.SLOT_DEFAULT_CHAT)
        self.assertEqual(res.declared_provider, "gemini")
        self.assertEqual(res.actual_provider, "gemini")
        self.assertTrue(res.is_live_capable)

    def test_unknown_preset_is_honest(self) -> None:
        from forgekit_provider.policy import provider_surface as ps
        ok, msg = ps.apply_preset("bogus", path=Path(tempfile.mkdtemp()) / "c.json")
        self.assertFalse(ok)
        self.assertIn("four-brain", msg)


class RoutingTargetHonorsSlotRoutingTests(unittest.TestCase):
    CFG = {
        "primary_provider": "claude",
        "linked_providers": ["claude", "codex", "gemini", "ollama"],
        "slot_routing": {"default_chat": "gemini", "execution": "codex", "safety": "claude"},
        "fallback_policy": {"slot_fallback_orders": {
            "default_chat": ["gemini", "ollama"], "execution": ["codex", "gemini", "ollama"]}},
    }

    def test_routing_target_is_default_chat_not_primary(self) -> None:
        from forgekit_provider.policy import runtime_mode as rm, provider_config as pc
        from forgekit_provider.policy.main_profile import profile_for

        brain = pc.load_provider_config(self.CFG)
        profile = profile_for(brain.primary_provider)
        # the fix: pass overrides=slot_routing + available=linked (as the app now does)
        pol = rm.resolve_effective_policy(
            profile, rm.MODE_INTERACTIVE,
            overrides=brain.slot_routing, available=brain.linked_providers)
        self.assertEqual(pol.routing_target(), "gemini")        # NOT "claude"
        self.assertNotEqual(pol.routing_target(), "claude")

    def test_submit_chain_head_is_live_lane(self) -> None:
        from forgekit_provider.policy import provider_config as pc, routing as rt
        brain = pc.load_provider_config(self.CFG)
        chain = rt.submit_chain(brain, pc.SLOT_DEFAULT_CHAT, prefer="gemini")
        self.assertEqual(chain[0], "gemini")        # head is the live lane, not claude
        self.assertIn("ollama", chain)              # explicit fallback present

    def test_claude_codex_remain_routing_participants(self) -> None:
        # execution declared=codex (routing participant) but actual falls to a live lane
        from forgekit_provider.policy import provider_config as pc, routing as rt
        brain = pc.load_provider_config(self.CFG)
        res = rt.resolve_routing(brain, "execution")
        self.assertEqual(res.declared_provider, "codex")   # codex is the declared brain
        self.assertTrue(res.is_live_capable)               # but a live provider actually answers
        self.assertIn(res.actual_provider, ("gemini", "ollama"))


if __name__ == "__main__":
    unittest.main()
