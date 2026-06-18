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
        self.assertIn("primary : ollama", lines)
        self.assertIn("implicit local fallback: off", lines)


class RoutingTests(unittest.TestCase):
    def test_provider_command_routes(self) -> None:
        from forgekit_console.commands.parser import parse_input
        from forgekit_console.commands.router import build_default_context, route

        ctx = build_default_context(Path("."))
        r = route(parse_input("/provider list"), ctx)
        self.assertIn("provider list", "\n".join(r.lines))


if __name__ == "__main__":
    unittest.main()
