"""Runtime mode contract — mode cycle produces a REAL policy change (pure, CI-safe).

These prove the WT1 core claim: switching the runtime mode changes the resolved
EffectivePolicy (provider-policy mode, slot routing, usage throttle, approval,
autonomy) — not just a label — and that this holds the same way regardless of which
provider is main (vendor-neutral). Plus the setup gate (≥1 provider enforced).
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.policy import provider_policy as pp
from forgekit_console.policy import runtime_mode as rm
from forgekit_console.policy import usage_policy as up
from forgekit_console.policy import setup_state as ss
from forgekit_console.policy.main_profile import profile_for


class ModeRegistryTests(unittest.TestCase):
    def test_seven_modes_present(self) -> None:
        ids = [m.id for m in rm.all_modes()]
        for expected in (
            rm.MODE_INTERACTIVE, rm.MODE_DELIVERY, rm.MODE_RESEARCH, rm.MODE_WATCH,
            rm.MODE_ALWAYS_ON, rm.MODE_COST_SAVE, rm.MODE_APPROVAL_WAIT,
        ):
            self.assertIn(expected, ids)

    def test_cycle_wraps_both_directions(self) -> None:
        first = rm.RUNTIME_MODES[0].id
        last = rm.RUNTIME_MODES[-1].id
        self.assertEqual(rm.cycle_mode(first, -1), last)        # wrap back
        self.assertEqual(rm.cycle_mode(last, 1), first)         # wrap forward
        self.assertEqual(rm.cycle_mode("nonexistent", 1), rm.RUNTIME_MODES[1].id)

    def test_cycle_visits_every_mode_once_per_loop(self) -> None:
        seen, cur = [], rm.DEFAULT_MODE
        for _ in range(len(rm.RUNTIME_MODES)):
            seen.append(cur)
            cur = rm.cycle_mode(cur, 1)
        self.assertEqual(sorted(seen), sorted(m.id for m in rm.RUNTIME_MODES))


class EffectivePolicyTests(unittest.TestCase):
    def _profile(self, main="claude"):
        return profile_for(main)

    def test_cost_save_vs_research_differ_in_real_policy(self) -> None:
        prof = self._profile("claude")
        cost = rm.resolve_effective_policy(prof, rm.MODE_COST_SAVE)
        research = rm.resolve_effective_policy(prof, rm.MODE_RESEARCH)
        # provider-policy mode actually differs (strict-single vs optimized)
        self.assertEqual(cost.provider_policy_mode, pp.POLICY_STRICT_SINGLE)
        self.assertEqual(research.provider_policy_mode, pp.POLICY_OPTIMIZED)
        # usage throttle posture differs (cost-save forces strict)
        self.assertEqual(cost.usage.usage_mode, up.USAGE_STRICT)
        self.assertNotEqual(research.usage.usage_mode, up.USAGE_STRICT)
        # budget posture differs
        self.assertEqual(cost.budget_posture, rm.BUDGET_STRICT)
        self.assertEqual(research.budget_posture, rm.BUDGET_RELAXED)

    def test_approval_wait_holds_all_actions(self) -> None:
        pol = rm.resolve_effective_policy(self._profile(), rm.MODE_APPROVAL_WAIT)
        self.assertTrue(pol.holds_all_actions())
        self.assertEqual(pol.autonomy, rm.AUTONOMY_MANUAL)

    def test_watch_is_observe_only_and_loops(self) -> None:
        pol = rm.resolve_effective_policy(self._profile(), rm.MODE_WATCH)
        self.assertEqual(pol.autonomy, rm.AUTONOMY_OBSERVE)
        self.assertTrue(pol.background_loop)

    def test_always_on_is_bounded_not_unbounded(self) -> None:
        pol = rm.resolve_effective_policy(self._profile(), rm.MODE_ALWAYS_ON)
        self.assertEqual(pol.autonomy, rm.AUTONOMY_BOUNDED)
        self.assertTrue(pol.background_loop)
        # destructive actions are gated behind approval even in the long-running mode
        self.assertEqual(pol.approval, rm.APPROVAL_DESTRUCTIVE)

    def test_vendor_neutral_same_mode_rules_across_main_providers(self) -> None:
        # the MODE rules are identical regardless of which provider is main; only the
        # resolved provider id in the slots changes.
        for main in ("claude", "codex", "gemini", "ollama"):
            pol = rm.resolve_effective_policy(profile_for(main), rm.MODE_COST_SAVE)
            self.assertEqual(pol.provider_policy_mode, pp.POLICY_STRICT_SINGLE)
            self.assertEqual(pol.usage.usage_mode, up.USAGE_STRICT)
            # strict-single → every slot is the main provider
            self.assertEqual(pol.routing_target(), main)

    def test_main_provider_changes_routing_target(self) -> None:
        a = rm.resolve_effective_policy(profile_for("claude"), rm.MODE_INTERACTIVE)
        b = rm.resolve_effective_policy(profile_for("ollama"), rm.MODE_INTERACTIVE)
        self.assertEqual(a.routing_target(), "claude")
        self.assertEqual(b.routing_target(), "ollama")
        # and the profile-derived policy mode can differ by provider
        self.assertEqual(b.provider_policy_mode, pp.POLICY_STRICT_SINGLE)  # ollama default


class SetupGateTests(unittest.TestCase):
    def test_no_provider_is_setup_required(self) -> None:
        state = ss.resolve_setup_state({})
        self.assertTrue(state.blocked)
        self.assertEqual(state.state, ss.STATE_SETUP_REQUIRED)
        self.assertTrue(state.next_actions)
        self.assertFalse(next(c for c in state.checks if c.name == "main provider").ok)

    def test_provider_present_is_ready(self) -> None:
        state = ss.resolve_setup_state({"main_provider": "claude"})
        self.assertTrue(state.ready)
        self.assertEqual(state.main_provider, "claude")
        self.assertIsNotNone(state.profile)
        self.assertTrue(next(c for c in state.checks if c.name == "main provider").ok)

    def test_ollama_ready_with_capability_warning(self) -> None:
        state = ss.resolve_setup_state({"main_provider": "ollama"})
        self.assertTrue(state.ready)
        self.assertTrue(state.next_actions)  # the limited-capability warning surfaces


if __name__ == "__main__":
    unittest.main()
