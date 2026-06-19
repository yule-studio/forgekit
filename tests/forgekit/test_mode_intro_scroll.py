"""Mode-switch UX / intro lifecycle / internal-scroll guard (Claude parity round).

Real render checks via the Textual pilot:
- A: Shift+Tab cycling NEVER appends to the transcript (no mode-table flood) — the mode
     shows only in live surfaces (issue line + bottom hint) that replace in place.
- D: the bottom hint reflects the CURRENT runtime mode, not a fixed "operator" string.
- B: the IntroHeader lives INSIDE the SessionFlow and scrolls away as content accumulates
     (first-impression element, not permanent top chrome).
- C: only SessionFlow can scroll; no widget draws a visible vertical gutter.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

_TEXTUAL = importlib.util.find_spec("textual") is not None


def _inline_app():
    from forgekit_console.commands.registry import load_agents, load_commands
    from forgekit_console.commands.router import ConsoleContext
    from forgekit_console.tui.app import ForgekitConsoleApp
    ctx = ConsoleContext(repo_root=Path("/tmp/repo"), agents=load_agents(), commands=load_commands())
    return ForgekitConsoleApp(repo_root=Path("/tmp/repo"), context=ctx,
                              config={"primary_provider": "ollama", "linked_providers": ["ollama"]},
                              inline=True)


# --------------------------------------------------------------------------- #
# pure: hint + flash reflect the mode
# --------------------------------------------------------------------------- #
class HintReflectsModeTests(unittest.TestCase):
    def test_hint_line_shows_the_runtime_mode_not_fixed_operator(self) -> None:
        from forgekit_console.tui import render

        line = render.hint_line(mode_label="research")
        self.assertIn("research", line)
        self.assertNotIn("operator · /help", line)   # the old fixed string is gone
        # falls back to 'operator' only when no mode is resolved
        self.assertIn("operator", render.hint_line(mode_label=""))

    def test_mode_switch_flash_is_ephemeral_marker(self) -> None:
        from forgekit_console.tui import render

        flash = render.mode_switch_flash("repo-autopilot")
        self.assertIn("repo-autopilot", flash)
        self.assertIn("mode on", flash)


# --------------------------------------------------------------------------- #
# real render
# --------------------------------------------------------------------------- #
@unittest.skipUnless(_TEXTUAL, "textual 필요")
class ModeIntroScrollRenderTests(unittest.IsolatedAsyncioTestCase):
    async def test_shift_tab_does_not_flood_the_transcript(self) -> None:
        app = _inline_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            before = len(app._transcript.lines)
            for _ in range(8):
                await pilot.press("shift+tab")
                await pilot.pause()
            self.assertEqual(len(app._transcript.lines), before)   # NO append on cycle

    async def test_issue_and_hint_reflect_the_current_mode(self) -> None:
        from textual.widgets import Static
        app = _inline_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("shift+tab")
            await pilot.pause()
            issue = str(app.query_one("#issue", Static).render())
            hint = str(app.query_one("#hint", Static).render())
            # both surfaces carry the live mode marker (◆ on issue, ▶▶ mode on hint)
            self.assertIn("◆", issue)
            self.assertIn("mode", hint)

    async def test_intro_is_inside_flow_and_scrolls_away(self) -> None:
        from forgekit_console.tui.session_flow import SessionFlow
        from forgekit_console.tui.header import IntroHeader
        app = _inline_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            intro = app.query_one(IntroHeader)
            flow = app.query_one(SessionFlow)
            self.assertIn(intro, list(flow.walk_children()))    # intro is INSIDE the flow
            top_y = intro.region.y
            for i in range(40):
                app._transcript.write(f"line {i}")
            app._follow_tail()
            for _ in range(4):
                await pilot.pause()
            # the intro scrolled UP and out of the viewport (first impression, not chrome)
            self.assertLess(intro.region.y, top_y)
            self.assertLess(intro.region.bottom, 1)             # above the visible top

    async def test_only_sessionflow_scrolls_no_visible_gutter(self) -> None:
        from forgekit_console.tui.session_flow import SessionFlow
        from forgekit_console.tui.main_panel import MainPanel
        from forgekit_console.tui.header import IntroHeader
        app = _inline_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            for i in range(50):
                app._transcript.write(f"line {i}")
            app._follow_tail()
            for _ in range(4):
                await pilot.pause()

            def visible_gutter(w):
                size = int(w.styles.scrollbar_size_vertical or 0)
                return size > 0 and w.show_vertical_scrollbar

            self.assertFalse(visible_gutter(app.query_one(SessionFlow)))
            self.assertFalse(visible_gutter(app.query_one(MainPanel)))
            self.assertFalse(visible_gutter(app.query_one(IntroHeader)))
            # MainPanel / IntroHeader never own scroll at all
            self.assertFalse(app.query_one(MainPanel).allow_vertical_scroll)
            self.assertFalse(app.query_one(IntroHeader).allow_vertical_scroll)


if __name__ == "__main__":
    unittest.main()
