"""Scroll model — single reading-flow owner, NO visible gutters, no nested content scroll.

Runtime audit (real widget properties, not CSS): the reading flow (SessionFlow) is the
ONLY content scroll owner; Transcript / Help / Palette / Composer never own scroll; the
input (PromptArea) has a bounded but GUTTER-LESS scroll (Claude-like). No widget draws a
visible vertical scrollbar gutter in either full or inline mode — the screen reads as a
terminal flow, not an internal app pane with scrollbars.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

_TEXTUAL = importlib.util.find_spec("textual") is not None


def _app(inline=False):
    from forgekit_console.commands.registry import load_agents, load_commands
    from forgekit_console.commands.router import ConsoleContext
    from forgekit_console.tui.app import ForgekitConsoleApp
    ctx = ConsoleContext(repo_root=Path("/tmp/repo"), agents=load_agents(), commands=load_commands())
    return ForgekitConsoleApp(repo_root=Path("/tmp/repo"), context=ctx, inline=inline)


def _gutter(w) -> int:
    return int(getattr(w.styles, "scrollbar_size_vertical", 0) or 0)


@unittest.skipUnless(_TEXTUAL, "textual 필요")
class ScrollModelTests(unittest.IsolatedAsyncioTestCase):
    async def _overflow(self, app, pilot):
        from forgekit_console.tui.prompt_area import PromptArea
        for i in range(80):
            app._transcript.write(f"line {i} " + "-" * 40)
        app.query_one("#prompt", PromptArea).value = "\n".join(f"in {i}" for i in range(20))
        await pilot.pause()

    async def _assert_clean_scroll(self, inline):
        from forgekit_console.tui.session_flow import SessionFlow
        from forgekit_console.tui.transcript import Transcript
        from forgekit_console.tui.help_panel import HelpPanel
        from forgekit_console.tui.composer import Composer
        from forgekit_console.tui.palette import CommandPalette
        from forgekit_console.tui.prompt_area import PromptArea

        app = _app(inline)
        async with app.run_test(size=(100, 28)) as pilot:
            await pilot.pause()
            await self._overflow(app, pilot)
            app._execute("/help")
            await pilot.pause()
            # NO widget draws a visible gutter (size>0 AND show) — anywhere, any mode
            for cls in (SessionFlow, Transcript, HelpPanel, Composer, CommandPalette, PromptArea):
                w = app.query_one(cls)
                visible_gutter = _gutter(w) > 0 and w.show_vertical_scrollbar
                self.assertFalse(visible_gutter, f"{cls.__name__} draws a visible gutter (inline={inline})")
            # content panes never own scroll — only SessionFlow (reading) + input may scroll
            self.assertFalse(app.query_one(Transcript).allow_vertical_scroll)
            self.assertFalse(app.query_one(HelpPanel).allow_vertical_scroll)
            self.assertFalse(app.query_one(CommandPalette).allow_vertical_scroll)
            self.assertFalse(app.query_one(Composer).allow_vertical_scroll)
            self.assertTrue(app.query_one(SessionFlow).allow_vertical_scroll)   # the one owner

    async def test_full_mode_clean(self):
        await self._assert_clean_scroll(False)

    async def test_inline_mode_clean(self):
        await self._assert_clean_scroll(True)

    async def test_input_scroll_is_bounded_and_gutterless(self):
        from forgekit_console.tui.prompt_area import PromptArea
        app = _app()
        async with app.run_test(size=(100, 28)) as pilot:
            await pilot.pause()
            p = app.query_one(PromptArea)
            self.assertEqual(_gutter(p), 0)                       # gutter-less
            self.assertEqual(int(p.styles.max_height.value), 12)  # bounded (never eats screen)


if __name__ == "__main__":
    unittest.main()
