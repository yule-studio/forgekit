"""Input selection contrast (console parity) — operator text-selection must be readable.

Real RUNTIME property check (not a CSS-string assertion): mount the app, resolve the
PromptArea's ``text-area--selection`` component style, and assert the selection
background is the saturated-blue selection token (``$selection-background`` = SELECTION_BG)
with the light foreground — i.e. a selection that POPS against the near-black surface, not
the old quiet accent-dim (which read too close to the background) nor Textual's default.
Enforces a real selection-vs-background contrast floor (regression guard).
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
    return ForgekitConsoleApp(repo_root=Path("/tmp/repo"), context=ctx,
                              config={"primary_provider": "ollama", "linked_providers": ["ollama"]})


@unittest.skipUnless(_TEXTUAL, "textual 필요")
class SelectionContrastTests(unittest.IsolatedAsyncioTestCase):
    async def test_input_selection_is_brand_high_contrast(self) -> None:
        from rich.color import Color as RichColor
        from textual.color import Color

        from forgekit_console.tui import theme
        from forgekit_console.tui.prompt_area import PromptArea

        app = _app()
        async with app.run_test(size=(100, 28)) as pilot:
            await pilot.pause()
            prompt = app.query_one(PromptArea)
            sel = prompt.get_component_styles("text-area--selection")
            bg = sel.background
            fg = sel.color

            # selection background resolves to the SATURATED-blue selection token (not the
            # quiet accent-dim it used to be, and not Textual's low-contrast default)
            self.assertEqual(bg, Color.parse(theme.SELECTION_BG))
            # text stays the light brand foreground → readable on the selection block
            self.assertEqual(fg, Color.parse(theme.FG))
            # real contrast: selection bg differs clearly from the screen background
            self.assertNotEqual(bg, Color.parse(theme.BG))
            # the WHOLE POINT of the lane: selection must POP against the near-black bg so
            # "선택됐는지 안 됐는지" is obvious — enforce a real bg-vs-selection contrast floor
            # that the old accent-dim (#2f6f7a ≈ 3.4:1) did not clear.
            self.assertGreater(_contrast(bg, Color.parse(theme.BG)), 3.7)
            # contrast ratio FG-on-selection is comfortably readable (WCAG-ish > 3:1)
            ratio = _contrast(fg, bg)
            self.assertGreater(ratio, 3.0)
            _ = RichColor  # keep import explicit (rich available)


def _contrast(c1, c2) -> float:
    """WCAG contrast ratio between two textual Colors."""

    def lum(c):
        def chan(v):
            v = v / 255.0
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        r, g, b = chan(c.r), chan(c.g), chan(c.b)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    l1, l2 = sorted((lum(c1), lum(c2)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


if __name__ == "__main__":
    unittest.main()
