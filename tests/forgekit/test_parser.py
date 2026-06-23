"""forgekit console slash parser + palette (pure)."""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401  (path side-effect)

from forgekit_console.commands.parser import palette_matches, parse_input


class ParseInputTests(unittest.TestCase):
    def test_free_text(self) -> None:
        p = parse_input("hello there")
        self.assertFalse(p.is_slash)
        self.assertEqual(p.name, "")

    def test_slash_with_args(self) -> None:
        p = parse_input("/status verbose now")
        self.assertTrue(p.is_slash)
        self.assertEqual(p.name, "status")
        self.assertEqual(p.args, ("verbose", "now"))

    def test_slash_lowercased(self) -> None:
        self.assertEqual(parse_input("/HELP").name, "help")

    def test_lone_slash_is_empty_slash(self) -> None:
        p = parse_input("/")
        self.assertTrue(p.is_slash)
        self.assertEqual(p.name, "")

    def test_empty(self) -> None:
        p = parse_input("   ")
        self.assertFalse(p.is_slash)


class PaletteTests(unittest.TestCase):
    def test_non_slash_no_matches(self) -> None:
        self.assertEqual(palette_matches("hello"), ())

    def test_bare_slash_returns_all(self) -> None:
        matches = palette_matches("/")
        names = {c.name for c in matches}
        self.assertIn("help", names)
        self.assertIn("quit", names)
        self.assertGreaterEqual(len(matches), 10)

    def test_prefix_filters(self) -> None:
        names = {c.name for c in palette_matches("/st")}
        self.assertEqual(names, {"status"})

    def test_prefix_multiple(self) -> None:
        names = {c.name for c in palette_matches("/a")}
        self.assertEqual(names, {"agents", "about", "always-on", "auto", "autopilot", "attach", "armory"})

    def test_substring_fallback_only_when_no_prefix(self) -> None:
        # prefix exists → exact prefix set (no widening); no prefix → substring fallback.
        self.assertEqual({c.name for c in palette_matches("/st")}, {"status"})  # unchanged
        self.assertIn("self-improve", {c.name for c in palette_matches("/improve")})
        self.assertEqual(palette_matches("/zzz"), ())  # nonsense still empty (honest)


if __name__ == "__main__":
    unittest.main()
