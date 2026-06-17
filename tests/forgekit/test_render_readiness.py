"""Render readiness surface — true-raster readiness, support matrix, /render.

Pure given an env mapping (reads image_renderer.diagnose_renderers + sys.version),
so these run without a terminal AND in the bare CI install (no textual/PIL/rich).
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.tui import image_renderer as ir
from forgekit_console.tui import render_readiness as rr


class SupportMatrixTests(unittest.TestCase):
    def test_matrix_covers_the_four_terminals(self) -> None:
        names = {row.terminal for row in rr.terminal_support_matrix()}
        self.assertIn("VS Code integrated", names)
        for t in ("iTerm2", "WezTerm", "Kitty"):
            self.assertIn(t, names)

    def test_vscode_is_fallback_iterm_kitty_are_recommended(self) -> None:
        rows = {row.terminal: row for row in rr.terminal_support_matrix()}
        self.assertFalse(rows["VS Code integrated"].recommended)
        self.assertEqual(rows["VS Code integrated"].expected_backend, "halfcell")
        self.assertTrue(rows["iTerm2"].recommended)
        self.assertTrue(rows["Kitty"].recommended)
        self.assertEqual(rows["Kitty"].expected_backend, "tgp")

    def test_recommended_terminals_constant(self) -> None:
        self.assertEqual(rr.RECOMMENDED_RASTER_TERMINALS, ("iTerm2", "WezTerm", "Kitty"))


class ReadinessReportTests(unittest.TestCase):
    def test_report_fields_coherent(self) -> None:
        rep = rr.render_readiness_report({"TERM_PROGRAM": "vscode"})
        self.assertIn(rep.avatar_backend, _ALL := (
            ir.BACKEND_TGP, ir.BACKEND_SIXEL, ir.BACKEND_AVATAR_MARK,
            ir.BACKEND_HALFBLOCK, ir.BACKEND_TEXT,
        ))
        self.assertEqual(rep.avatar_policy, ir.policy_state(rep.avatar_backend))
        # true_raster_ready iff python ok AND lib ok AND avatar is a real raster
        expected = rep.python_ok and rep.lib_ok and ir.is_true_raster(rep.avatar_backend)
        self.assertEqual(rep.true_raster_ready, expected)

    def test_non_raster_env_is_not_ready(self) -> None:
        # a plain terminal cannot do true raster → not ready, managed fallback
        rep = rr.render_readiness_report({"TERM": "xterm-256color"})
        self.assertFalse(rep.true_raster_ready)
        self.assertFalse(ir.is_true_raster(rep.avatar_backend))

    def test_readiness_lines_mention_recommendation_when_not_ready(self) -> None:
        lines = rr.render_readiness_lines(env={"TERM": "xterm-256color"})
        joined = "\n".join(lines)
        self.assertIn("readiness", joined)
        self.assertIn("iTerm2", joined)  # recommended terminal surfaced
        self.assertIn("managed fallback", joined)

    def test_separates_import_from_raster(self) -> None:
        # the report keeps lib_ok and true_raster_ready distinct (the whole point).
        rep = rr.render_readiness_report({"TERM_PROGRAM": "vscode"})
        if rep.lib_ok and not ir.is_true_raster(rep.avatar_backend):
            self.assertFalse(rep.true_raster_ready)  # importable ≠ raster


class RenderCommandRouteTests(unittest.TestCase):
    def test_slash_render_returns_readiness(self) -> None:
        from pathlib import Path

        from forgekit_console.commands.parser import parse_input
        from forgekit_console.commands.router import build_default_context, route

        res = route(parse_input("/render"), build_default_context(Path("/tmp")))
        self.assertEqual(res.kind, "info")
        joined = "\n".join(res.lines)
        self.assertIn("readiness", joined)
        self.assertIn("avatar", joined)
        self.assertIn("brand", joined)

    def test_render_command_registered(self) -> None:
        from forgekit_console.commands.registry import find_command, load_commands

        self.assertIsNotNone(find_command("render", load_commands()))


if __name__ == "__main__":
    unittest.main()
