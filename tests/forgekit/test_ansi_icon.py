"""ANSI icon — the sanitizer security boundary + theme remap (pure, CI-safe).

The point of these tests is the SECURITY contract: raw ANSI is never replayed.
We feed hostile escapes (OSC 8 hyperlink, OSC 52 clipboard, cursor moves, erase,
alt-screen private mode, DCS, charset, unknown) and assert they are dropped +
recorded, that only printable text + SGR colour survives, and that a re-serialized
clean asset round-trips with zero rejects. All pure stdlib → runs in bare CI.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.tui.ansi_icon import model as m
from forgekit_console.tui.ansi_icon import sanitizer as san
from forgekit_console.tui.ansi_icon import render as ar

_ESC = "\x1b"
_HAS_RICH = importlib.util.find_spec("rich") is not None


def _text(doc) -> str:
    return "\n".join("".join(s.text for s in line) for line in doc.lines)


class SafeParseTests(unittest.TestCase):
    def test_truecolor_fg_bg_and_text(self) -> None:
        res = san.sanitize(f"{_ESC}[38;2;255;0;0m{_ESC}[48;2;0;0;0mAB{_ESC}[0m")
        self.assertTrue(res.ok)
        self.assertTrue(res.clean)
        self.assertEqual(res.rejected, ())
        self.assertEqual(_text(res.doc), "AB")
        span = res.doc.lines[0][0]
        self.assertEqual(span.style.fg, (255, 0, 0))
        self.assertEqual(span.style.bg, (0, 0, 0))

    def test_basic16_and_256_and_bold(self) -> None:
        res = san.sanitize(f"{_ESC}[1m{_ESC}[31mX{_ESC}[48;5;21mY")
        self.assertTrue(res.clean)
        spans = res.doc.lines[0]
        self.assertTrue(spans[0].style.bold)
        self.assertEqual(spans[0].style.fg, san._BASE16[1])  # red
        self.assertEqual(spans[-1].style.bg, san._xterm256_to_rgb(21))

    def test_newlines_build_rows(self) -> None:
        res = san.sanitize("a\nbb\nccc")
        self.assertEqual(res.doc.height, 3)
        self.assertEqual(res.doc.width, 3)
        self.assertEqual(_text(res.doc), "a\nbb\nccc")

    def test_empty_sgr_is_reset(self) -> None:
        # ESC[m == ESC[0m (reset)
        res = san.sanitize(f"{_ESC}[31mA{_ESC}[mB")
        self.assertTrue(res.clean)
        self.assertIsNone(res.doc.lines[0][-1].style.fg)


class UnsafeRejectTests(unittest.TestCase):
    """Every unsafe sequence is DROPPED + recorded; printable text survives."""

    def _assert_stripped(self, raw: str, reason: str, kept: str) -> None:
        res = san.sanitize(raw)
        self.assertIn(reason, res.rejected, f"{reason} not recorded for {raw!r}")
        self.assertFalse(res.clean)  # something was stripped
        self.assertEqual(_text(res.doc), kept)
        # the raw escape byte must NEVER appear in the sanitized text
        self.assertNotIn(_ESC, _text(res.doc))

    def test_osc8_hyperlink_dropped(self) -> None:
        self._assert_stripped(f"{_ESC}]8;;https://evil.example{_ESC}\\click{_ESC}]8;;{_ESC}\\",
                              m.REJECT_OSC, "click")

    def test_osc52_clipboard_dropped(self) -> None:
        self._assert_stripped(f"{_ESC}]52;c;c2VjcmV0{chr(7)}keep", m.REJECT_OSC, "keep")

    def test_cursor_moves_dropped(self) -> None:
        self._assert_stripped(f"{_ESC}[2Aup{_ESC}[10;5fhome", m.REJECT_CSI, "uphome")

    def test_erase_dropped(self) -> None:
        self._assert_stripped(f"{_ESC}[2J{_ESC}[Kclear", m.REJECT_CSI, "clear")

    def test_alt_screen_private_mode_dropped(self) -> None:
        self._assert_stripped(f"{_ESC}[?1049hX", m.REJECT_CSI, "X")

    def test_dcs_string_dropped(self) -> None:
        self._assert_stripped(f"{_ESC}Pq#0;1;2{_ESC}\\after", m.REJECT_DCS, "after")

    def test_charset_designator_dropped(self) -> None:
        self._assert_stripped(f"{_ESC}(0lines", m.REJECT_CHARSET, "lines")

    def test_unknown_escape_dropped(self) -> None:
        self._assert_stripped(f"{_ESC}cRIS", m.REJECT_UNKNOWN_ESC, "RIS")

    def test_lone_trailing_escape_dropped(self) -> None:
        self._assert_stripped(f"tail{_ESC}", m.REJECT_LONE_ESC, "tail")

    def test_other_c0_controls_dropped_silently(self) -> None:
        res = san.sanitize("a\rb\tc")  # CR + TAB dropped, no escape
        self.assertEqual(_text(res.doc), "abc")
        self.assertNotIn(_ESC, _text(res.doc))


class RobustnessTests(unittest.TestCase):
    def test_sanitize_never_raises_on_junk(self) -> None:
        for junk in ("", _ESC, _ESC * 50, f"{_ESC}[", f"{_ESC}]", f"{_ESC}[999999m",
                     "\x00\x01\x02", f"{_ESC}[38;2;mbad"):
            res = san.sanitize(junk)  # must not raise
            self.assertNotIn(_ESC, _text(res.doc))

    def test_non_text_input_is_not_ok(self) -> None:
        res = san.sanitize(None)  # type: ignore[arg-type]
        self.assertFalse(res.ok)

    def test_huge_input_is_bounded(self) -> None:
        res = san.sanitize("x\n" * 100_000)
        self.assertLessEqual(res.doc.height, san._MAX_LINES)


class SerializeCleanTests(unittest.TestCase):
    """The bake output: a re-serialized sanitized doc is clean ANSI (SGR only)."""

    def test_roundtrip_is_clean_and_preserves_text(self) -> None:
        raw = (f"{_ESC}]52;c;evil{chr(7)}{_ESC}[38;2;10;20;30mPIX{_ESC}[0m"
               f"{_ESC}[2J\n{_ESC}[48;2;0;0;0m  {_ESC}[0m")
        first = san.sanitize(raw)
        self.assertFalse(first.clean)  # the source had unsafe bytes
        clean_text = san.serialize_clean(first.doc)
        # the serialized asset has no OSC/cursor — only SGR introducers survive
        self.assertNotIn("]52", clean_text)
        self.assertNotIn("[2J", clean_text)
        second = san.sanitize(clean_text)
        self.assertTrue(second.clean)  # re-sanitizing the baked output → zero rejects
        self.assertEqual(_text(second.doc), _text(first.doc))


class ThemeRemapTests(unittest.TestCase):
    def test_resolve_theme_explicit_override_wins(self) -> None:
        self.assertEqual(ar.resolve_theme({"FORGEKIT_TERM_THEME": "light"}), ar.THEME_LIGHT)
        self.assertEqual(ar.resolve_theme({"FORGEKIT_TERM_THEME": "dark", "COLORFGBG": "0;15"}),
                         ar.THEME_DARK)

    def test_resolve_theme_auto_from_colorfgbg(self) -> None:
        self.assertEqual(ar.resolve_theme({"COLORFGBG": "0;15"}), ar.THEME_LIGHT)
        self.assertEqual(ar.resolve_theme({"COLORFGBG": "15;0"}), ar.THEME_DARK)

    def test_resolve_theme_defaults_dark_when_undecidable(self) -> None:
        self.assertEqual(ar.resolve_theme({}), ar.THEME_DARK)
        self.assertEqual(ar.theme_source({}), ar.THEME_SRC_DEFAULT)
        self.assertEqual(ar.theme_source({"FORGEKIT_TERM_THEME": "light"}), ar.THEME_SRC_EXPLICIT)
        self.assertEqual(ar.theme_source({"COLORFGBG": "0;15"}), ar.THEME_SRC_COLORFGBG)

    def test_dark_is_identity(self) -> None:
        self.assertEqual(ar.remap_color((10, 200, 240), ar.THEME_DARK), (10, 200, 240))
        self.assertIsNone(ar.remap_color(None, ar.THEME_DARK))

    def test_light_maps_black_field_to_page(self) -> None:
        # the near-black field becomes the light page colour (not left as a black mass)
        self.assertEqual(ar.remap_color((0, 0, 0), ar.THEME_LIGHT), ar._LIGHT_PAGE)
        self.assertEqual(ar.remap_color((8, 8, 8), ar.THEME_LIGHT), ar._LIGHT_PAGE)

    def test_light_darkens_bright_figure_preserving_hue(self) -> None:
        # a bright figure pixel must get DARKER on light (readable), not a naive invert
        bright = (40, 220, 240)  # bright cyan
        out = ar.remap_color(bright, ar.THEME_LIGHT)
        self.assertLess(ar._luma(out), ar._luma(bright))  # darker
        # hue roughly preserved: still blue-green dominant (b,g > r)
        self.assertGreater(out[2], out[0])
        self.assertGreater(out[1], out[0])


class RendererIntegrationTests(unittest.TestCase):
    """AnsiIconRenderer path selection + fallback, via injected temp assets.

    Uses a written temp ANSI file (NOT the shipped asset) so these run anywhere.
    Status checks need no Rich (the status is decided before rendering); the actual
    ANSI Text render is gated on Rich.
    """

    def _tmp(self, text: str) -> Path:
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        p = d / "icon.ansi"
        p.write_text(text, encoding="utf-8")
        return p

    def test_missing_asset_degrades_with_status(self) -> None:
        r = ar.AnsiIconRenderer(path=Path("/no/such/forgekit-ansi.ansi"))
        out = r.outcome()
        self.assertEqual(out.status, ar.STATUS_NO_ASSET)
        self.assertNotEqual(out.backend, ar.BACKEND_ANSI_ICON)  # degraded honestly

    def test_unsafe_asset_is_refused_and_degrades(self) -> None:
        # an asset carrying an OSC 52 clipboard write is REFUSED (not rendered)
        p = self._tmp(f"{_ESC}]52;c;evil{chr(7)}{_ESC}[38;2;1;2;3mX{_ESC}[0m")
        out = ar.AnsiIconRenderer(path=p).outcome()
        self.assertEqual(out.status, ar.STATUS_UNSAFE)
        self.assertNotEqual(out.backend, ar.BACKEND_ANSI_ICON)

    def test_too_wide_asset_degrades_unless_forced(self) -> None:
        wide = f"{_ESC}[38;2;1;2;3m" + ("X" * 40) + f"{_ESC}[0m"
        p = self._tmp(wide)
        capped = ar.AnsiIconRenderer(path=p, max_cols=8).outcome()
        self.assertEqual(capped.status, ar.STATUS_TOO_WIDE)
        # force (max_cols=0) lifts the cap → never TOO_WIDE (OK with Rich present;
        # INVALID only because Rich is absent in bare CI — but the cap WAS lifted).
        forced = ar.AnsiIconRenderer(path=p, max_cols=0).outcome()
        self.assertNotEqual(forced.status, ar.STATUS_TOO_WIDE)
        if _HAS_RICH:
            self.assertEqual(forced.status, ar.STATUS_OK)

    def test_invalid_asset_degrades(self) -> None:
        p = self._tmp(f"{_ESC}[2J{_ESC}[H")  # only unsafe control, no printable text
        out = ar.AnsiIconRenderer(path=p).outcome()
        self.assertIn(out.status, (ar.STATUS_INVALID, ar.STATUS_UNSAFE))
        self.assertNotEqual(out.backend, ar.BACKEND_ANSI_ICON)

    @unittest.skipUnless(_HAS_RICH, "needs rich")
    def test_clean_asset_renders_ansi_icon(self) -> None:
        from rich.text import Text

        p = self._tmp(f"{_ESC}[38;2;10;200;240mPIX{_ESC}[0m\n{_ESC}[48;2;0;0;0m  {_ESC}[0m")
        r = ar.AnsiIconRenderer(path=p)
        self.assertEqual(r.outcome().status, ar.STATUS_OK)
        self.assertEqual(r.realized_backend(), ar.BACKEND_ANSI_ICON)
        self.assertIsInstance(r.renderable(), Text)

    @unittest.skipUnless(_HAS_RICH, "needs rich")
    def test_light_theme_render_succeeds(self) -> None:
        from rich.text import Text

        p = self._tmp(f"{_ESC}[38;2;0;0;0m##{_ESC}[38;2;40;220;240mFF{_ESC}[0m")
        r = ar.AnsiIconRenderer(path=p, env={"FORGEKIT_TERM_THEME": "light"})
        self.assertEqual(r.outcome().theme, ar.THEME_LIGHT)
        self.assertIsInstance(r.renderable(), Text)


if __name__ == "__main__":
    unittest.main()
