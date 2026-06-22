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
