"""forgekit brand theme — colour tokens, wordmark gradient, no-orange guard.

Part 1 of the brand/UI work: the cyan/magenta-on-black palette replaces the old
orange. These tests pin the named constants, the cyan→magenta wordmark markup,
and assert no ``orange`` token survives anywhere in the TUI source (grep-style).
All pure — no terminal needed.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.tui import theme

_TUI_DIR = Path(_SRC) / "forgekit_console" / "tui"


class ThemeConstantTests(unittest.TestCase):
    def test_core_tokens_exist_and_are_hex(self) -> None:
        for name in (
            "BG", "FG", "MUTED",
            "ACCENT_PRIMARY", "ACCENT_SECONDARY", "ACCENT_DIM", "BORDER",
            "WARNING", "SUCCESS", "ERROR", "SELECTION_BG",
        ):
            value = getattr(theme, name)
            self.assertRegex(value, r"^#[0-9a-fA-F]{6}$", f"{name}={value!r}")

    def test_selection_token_distinct_from_accent_dim(self) -> None:
        # the selection highlight is its OWN saturated token, no longer the quiet accent-dim
        self.assertNotEqual(theme.SELECTION_BG, theme.ACCENT_DIM)
        v = theme.css_variables()
        self.assertEqual(v["selection-background"], theme.SELECTION_BG)
        self.assertEqual(v["selection-foreground"], theme.FG)
        # the cross-widget drag highlight uses the SAME token (uniform selection)
        self.assertEqual(v["screen-selection-background"], theme.SELECTION_BG)

    def test_brand_accents_are_cyan_and_magenta(self) -> None:
        # cyan/aqua primary, magenta/pink secondary — the banner gradient ends
        self.assertEqual(theme.ACCENT_PRIMARY.lower(), "#00d8f0")
        self.assertEqual(theme.ACCENT_SECONDARY.lower(), "#f23ccf")
        self.assertEqual(theme.BG.lower(), "#0b0d12")

    def test_css_variables_map_repins_textual_defaults(self) -> None:
        variables = theme.css_variables()
        self.assertEqual(variables["background"], theme.BG)
        self.assertEqual(variables["accent"], theme.ACCENT_PRIMARY)
        self.assertEqual(variables["accent-secondary"], theme.ACCENT_SECONDARY)
        self.assertEqual(variables["brand-border"], theme.BORDER)
        # no leading '$' in the keys (textual's get_css_variables form)
        self.assertTrue(all(not k.startswith("$") for k in variables))


class WordmarkTests(unittest.TestCase):
    def test_forge_is_cyan_kit_is_magenta(self) -> None:
        mark = theme.wordmark("forgekit")
        # "forge" carries the cyan token, "kit" the magenta token
        self.assertIn(theme.ACCENT_PRIMARY, mark)
        self.assertIn(theme.ACCENT_SECONDARY, mark)
        self.assertIn("forge", mark)
        self.assertIn("kit", mark)
        # cyan span comes before the magenta span (gradient order)
        self.assertLess(mark.index(theme.ACCENT_PRIMARY), mark.index(theme.ACCENT_SECONDARY))

    def test_split_falls_between_forge_and_kit(self) -> None:
        mark = theme.wordmark("forgekit")
        # the "forge" half and "kit" half are in different colour spans
        cyan_close = f"[/b {theme.ACCENT_PRIMARY}]"
        self.assertIn("forge" + "[/b", mark.split(cyan_close)[0] + "[/b")
        self.assertIn("kit", mark.split(cyan_close)[1])

    def test_arbitrary_text_still_two_tone(self) -> None:
        mark = theme.wordmark("hello")
        self.assertIn(theme.ACCENT_PRIMARY, mark)
        self.assertIn(theme.ACCENT_SECONDARY, mark)


class NoOrangeGuardTests(unittest.TestCase):
    """No ``orange`` markup/colour token may survive in the TUI source."""

    _ORANGE = re.compile(r"orange", re.IGNORECASE)

    def test_no_orange_token_in_tui_source(self) -> None:
        offenders = []
        for path in _TUI_DIR.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                if self._ORANGE.search(line):
                    offenders.append(f"{path.name}:{i}: {line.strip()}")
        self.assertEqual(offenders, [], "orange token still present:\n" + "\n".join(offenders))

    def test_render_uses_brand_accents(self) -> None:
        from forgekit_console.tui import render

        # the operator mode pill marker now uses the cyan accent (not orange1)
        self.assertIn(theme.ACCENT_PRIMARY, render.mode_pill("operator"))
        # intro meta brand uses the wordmark gradient (both accents present)
        joined = "\n".join(render.intro_meta_lines(repo="/r", version="0.1.0"))
        self.assertIn(theme.ACCENT_PRIMARY, joined)
        self.assertIn(theme.ACCENT_SECONDARY, joined)


if __name__ == "__main__":
    unittest.main()
