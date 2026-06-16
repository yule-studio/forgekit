"""Avatar image renderer — capability detection + 3-tier renderer priority.

Image-FIRST is the contract, with an explicit 3-tier priority:

1. capable terminal → REAL inline raster,
2. not-capable terminal → IMAGE-DERIVED half-block (still an image, NOT text),
3. only when Pillow / the asset is missing → text/logo mark.

The capability decision and the selection are pure (injectable env / force), so
these tests need no real terminal and no graphics protocol.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.tui import image_renderer as ir


class CapabilityDetectionTests(unittest.TestCase):
    def test_force_arg_wins(self) -> None:
        self.assertTrue(ir.detect_image_capability({}, force=True).capable)
        self.assertFalse(ir.detect_image_capability({"KITTY_WINDOW_ID": "1"}, force=False).capable)

    def test_env_override_image(self) -> None:
        cap = ir.detect_image_capability({"FORGEKIT_AVATAR": "image"})
        self.assertTrue(cap.capable)
        self.assertTrue(cap.forced)

    def test_env_override_text(self) -> None:
        cap = ir.detect_image_capability({"FORGEKIT_AVATAR": "text", "KITTY_WINDOW_ID": "9"})
        self.assertFalse(cap.capable)
        self.assertTrue(cap.forced)

    def test_kitty_detected(self) -> None:
        self.assertTrue(ir.detect_image_capability({"KITTY_WINDOW_ID": "1"}).capable)
        self.assertTrue(ir.detect_image_capability({"TERM": "xterm-kitty"}).capable)

    def test_iterm_detected(self) -> None:
        self.assertTrue(ir.detect_image_capability({"TERM_PROGRAM": "iTerm.app"}).capable)
        self.assertTrue(ir.detect_image_capability({"ITERM_SESSION_ID": "w0"}).capable)

    def test_sixel_detected(self) -> None:
        self.assertTrue(ir.detect_image_capability({"TERM": "xterm-sixel"}).capable)

    def test_plain_terminal_not_capable(self) -> None:
        cap = ir.detect_image_capability({"TERM": "xterm-256color"})
        self.assertFalse(cap.capable)
        self.assertIn("no known", cap.reason)


class CapabilityVscodeTests(unittest.TestCase):
    def test_vscode_integrated_terminal_attempts_real(self) -> None:
        # recent VS Code terminals speak the iTerm2 inline-image protocol → attempt
        cap = ir.detect_image_capability({"TERM_PROGRAM": "vscode"})
        self.assertTrue(cap.capable)
        self.assertIn("vscode", cap.reason)

    def test_wezterm_detected(self) -> None:
        self.assertTrue(ir.detect_image_capability({"TERM_PROGRAM": "WezTerm"}).capable)
        self.assertTrue(ir.detect_image_capability({"TERM": "wezterm"}).capable)


class RendererSelectionTests(unittest.TestCase):
    def test_capable_selects_real(self) -> None:
        cap = ir.ImageCapability(True)
        self.assertEqual(ir.select_renderer(cap), ir.RENDERER_REAL)

    def test_not_capable_selects_image_derived_halfblock_not_text(self) -> None:
        # tier 2: not-capable → image-derived half-block, NOT the text mark
        cap = ir.ImageCapability(False)
        self.assertEqual(ir.select_renderer(cap), ir.RENDERER_HALFBLOCK)
        self.assertNotEqual(ir.select_renderer(cap), ir.RENDERER_TEXT)

    def test_accepts_bare_bool(self) -> None:
        self.assertEqual(ir.select_renderer(True), ir.RENDERER_REAL)
        self.assertEqual(ir.select_renderer(False), ir.RENDERER_HALFBLOCK)

    def test_make_renderer_capable_is_real(self) -> None:
        r = ir.make_renderer(ir.ImageCapability(True))
        self.assertEqual(r.renderer_id, ir.RENDERER_REAL)
        self.assertIsInstance(r, ir.RealImageRenderer)

    def test_make_renderer_incapable_is_halfblock(self) -> None:
        # incapable terminal still gets an IMAGE (tier 2), not the text mark
        r = ir.make_renderer(ir.ImageCapability(False))
        self.assertEqual(r.renderer_id, ir.RENDERER_HALFBLOCK)
        self.assertIsInstance(r, ir.HalfBlockRenderer)


class AssetTests(unittest.TestCase):
    def test_display_png_is_the_baked_small_image(self) -> None:
        png = ir.display_png_path()
        self.assertTrue(png.is_file(), f"baked display PNG missing: {png}")
        self.assertEqual(png.suffix, ".png")
        # small file — Claude-icon scale, not a huge raster
        self.assertLess(png.stat().st_size, 200_000)

    def test_source_master_present(self) -> None:
        self.assertTrue(ir.source_image_path().is_file())

    def test_best_image_prefers_display_png(self) -> None:
        self.assertEqual(ir.best_image_path(), ir.display_png_path())


class HalfBlockTier2Tests(unittest.TestCase):
    """Tier 2 — an IMAGE-DERIVED half-block render of the baked PNG (Pillow)."""

    def test_halfblock_renderer_produces_image_derived_render_not_text(self) -> None:
        # With Pillow + the baked asset present, tier 2 is a Rich Text half-block
        # render of the actual image — NOT the plain text mark.
        from rich.text import Text

        out = ir.HalfBlockRenderer().renderable()
        self.assertIsInstance(out, Text)
        plain = out.plain
        # the half-block raster is made of upper-half block glyphs (image-derived)
        self.assertIn("▀", plain)
        # and it is NOT the brand text mark
        self.assertNotIn("forge", plain)

    def test_make_renderer_incapable_renders_halfblock_image(self) -> None:
        from rich.text import Text

        r = ir.make_renderer(ir.ImageCapability(False))
        out = r.renderable()
        self.assertIsInstance(out, Text)
        self.assertIn("▀", out.plain)


class FallbackTests(unittest.TestCase):
    def test_text_mark_is_small_and_crisp(self) -> None:
        # last-resort text mark must be small, NOT a per-pixel raster block
        mark = ir.text_mark_lines()
        self.assertLessEqual(len(mark), 3)
        self.assertIn("forge", "\n".join(mark))
        self.assertNotIn("on rgb(", "\n".join(mark))

    def test_text_renderer_renders_the_mark(self) -> None:
        out = ir.TextMarkRenderer().renderable()
        self.assertIn("forge", out)

    def test_real_renderer_degrades_to_halfblock_when_lib_missing(self) -> None:
        # textual-image isn't installed in the test env → the real renderer drops
        # to the IMAGE-DERIVED half-block (tier 2), not straight to text.
        from rich.text import Text

        out = ir.RealImageRenderer().renderable()
        if isinstance(out, Text):  # tier 2 half-block (expected without textual-image)
            self.assertIn("▀", out.plain)
        elif isinstance(out, str):  # only if Pillow/asset somehow gone → text mark
            self.assertIn("forge", out)
        else:  # textual-image present → a real Image renderable
            self.assertIsNotNone(out)

    def test_halfblock_with_missing_asset_uses_text(self) -> None:
        # ONLY when the image asset is missing does tier 2 fall through to text.
        orig = ir.best_image_path
        ir.best_image_path = lambda: None  # type: ignore[assignment]
        try:
            out = ir.HalfBlockRenderer().renderable()
            self.assertIsInstance(out, str)
            self.assertIn("forge", out)
        finally:
            ir.best_image_path = orig  # type: ignore[assignment]


class BrandBannerTests(unittest.TestCase):
    """The intro brand mark: REAL inline banner image first, else text wordmark."""

    def test_banner_asset_paths_exist_in_package(self) -> None:
        # the brand banner master + the small baked intro both ship in the package
        master = ir.banner_master_path()
        intro = ir.banner_intro_path()
        self.assertTrue(master.is_file(), f"banner master missing: {master}")
        self.assertEqual(master.suffix, ".png")
        self.assertTrue(intro.is_file(), f"baked intro banner missing: {intro}")
        # best_banner_path prefers the small baked intro
        self.assertEqual(ir.best_banner_path(), intro)
        # the baked intro is small (compact), not the full 1916px master
        self.assertLess(intro.stat().st_size, master.stat().st_size)

    def test_capable_terminal_selects_real_banner_image(self) -> None:
        r = ir.make_brand_renderer(ir.ImageCapability(True))
        self.assertEqual(r.renderer_id, ir.RENDERER_BRAND_IMAGE)
        self.assertIsInstance(r, ir.BrandBannerRenderer)

    def test_incapable_terminal_falls_to_text_wordmark(self) -> None:
        r = ir.make_brand_renderer(ir.ImageCapability(False))
        self.assertEqual(r.renderer_id, ir.RENDERER_BRAND_TEXT)
        self.assertIsInstance(r, ir.BrandTextRenderer)

    def test_text_wordmark_is_cyan_magenta_gradient(self) -> None:
        from forgekit_console.tui import theme

        out = ir.BrandTextRenderer().renderable()
        self.assertIn(theme.ACCENT_PRIMARY, out)
        self.assertIn(theme.ACCENT_SECONDARY, out)
        self.assertIn("forge", out)
        self.assertIn("kit", out)

    def test_banner_renderer_degrades_to_text_when_lib_missing(self) -> None:
        # textual-image may be absent → the real banner renderer drops to the
        # compact text wordmark (the intended fallback), never crashes.
        out = ir.BrandBannerRenderer().renderable()
        if isinstance(out, str):  # text wordmark fallback
            self.assertIn("forge", out)
        else:  # textual-image present → a real Image renderable
            self.assertIsNotNone(out)


if __name__ == "__main__":
    unittest.main()
