"""`/provider` operator surface — setup UX over the provider_ops engine.

Proves the operator can actually set the primary provider (persisted), that no-config
is honest setup-required (and explicitly NOT implicit-ollama), that live vs
unsupported_in_console is shown, and that the command routes.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.policy import provider_ops as ops
from forgekit_console.policy import provider_surface as ps


class ApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.path = self.tmp / "config.json"

    def test_set_primary_persists(self) -> None:
        ok, msg = ps.apply_set_primary("ollama", path=self.path)
        self.assertTrue(ok, msg)
        self.assertEqual(ops.load_raw_config(path=self.path)["primary_provider"], "ollama")

    def test_set_unknown_fails(self) -> None:
        ok, msg = ps.apply_set_primary("nope", path=self.path)
        self.assertFalse(ok)
        self.assertIn("알 수 없는 provider", msg)
        self.assertFalse(self.path.exists())

    def test_set_cli_provider_notes_unsupported(self) -> None:
        ok, msg = ps.apply_set_primary("claude", path=self.path)
        self.assertTrue(ok)
        self.assertIn("unsupported_in_console", msg)   # honest — claude can't live-submit


class SurfaceTests(unittest.TestCase):
    def test_no_config_is_setup_required_not_implicit_ollama(self) -> None:
        lines = "\n".join(ps.provider_status_lines({}))
        self.assertIn("setup-required", lines)
        self.assertIn("자동 Ollama 사용 안 함", lines)    # kills the implicit-ollama misconception

    def test_list_shows_live_vs_unsupported(self) -> None:
        lines = "\n".join(ps.provider_list_lines({}))
        self.assertIn("ollama", lines)
        self.assertIn("live", lines)
        self.assertIn("unsupported_in_console", lines)    # claude/codex

    def test_status_after_primary(self) -> None:
        lines = "\n".join(ps.provider_status_lines({"primary_provider": "ollama"}))
        self.assertIn("primary brain : ollama", lines)
        self.assertIn("implicit local fallback: off", lines)
        # the brain-vs-transport split is surfaced (declared → actual per slot)
        self.assertIn("default_chat", lines)
        self.assertIn("actual ollama", lines)


class LinkRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.path = self.tmp / "config.json"
        ps.apply_set_primary("ollama", path=self.path)   # a base config

    def test_link_persists(self) -> None:
        ok, _ = ps.apply_link("gemini", path=self.path)
        self.assertTrue(ok)
        self.assertIn("gemini", ops.load_raw_config(path=self.path)["linked_providers"])

    def test_link_unknown_fails(self) -> None:
        self.assertFalse(ps.apply_link("nope", path=self.path)[0])

    def test_link_already_linked(self) -> None:
        ps.apply_link("gemini", path=self.path)
        self.assertFalse(ps.apply_link("gemini", path=self.path)[0])

    def test_unlink_primary_refused(self) -> None:
        ok, msg = ps.apply_unlink("ollama", path=self.path)
        self.assertFalse(ok)
        self.assertIn("primary", msg)

    def test_unlink_linked(self) -> None:
        ps.apply_link("gemini", path=self.path)
        ok, _ = ps.apply_unlink("gemini", path=self.path)
        self.assertTrue(ok)
        self.assertNotIn("gemini", ops.load_raw_config(path=self.path)["linked_providers"])

    def test_route_set_requires_linked(self) -> None:
        # claude not linked → refused
        self.assertFalse(ps.apply_route_set("research", "claude", path=self.path)[0])

    def test_route_set_and_clear(self) -> None:
        ps.apply_link("gemini", path=self.path)
        ok, _ = ps.apply_route_set("research", "gemini", path=self.path)
        self.assertTrue(ok)
        self.assertEqual(ops.load_raw_config(path=self.path)["slot_routing"]["research"], "gemini")
        ps.apply_route_clear("research", path=self.path)
        self.assertNotIn("research", ops.load_raw_config(path=self.path).get("slot_routing", {}))

    def test_unknown_slot_refused(self) -> None:
        self.assertFalse(ps.apply_route_set("nonsense", "ollama", path=self.path)[0])

    def test_route_show_lists_slots(self) -> None:
        lines = "\n".join(ps.route_show_lines({"primary_provider": "ollama"}))
        self.assertIn("default_chat", lines)
        self.assertIn("implicit_local", lines)


class RouteShowResolutionTests(unittest.TestCase):
    """`/provider route show` must resolve EACH slot to its ACTUAL live provider via the
    explicit fallback — not print a bare `unsupported_in_console` on every CLI-declared
    work slot. This is the non-chat slot fallback / declared-vs-actual confusion the lane
    closes: an operator must see that `execution → codex` actually reaches gemini."""

    def test_four_brain_work_slots_show_fallback_to_live(self) -> None:
        fb = ops.preset_four_brain({})
        lines = ps.route_show_lines(fb)
        joined = "\n".join(lines)
        # execution DECLARES codex (CLI, routing-only) but its fallback is [codex, gemini, ollama]
        # → the surface must resolve it to the live transport, NOT leave it looking broken.
        self.assertTrue(any("execution" in l and "codex → gemini" in l for l in lines),
                        f"execution slot should resolve codex→gemini fallback:\n{joined}")
        self.assertTrue(any("safety" in l and "claude → gemini" in l for l in lines),
                        f"safety slot should resolve claude→gemini fallback:\n{joined}")
        # default_chat is the one true live submit slot → marked ● and resolves to gemini.
        self.assertTrue(any(l.lstrip().startswith("●") and "default_chat" in l and "gemini" in l
                            for l in lines), f"default_chat should be ● live gemini:\n{joined}")
        # no work slot may be left showing a bare unsupported word when a live fallback exists.
        self.assertNotIn("unsupported_in_console", joined)
        self.assertNotIn("(live 경로 없음)", joined)

    def test_no_live_fallback_is_honest_no_path_with_next_action(self) -> None:
        # safety declared claude with a fallback of ONLY routing-only brains → genuinely no live path.
        cfg = {
            "primary_provider": "claude",
            "linked_providers": ["claude", "codex"],
            "slot_routing": {"safety": "claude"},
            "fallback_policy": {"slot_fallback_orders": {"safety": ["claude", "codex"]}},
        }
        lines = [l for l in ps.route_show_lines(cfg) if "safety" in l]
        self.assertTrue(lines, "safety slot line missing")
        line = lines[0]
        self.assertIn("(live 경로 없음)", line)               # honest: no faked live
        self.assertIn("/provider route set safety", line)     # actionable next step (no dead-end)
        self.assertTrue(line.lstrip().startswith("○"))

    def test_setup_required_when_no_primary(self) -> None:
        lines = "\n".join(ps.route_show_lines({}))
        self.assertIn("setup-required", lines)
        self.assertIn("/provider set", lines)

    def test_legend_separates_chat_vs_nonchat(self) -> None:
        joined = "\n".join(ps.route_show_lines(ops.preset_four_brain({})))
        # the surface explicitly tells the operator chat = live submit, others = routing declaration.
        self.assertIn("live submit", joined)
        self.assertIn("routing 선언", joined)
        self.assertIn("routing-only", joined)   # claude/codex framed as brain participants, honestly


class VerifiedLiveTests(unittest.TestCase):
    """Routing surfaces must distinguish probe-VERIFIED live from mere transport capability.

    The lane forbids fake-live: `/provider route show` and `/provider` must not assert bare
    "live" from openai-compat capability alone (gemini needs a key, ollama needs a daemon).
    A probe-backed `live_map` upgrades to live(검증됨) or honestly downgrades to 미검증."""

    def test_unprobed_never_claims_bare_live(self) -> None:
        # live_map=None → capable but unproven; must say 미검증, never a bare "live" verdict.
        lines = ps.route_show_lines(ops.preset_four_brain({}))
        joined = "\n".join(lines)
        self.assertIn("live-capable", joined)
        self.assertIn("미검증", joined)
        dc = next(l for l in lines if "default_chat" in l and "gemini" in l)
        self.assertNotIn("· 검증됨", dc)            # no fake verified-live without a probe

    def test_probe_verified_shows_live_checked(self) -> None:
        lm = {"gemini": True, "ollama": True, "claude": False, "codex": False}
        lines = ps.route_show_lines(ops.preset_four_brain({}), live_map=lm)
        dc = next(l for l in lines if "default_chat" in l and "gemini" in l)
        self.assertTrue(dc.lstrip().startswith("●"))
        self.assertIn("검증됨", dc)
        # fallback work slot (execution: codex→gemini) inherits gemini's verified state.
        ex = next(l for l in lines if "execution" in l)
        self.assertIn("codex → gemini", ex)
        self.assertIn("검증됨", ex)

    def test_probe_unreachable_is_honest_not_live(self) -> None:
        # gemini declared live-capable but probe says NOT reachable/authed → ○, not live.
        lm = {"gemini": False, "ollama": False}
        lines = ps.route_show_lines(ops.preset_four_brain({}), live_map=lm)
        dc = next(l for l in lines if "default_chat" in l and "gemini" in l)
        self.assertTrue(dc.lstrip().startswith("○"))
        self.assertIn("연결/인증 필요", dc)
        self.assertNotIn("· 검증됨", dc)

    def test_status_splits_verified_vs_capable(self) -> None:
        lines = ps.provider_status_lines(ops.preset_four_brain({}), live_map={"gemini": True, "ollama": False})
        joined = "\n".join(lines)
        verified = next(l for l in lines if "live(검증됨)" in l)
        capable = next(l for l in lines if "live-capable" in l)
        self.assertIn("gemini", verified)        # probe-verified
        self.assertNotIn("ollama", verified)     # ollama not verified → not listed as live
        self.assertIn("ollama", capable)         # capable-but-unverified

    def test_status_unprobed_says_probe_not_run(self) -> None:
        joined = "\n".join(ps.provider_status_lines(ops.preset_four_brain({})))
        self.assertIn("probe 안 함", joined)      # honest: we didn't verify live


class RoutingTests(unittest.TestCase):
    def test_provider_subcommands_route(self) -> None:
        from forgekit_console.commands.parser import parse_input
        from forgekit_console.commands.router import build_default_context, route

        ctx = build_default_context(Path("."))
        self.assertIn("provider list", "\n".join(route(parse_input("/provider list"), ctx).lines))
        self.assertIn("slot routing", "\n".join(route(parse_input("/provider route show"), ctx).lines))


class ProviderStateTaxonomyTests(unittest.TestCase):
    """The honest 5-state taxonomy (setup-required / configured / linked / live / unsupported).

    ``live`` is asserted ONLY from a verified probe (no fake-live); CLI brains (claude/codex)
    are ``unsupported`` for console live-submit even as configured participants."""

    def test_not_in_brain_is_setup_required(self) -> None:
        states = dict(ps.provider_state_map({}))               # empty config → every provider needs setup
        self.assertEqual(set(states.values()), {ps.STATE_SETUP_REQUIRED})

    def test_cli_primary_is_unsupported_not_faked_live(self) -> None:
        cfg = {"primary_provider": "claude", "linked_providers": ["claude"]}
        states = dict(ps.provider_state_map(cfg))
        self.assertEqual(states["claude"], ps.STATE_UNSUPPORTED)   # CLI brain, no console live — honest

    def test_openai_primary_without_probe_is_configured(self) -> None:
        cfg = {"primary_provider": "gemini", "linked_providers": ["gemini"]}
        states = dict(ps.provider_state_map(cfg))                  # unprobed → never "live"
        self.assertEqual(states["gemini"], ps.STATE_CONFIGURED)

    def test_linked_participant_is_linked(self) -> None:
        cfg = {"primary_provider": "gemini", "linked_providers": ["gemini", "ollama"]}
        states = dict(ps.provider_state_map(cfg))
        self.assertEqual(states["ollama"], ps.STATE_LINKED)

    def test_live_only_from_verified_probe(self) -> None:
        cfg = {"primary_provider": "gemini", "linked_providers": ["gemini", "ollama"]}
        states = dict(ps.provider_state_map(cfg, live_map={"ollama": True, "gemini": False}))
        self.assertEqual(states["ollama"], ps.STATE_LIVE)         # verified → live
        self.assertEqual(states["gemini"], ps.STATE_CONFIGURED)   # not verified → no fake-live

    def test_every_state_is_one_of_five(self) -> None:
        cfg = {"primary_provider": "claude", "linked_providers": ["claude", "gemini", "ollama"]}
        states = dict(ps.provider_state_map(cfg, live_map={"ollama": True}))
        for pid, state in states.items():
            self.assertIn(state, ps.PROVIDER_STATES, f"{pid} → {state} not a known state")


if __name__ == "__main__":
    unittest.main()
