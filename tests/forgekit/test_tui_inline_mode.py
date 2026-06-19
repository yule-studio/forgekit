"""Inline UI mode — full-screen (alt-screen) vs inline (terminal-flow).

AUDIT (verified against textual 8.2.7): the default ``App.run()`` enters the
alternate screen + captures the mouse; ``App.run(inline=True, mouse=False)`` uses the
LinuxInlineDriver which does NOT enter the alt-screen and honours ``mouse=False`` — so
inline mode preserves native terminal scrollback and lets the terminal own drag-select.

These tests lock the pure mode chooser, the entrypoint wiring (the right ``App.run``
kwargs are passed), and the bounded inline layout (palette/help/composer survive). The
real-TTY inline RENDER cannot be exercised headlessly — that boundary is documented.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

_TEXTUAL = importlib.util.find_spec("textual") is not None


# --------------------------------------------------------------------------- #
# pure: mode resolution + run kwargs
# --------------------------------------------------------------------------- #
class UiModeTests(unittest.TestCase):
    def test_cli_flag_wins(self):
        from forgekit_console.tui import ui_mode as uim
        self.assertEqual(uim.resolve_ui_mode({"FORGEKIT_UI_MODE": "full"}, cli="inline"), "inline")
        self.assertEqual(uim.resolve_ui_mode({"FORGEKIT_UI_MODE": "inline"}, cli="full"), "full")

    def test_env_and_default(self):
        from forgekit_console.tui import ui_mode as uim
        self.assertEqual(uim.resolve_ui_mode({"FORGEKIT_UI_MODE": "full"}), "full")
        self.assertEqual(uim.resolve_ui_mode({}), "inline")            # bare forgekit = inline (default)
        self.assertEqual(uim.resolve_ui_mode({"FORGEKIT_UI_MODE": "auto"}), "inline")  # terminal-native default
        self.assertEqual(uim.resolve_ui_mode({"FORGEKIT_UI_MODE": "inline"}), "inline")

    def test_run_kwargs_inline_avoids_altscreen_and_mouse(self):
        from forgekit_console.tui import ui_mode as uim
        kw = uim.run_kwargs("inline")
        self.assertTrue(kw["inline"])
        self.assertTrue(kw["inline_no_clear"])
        self.assertFalse(kw["mouse"])           # terminal owns selection
        self.assertEqual(uim.run_kwargs("full"), {})


# --------------------------------------------------------------------------- #
# entrypoint wiring: launch_console passes the right kwargs + inline flag
# --------------------------------------------------------------------------- #
@unittest.skipUnless(_TEXTUAL, "textual 필요")
class LaunchWiringTests(unittest.TestCase):
    def _capture(self, argv):
        from forgekit_console.app import main as appmain
        from forgekit_console.tui.app import ForgekitConsoleApp

        seen = {}
        orig_run = ForgekitConsoleApp.run

        def fake_run(self, **kwargs):
            seen["kwargs"] = kwargs
            seen["inline_flag"] = self._ui_inline
            return 0

        ForgekitConsoleApp.run = fake_run
        try:
            appmain.main(argv)
        finally:
            ForgekitConsoleApp.run = orig_run
        return seen

    def test_inline_flag_runs_inline(self):
        seen = self._capture(["--inline"])
        self.assertTrue(seen["kwargs"].get("inline"))
        self.assertFalse(seen["kwargs"].get("mouse"))
        self.assertTrue(seen["inline_flag"])

    def test_default_runs_inline(self):
        # bare forgekit = inline (Claude-Code-style terminal-native default)
        seen = self._capture([])
        self.assertTrue(seen["kwargs"].get("inline"))
        self.assertFalse(seen["kwargs"].get("mouse"))
        self.assertTrue(seen["inline_flag"])

    def test_full_flag_is_the_escape_hatch(self):
        seen = self._capture(["--full"])
        self.assertEqual(seen["kwargs"], {})
        self.assertFalse(seen["inline_flag"])


# --------------------------------------------------------------------------- #
# layout: inline bounds the flow + compact intro; surfaces survive
# --------------------------------------------------------------------------- #
@unittest.skipUnless(_TEXTUAL, "textual 필요")
class InlineLayoutTests(unittest.IsolatedAsyncioTestCase):
    def _app(self, inline):
        from forgekit_console.commands.registry import load_agents, load_commands
        from forgekit_console.commands.router import ConsoleContext
        from forgekit_console.tui.app import ForgekitConsoleApp
        ctx = ConsoleContext(repo_root=Path("/tmp/repo"), agents=load_agents(), commands=load_commands())
        return ForgekitConsoleApp(repo_root=Path("/tmp/repo"), context=ctx, inline=inline)

    async def test_inline_bounds_flow_full_uses_1fr(self):
        from forgekit_console.tui.session_flow import SessionFlow
        from forgekit_console.tui.header import IntroHeader
        from forgekit_console.tui import intro_state

        # inline → bounded flow + compact intro
        app = self._app(True)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            self.assertTrue(app.screen.has_class("-inline"))
            self.assertEqual(app.query_one(SessionFlow).region.height, 14)   # bounded
            self.assertEqual(app.query_one(IntroHeader).mode, intro_state.INTRO_COMPACT)  # no hero
        # full → flow fills (much taller than the inline cap)
        app2 = self._app(False)
        async with app2.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            self.assertFalse(app2.screen.has_class("-inline"))
            self.assertGreater(app2.query_one(SessionFlow).region.height, 14)

    async def test_inline_keeps_palette_help_composer_copy(self):
        from forgekit_console.tui.composer import Composer
        from forgekit_console.tui.palette import CommandPalette
        from forgekit_console.tui.session_flow import SessionFlow

        app = self._app(True)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            # multiline still works in inline (Ctrl+J newline)
            prompt = app.query_one("#prompt")
            prompt.focus()
            await pilot.press("a"); await pilot.press("ctrl+j"); await pilot.press("b")
            self.assertIn("\n", prompt.value)
            prompt.clear()
            for i in range(40):
                app._transcript.write(f"line {i}")   # overflow the bounded flow
            await pilot.pause()
            # palette opens directly below the docked input (unchanged in inline)
            await pilot.press("slash", "h", "e")
            await pilot.pause()
            bar = app.query_one("#composer-input-shell")
            self.assertGreaterEqual(app.query_one(CommandPalette).region.y, bar.region.bottom)
            self.assertTrue(app.query_one(SessionFlow).allow_vertical_scroll)   # single owner
            await pilot.press("escape")
            await pilot.pause()
            # help still switches the main view in inline
            app._execute("/help")
            await pilot.pause()
            self.assertTrue(app._main.help_open)


if __name__ == "__main__":
    unittest.main()
