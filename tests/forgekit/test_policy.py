"""forgekit policy layer — slot resolution, main-provider defaults, usage policy."""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.policy import main_profile, provider_policy, usage_policy
from forgekit_console.policy.provider_policy import (
    ALL_SLOTS,
    POLICY_HYBRID,
    POLICY_OPTIMIZED,
    POLICY_STRICT_SINGLE,
    SLOT_EXECUTION,
    SLOT_RESEARCH,
    SLOT_SYNTHESIS,
    resolve_slots,
)


class StrictSingleTests(unittest.TestCase):
    def test_fills_every_slot_with_main(self) -> None:
        mapping = resolve_slots("claude", POLICY_STRICT_SINGLE)
        self.assertEqual(set(mapping), set(ALL_SLOTS))
        self.assertTrue(all(v == "claude" for v in mapping.values()))

    def test_strict_ignores_overrides(self) -> None:
        mapping = resolve_slots(
            "claude", POLICY_STRICT_SINGLE,
            overrides={SLOT_EXECUTION: "codex"}, available=("codex",),
        )
        self.assertEqual(mapping[SLOT_EXECUTION], "claude")


class HybridTests(unittest.TestCase):
    def test_uses_explicit_overrides(self) -> None:
        mapping = resolve_slots(
            "claude", POLICY_HYBRID,
            overrides={SLOT_EXECUTION: "codex"}, available=("codex",),
        )
        self.assertEqual(mapping[SLOT_EXECUTION], "codex")
        self.assertEqual(mapping[SLOT_RESEARCH], "claude")  # no override → main

    def test_hybrid_does_not_auto_pick(self) -> None:
        # research-capable gemini is available but hybrid never auto-picks.
        mapping = resolve_slots("claude", POLICY_HYBRID, available=("gemini",))
        self.assertEqual(mapping[SLOT_RESEARCH], "claude")

    def test_unavailable_override_falls_back_to_main(self) -> None:
        mapping = resolve_slots(
            "claude", POLICY_HYBRID, overrides={SLOT_EXECUTION: "codex"}, available=(),
        )
        self.assertEqual(mapping[SLOT_EXECUTION], "claude")


class OptimizedTests(unittest.TestCase):
    def test_auto_picks_by_capability(self) -> None:
        mapping = resolve_slots(
            "claude", POLICY_OPTIMIZED, available=("codex", "gemini"),
        )
        self.assertEqual(mapping[SLOT_EXECUTION], "codex")   # codex = execution
        self.assertEqual(mapping[SLOT_RESEARCH], "gemini")   # gemini = research
        self.assertEqual(mapping[SLOT_SYNTHESIS], "claude")  # claude = synthesis (main)

    def test_override_beats_autopick(self) -> None:
        mapping = resolve_slots(
            "claude", POLICY_OPTIMIZED,
            overrides={SLOT_EXECUTION: "gemini"}, available=("codex", "gemini"),
        )
        self.assertEqual(mapping[SLOT_EXECUTION], "gemini")

    def test_no_better_provider_keeps_main(self) -> None:
        mapping = resolve_slots("claude", POLICY_OPTIMIZED, available=())
        self.assertTrue(all(v == "claude" for v in mapping.values()))

    def test_unknown_mode_raises(self) -> None:
        with self.assertRaises(ValueError):
            resolve_slots("claude", "bogus")


class MainProfileTests(unittest.TestCase):
    def test_builtins_differ(self) -> None:
        claude = main_profile.profile_for("claude")
        codex = main_profile.profile_for("codex")
        gemini = main_profile.profile_for("gemini")
        ollama = main_profile.profile_for("ollama")
        self.assertEqual(claude.agent_lean, main_profile.LEAN_SYNTHESIS)
        self.assertEqual(codex.agent_lean, main_profile.LEAN_EXECUTION)
        self.assertEqual(gemini.agent_lean, main_profile.LEAN_RESEARCH)
        self.assertEqual(ollama.agent_lean, main_profile.LEAN_LOCAL_FIRST)
        # leans are distinct across the four
        leans = {claude.agent_lean, codex.agent_lean, gemini.agent_lean, ollama.agent_lean}
        self.assertEqual(len(leans), 4)

    def test_ollama_warns_and_strict_single(self) -> None:
        ollama = main_profile.profile_for("ollama")
        self.assertEqual(ollama.default_policy_mode, POLICY_STRICT_SINGLE)
        self.assertTrue(ollama.warnings)
        self.assertEqual(ollama.default_usage_mode, "local")

    def test_claude_usage_subscription(self) -> None:
        self.assertEqual(main_profile.profile_for("claude").default_usage_mode, "subscription")
        self.assertEqual(main_profile.profile_for("codex").default_usage_mode, "api")

    def test_unknown_provider_conservative(self) -> None:
        prof = main_profile.profile_for("mystery")
        self.assertEqual(prof.default_policy_mode, POLICY_STRICT_SINGLE)
        self.assertTrue(prof.warnings)


class UsagePolicyTests(unittest.TestCase):
    def test_default_usage_per_billing(self) -> None:
        sub = usage_policy.default_usage_policy("claude", "subscription")
        api = usage_policy.default_usage_policy("codex", "api")
        local = usage_policy.default_usage_policy("ollama", "local")
        self.assertEqual(sub.usage_mode, usage_policy.USAGE_SUBSCRIPTION_AWARE)
        self.assertEqual(api.usage_mode, usage_policy.USAGE_ADAPTIVE)
        self.assertEqual(local.usage_mode, usage_policy.USAGE_LOCAL_FIRST)

    def test_unknown_billing_raises(self) -> None:
        with self.assertRaises(ValueError):
            usage_policy.default_usage_policy("x", "bitcoin")

    def test_reserve_and_throttle(self) -> None:
        pol = usage_policy.UsagePolicy(
            usage_mode=usage_policy.USAGE_ADAPTIVE, billing_mode="api", reserve=0.2,
        )
        # reserve floor = 100 * (1 - 0.2) = 80
        self.assertEqual(pol.reserve_floor(100.0), 80.0)
        self.assertFalse(usage_policy.should_throttle(pol, 70.0, 100.0))
        self.assertTrue(usage_policy.should_throttle(pol, 85.0, 100.0))

    def test_strict_throttles_at_budget(self) -> None:
        pol = usage_policy.UsagePolicy(
            usage_mode=usage_policy.USAGE_STRICT, billing_mode="api", reserve=0.0,
        )
        self.assertFalse(usage_policy.should_throttle(pol, 99.0, 100.0))
        self.assertTrue(usage_policy.should_throttle(pol, 100.0, 100.0))

    def test_unbounded_budget_never_throttles_unless_strict(self) -> None:
        adaptive = usage_policy.default_usage_policy("ollama", "local")
        self.assertFalse(usage_policy.should_throttle(adaptive, 999.0, 0.0))


if __name__ == "__main__":
    unittest.main()
