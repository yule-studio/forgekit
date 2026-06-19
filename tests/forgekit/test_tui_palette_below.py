"""Slash palette geometry — the command list opens DIRECTLY BELOW the input bar.

The correction: the operator wants the slash command list flush UNDER the chat bar
(Claude), inside the bottom-docked composer zone — NOT above it, NOT in the transcript,
and visible the instant `/` is pressed without any scroll. These are geometry assertions
(real region measurement), per the acceptance criteria.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

_TEXTUAL = importlib.util.find_spec("textual") is not None


def _app():
    from forgekit_console.commands.registry import load_agents, load_commands
    from forgekit_console.commands.router import ConsoleContext
    from forgekit_console.tui.app import ForgekitConsoleApp
    ctx = ConsoleContext(repo_root=Path("/tmp/repo"), agents=load_agents(), commands=load_commands())
    return ForgekitConsoleApp(repo_root=Path("/tmp/repo"), context=ctx)


@unittest.skipUnless(_TEXTUAL, "textual 필요")
class PaletteBelowGeometryTests(unittest.IsolatedAsyncioTestCase):
    async def test_input_bottom_anchored_before_slash(self):
        from forgekit_console.tui.composer import Composer
        app = _app()
        async with app.run_test(size=(100, 28)) as pilot:
            await pilot.pause()
            comp = app.query_one(Composer).region
            self.assertGreaterEqual(comp.bottom, app.size.height - 1)   # composer docked at bottom

    async def test_palette_flush_below_input_and_visible(self):
        from forgekit_console.tui.palette import CommandPalette
        app = _app()
        async with app.run_test(size=(100, 28)) as pilot:
            await pilot.pause()
            await pilot.press("slash", "h", "e")
            await pilot.pause()
            H = app.size.height
            bar = app.query_one("#composer-input-shell").region
            pal = app.query_one(CommandPalette).region
            self.assertTrue(app._palette.is_open)
            self.assertGreaterEqual(pal.y, bar.bottom)            # 2: palette.top >= input.bottom
            self.assertLessEqual(pal.y - bar.bottom, 1)           # 2: gap is 0~1 rows (flush)
            self.assertTrue(0 <= bar.y and bar.bottom <= H)       # 3: input in viewport
            self.assertTrue(0 <= pal.y and pal.bottom <= H)       # 3: palette in viewport

    async def test_palette_in_composer_zone_not_transcript(self):
        from forgekit_console.tui.composer import Composer
        from forgekit_console.tui.palette import CommandPalette
        from forgekit_console.tui.session_flow import SessionFlow
        app = _app()
        async with app.run_test(size=(100, 28)) as pilot:
            await pilot.pause()
            await pilot.press("slash", "h")
            await pilot.pause()
            pal = app.query_one(CommandPalette)
            # 4 (responsibility split): palette's parent is the Composer, NOT the SessionFlow
            self.assertIsInstance(pal.parent, Composer)
            flow = app.query_one(SessionFlow).region
            self.assertGreaterEqual(pal.region.y, flow.bottom)    # below the transcript zone

    async def test_long_transcript_palette_still_flush_below_input_no_scroll(self):
        from forgekit_console.tui.palette import CommandPalette
        app = _app()
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            for i in range(120):
                app._transcript.write(f"history line {i} ....................")
            await pilot.pause()
            # open WITHOUT any manual scroll
            await pilot.press("slash", "p")
            await pilot.pause()
            H = app.size.height
            bar = app.query_one("#composer-input-shell").region
            pal = app.query_one(CommandPalette).region
            # 5: first open shows it immediately, flush below input, fully in viewport
            self.assertGreaterEqual(pal.y, bar.bottom)
            self.assertLessEqual(pal.y - bar.bottom, 1)
            self.assertLessEqual(pal.bottom, H)
            self.assertGreaterEqual(pal.y, 0)


if __name__ == "__main__":
    unittest.main()
