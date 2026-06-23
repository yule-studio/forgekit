"""Transcript drag-selection contrast + mode-aware copy/selection visibility (parity lane).

Two parity gaps closed with REAL structure (not CSS-string reading, not fake):

1. The cross-widget drag-selection highlight (Textual ``screen--selection``, used when the
   full-screen TUI captures the mouse) was Textual's DEFAULT ~50%-alpha blue (``#0178D47F``)
   — a muddy, low-contrast block on the near-black brand background. It is now the brand
   desaturated-cyan (``accent-dim``) with the light foreground FORCED on top, matching the
   composer's ``text-area--selection`` treatment. Proven by a RUNTIME property check (resolve
   the component style on a mounted app) + a measured WCAG contrast ratio, mirroring
   ``test_tui_selection_contrast`` for the input.

2. The help "select & copy" guidance was a static block that always showed the full-screen
   mouse-capture caveat regardless of the actual run mode. It is now MODE-AWARE: inline (mouse
   not captured → terminal-native drag-select) vs full (app drag-select + Ctrl+C). Honest about
   what actually works per mode; ``/copy`` works in both. Pure → tested without a terminal.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.tui import render, theme

_TEXTUAL = importlib.util.find_spec("textual") is not None


# --- gap 2: mode-aware copy/selection visibility (pure) ---------------------
class SelectionCopyGuidanceTests(unittest.TestCase):
    def test_inline_mode_surfaces_terminal_native_selection(self) -> None:
        lines = render.selection_copy_lines(inline=True)
        text = "\n".join(lines)
        self.assertIn("inline 모드", text)
        self.assertIn("터미널 native 선택", text)   # mouse not captured → terminal owns it
        self.assertNotIn("Ctrl+C", text)            # no app-capture copy path in inline
        self.assertIn("/copy", text)                # /copy still works
        self.assertIn("선택-blue", text)            # selection highlight named (saturated blue)

    def test_full_mode_surfaces_app_drag_and_ctrl_c(self) -> None:
        lines = render.selection_copy_lines(inline=False)
        text = "\n".join(lines)
        self.assertIn("full-screen 모드", text)
        self.assertIn("Ctrl+C", text)               # app captures mouse → drag + Ctrl+C
        self.assertIn("일반 터미널 드래그는 막힘", text)   # honest about the caveat
        self.assertIn("/copy", text)

    def test_help_section_embeds_mode_aware_guidance(self) -> None:
        from forgekit_console.commands.registry import load_agents, load_commands

        secs_inline = render.help_sections(load_commands(), load_agents(), inline=True)
        help_inline = "\n".join(secs_inline[0].lines)   # Help tab is first
        self.assertIn("터미널 native 선택", help_inline)
        secs_full = render.help_sections(load_commands(), load_agents(), inline=False)
        help_full = "\n".join(secs_full[0].lines)
        self.assertIn("Ctrl+C", help_full)
        # tab TITLES are identical across modes (only the copy body differs)
        self.assertEqual([s.title for s in secs_inline], [s.title for s in secs_full])


def _contrast(c1, c2) -> float:
    """WCAG contrast ratio between two textual Colors."""

    def lum(c):
        def chan(v):
            v = v / 255.0
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * chan(c.r) + 0.7152 * chan(c.g) + 0.0722 * chan(c.b)

    l1, l2 = sorted((lum(c1), lum(c2)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


# --- gap 1: transcript drag-selection contrast (runtime property) -----------
@unittest.skipUnless(_TEXTUAL, "textual 필요")
class TranscriptSelectionContrastTests(unittest.IsolatedAsyncioTestCase):
    async def test_screen_selection_is_brand_high_contrast(self) -> None:
        from textual.color import Color

        from forgekit_console.commands.registry import load_agents, load_commands
        from forgekit_console.commands.router import ConsoleContext
        from forgekit_console.tui.app import ForgekitConsoleApp

        ctx = ConsoleContext(repo_root=Path("/tmp/repo"), agents=load_agents(),
                             commands=load_commands())
        app = ForgekitConsoleApp(repo_root=Path("/tmp/repo"), context=ctx,
                                 config={"primary_provider": "ollama", "linked_providers": ["ollama"]})
        async with app.run_test(size=(100, 28)) as pilot:
            await pilot.pause()
            # the cross-widget drag-selection style (transcript + any widget), resolved live.
            sel = app.screen.get_component_styles("screen--selection")
            bg, fg = sel.background, sel.color

            # saturated selection-blue bg (NOT Textual's default ~50%-alpha #0178D47F, and
            # no longer the quiet accent-dim that read too close to the background)
            self.assertEqual(bg, Color.parse(theme.SELECTION_BG))
            # light brand foreground FORCED on top → uniformly readable selection
            self.assertEqual(fg, Color.parse(theme.FG))
            # selection must POP against the near-black background (the lane's whole point)
            self.assertGreater(_contrast(bg, Color.parse(theme.BG)), 3.7)
            # measured FG-on-selection contrast comfortably readable (> 3:1)
            self.assertGreater(_contrast(fg, bg), 3.0)


if __name__ == "__main__":
    unittest.main()
