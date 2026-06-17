"""Hero art defensive sanitize + intro hero/compact state machine (pure, CI-safe).

These cover the two PURE pieces: the hero-text cleanup (no control bytes / escapes,
width preserved, bounded) and the hero-vs-compact decision with its overrides. No
terminal, no Rich needed — they run in a bare CI install.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.tui import hero_art
from forgekit_console.tui import intro_state as st

_ESC = "\x1b"


class HeroSanitizeTests(unittest.TestCase):
    def test_plain_unicode_art_preserved_with_width(self) -> None:
        art = "╭────────╮\n│  ▟██▙  │\n╰────────╯"
        lines = hero_art.sanitize_hero_text(art)
        self.assertEqual(lines, ("╭────────╮", "│  ▟██▙  │", "╰────────╯"))
        self.assertEqual(hero_art.hero_width(lines), 10)  # 56-style width preserved

    def test_control_bytes_dropped(self) -> None:
        lines = hero_art.sanitize_hero_text("a\x00b\x07c\x7fd")
        self.assertEqual(lines, ("abcd",))

    def test_escape_sequences_stripped(self) -> None:
        # a stray CSI colour + an OSC-ish run must not survive into the art
        lines = hero_art.sanitize_hero_text(f"{_ESC}[31mRED{_ESC}[0m\nok")
        self.assertEqual(lines, ("RED", "ok"))
        self.assertNotIn(_ESC, "".join(lines))

    def test_blank_edges_trimmed_inner_kept(self) -> None:
        lines = hero_art.sanitize_hero_text("\n\n  X\n  Y\n\n")
        self.assertEqual(lines, ("  X", "  Y"))

    def test_bounded_against_huge_input(self) -> None:
        big = ("x" * 5000 + "\n") * 5000
        lines = hero_art.sanitize_hero_text(big)
        self.assertLessEqual(len(lines), hero_art.MAX_LINES)
        self.assertLessEqual(hero_art.hero_width(lines), hero_art.MAX_WIDTH)

    def test_non_text_is_empty(self) -> None:
        self.assertEqual(hero_art.sanitize_hero_text(None), ())  # type: ignore[arg-type]


class HeroLoadTests(unittest.TestCase):
    def _tmp(self, text: str) -> Path:
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        p = d / "hero.txt"
        p.write_text(text, encoding="utf-8")
        return p

    def test_env_path_override_loads(self) -> None:
        p = self._tmp("HERO\nART")
        env = {hero_art.ENV_HERO_PATH: str(p)}
        self.assertTrue(hero_art.hero_available(env))
        self.assertEqual(hero_art.load_hero_lines(env), ("HERO", "ART"))

    def test_missing_asset_is_unavailable(self) -> None:
        env = {hero_art.ENV_HERO_PATH: "/no/such/hero.txt"}
        self.assertFalse(hero_art.hero_available(env))
        self.assertIsNone(hero_art.load_hero_lines(env))


class IntroModeTests(unittest.TestCase):
    def test_no_asset_is_always_compact(self) -> None:
        self.assertEqual(
            st.resolve_intro_mode({}, hero_available=False, transcript_empty=True),
            st.INTRO_COMPACT,
        )

    def test_fresh_empty_idle_is_hero(self) -> None:
        self.assertEqual(
            st.resolve_intro_mode({}, hero_available=True, transcript_empty=True),
            st.INTRO_HERO,
        )

    def test_working_states_collapse_to_compact(self) -> None:
        for kw in (
            {"transcript_empty": False},
            {"transcript_empty": True, "typing": True},
            {"transcript_empty": True, "palette_open": True},
            {"transcript_empty": True, "in_agent": True},
            {"transcript_empty": True, "help_open": True},
        ):
            self.assertEqual(
                st.resolve_intro_mode({}, hero_available=True, **kw),
                st.INTRO_COMPACT, kw,
            )

    def test_about_surface_is_hero_even_when_help_open(self) -> None:
        self.assertEqual(
            st.resolve_intro_mode(
                {}, hero_available=True, transcript_empty=False,
                help_open=True, about_open=True,
            ),
            st.INTRO_HERO,
        )

    def test_env_off_forces_compact(self) -> None:
        self.assertEqual(
            st.resolve_intro_mode(
                {"FORGEKIT_HERO_ART": "off"}, hero_available=True, transcript_empty=True,
            ),
            st.INTRO_COMPACT,
        )

    def test_env_intro_mode_hero_forces_hero_while_working(self) -> None:
        self.assertEqual(
            st.resolve_intro_mode(
                {"FORGEKIT_INTRO_MODE": "hero"}, hero_available=True,
                transcript_empty=False, typing=True,
            ),
            st.INTRO_HERO,
        )

    def test_env_intro_mode_compact_forces_compact_when_fresh(self) -> None:
        self.assertEqual(
            st.resolve_intro_mode(
                {"FORGEKIT_INTRO_MODE": "compact"}, hero_available=True,
                transcript_empty=True,
            ),
            st.INTRO_COMPACT,
        )


if __name__ == "__main__":
    unittest.main()
