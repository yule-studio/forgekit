"""Auto-recommend routing engine (forgekit brain advisor).

Every suggestion must carry a reason; suggestions are tiered (safe/tradeoff/blocked);
the engine never mutates config. Covers capability-aligned routing, unsupported-slot
blocked surface, budget-aware tradeoff, and the no-config case.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.policy import provider_config as pc
from forgekit_console.policy import recommend as rec
from forgekit_console.policy import runtime_mode as rm


def _cfg(**kw):
    base = {"primary_provider": "claude", "linked_providers": ["claude"]}
    base.update(kw)
    return pc.load_provider_config(base)


class RecommendTests(unittest.TestCase):
    def test_no_config_is_blocked(self) -> None:
        recs = rec.recommend(pc.load_provider_config({}), rm.MODE_INTERACTIVE)
        self.assertEqual(recs[0].tier, rec.TIER_BLOCKED)
        self.assertIn("미설정", recs[0].title)

    def test_safe_capability_routing(self) -> None:
        # codex + gemini linked but execution/research not routed to them → safe recs
        cfg = _cfg(linked_providers=["claude", "codex", "gemini"])
        recs = rec.recommend(cfg, rm.MODE_INTERACTIVE)
        safe = [r for r in recs if r.tier == rec.TIER_SAFE]
        slots = {r.slot: r.provider for r in safe}
        self.assertEqual(slots.get("execution"), "codex")
        self.assertEqual(slots.get("research"), "gemini")
        # every rec has a reason
        self.assertTrue(all(r.reason for r in recs))

    def test_unsupported_active_slot_is_blocked(self) -> None:
        # claude primary, interactive → default_chat=claude (no console submit) → blocked
        cfg = _cfg(linked_providers=["claude", "ollama"])
        recs = rec.recommend(cfg, rm.MODE_INTERACTIVE)
        blocked = [r for r in recs if r.tier == rec.TIER_BLOCKED]
        self.assertTrue(blocked)
        self.assertTrue(any("live" in r.title.lower() for r in blocked))
        # the blocked rec explains the limitation (not just "use X")
        self.assertTrue(any("live-submit" in r.reason for r in blocked))

    def test_budget_tradeoff(self) -> None:
        cfg = _cfg(linked_providers=["claude", "ollama"],
                   slot_routing={"default_chat": "claude"})
        recs = rec.recommend(cfg, rm.MODE_INTERACTIVE, budget_high_providers=("claude",))
        tradeoffs = [r for r in recs if r.tier == rec.TIER_TRADEOFF and r.slot == "default_chat"]
        self.assertTrue(tradeoffs)
        self.assertEqual(tradeoffs[0].provider, "ollama")

    def test_well_configured_has_no_safe_routing_churn(self) -> None:
        # everything routed to a capable provider → no safe capability recs
        cfg = _cfg(linked_providers=["claude", "codex", "gemini", "ollama"],
                   slot_routing={"execution": "codex", "research": "gemini",
                                 "synthesis": "claude", "safety": "claude",
                                 "compression": "ollama", "classification": "ollama",
                                 "default_chat": "ollama"})
        recs = rec.recommend(cfg, rm.MODE_INTERACTIVE)
        self.assertFalse([r for r in recs if r.tier == rec.TIER_SAFE])

    def test_render_lines_shows_reason(self) -> None:
        cfg = _cfg(linked_providers=["claude", "codex"])
        lines = rec.render_lines(rec.recommend(cfg, rm.MODE_INTERACTIVE))
        joined = "\n".join(lines)
        self.assertIn("suggestion-only", joined)
        self.assertIn("이유", joined)


if __name__ == "__main__":
    unittest.main()
