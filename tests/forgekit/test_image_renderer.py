"""Avatar image renderer — capability detection + renderer selection + fallback.

Real-image-FIRST is the contract: when the terminal is capable we select the
real-image renderer; otherwise the text-mark fallback. The capability decision
and the selection are pure (injectable env / force), so these tests need no real
terminal and no graphics protocol.
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


class RendererSelectionTests(unittest.TestCase):
    def test_capable_selects_real(self) -> None:
        cap = ir.ImageCapability(True)
        self.assertEqual(ir.select_renderer(cap), ir.RENDERER_REAL)

    def test_not_capable_selects_text(self) -> None:
        cap = ir.ImageCapability(False)
        self.assertEqual(ir.select_renderer(cap), ir.RENDERER_TEXT)

    def test_accepts_bare_bool(self) -> None:
        self.assertEqual(ir.select_renderer(True), ir.RENDERER_REAL)
        self.assertEqual(ir.select_renderer(False), ir.RENDERER_TEXT)

    def test_make_renderer_capable_is_real(self) -> None:
        r = ir.make_renderer(ir.ImageCapability(True))
        self.assertEqual(r.renderer_id, ir.RENDERER_REAL)
        self.assertIsInstance(r, ir.RealImageRenderer)

    def test_make_renderer_incapable_is_text(self) -> None:
        r = ir.make_renderer(ir.ImageCapability(False))
        self.assertEqual(r.renderer_id, ir.RENDERER_TEXT)
        self.assertIsInstance(r, ir.TextMarkRenderer)


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


class FallbackTests(unittest.TestCase):
    def test_text_mark_is_small_and_crisp(self) -> None:
        # fallback must be a small text mark, NOT a per-pixel raster block
        mark = ir.text_mark_lines()
        self.assertLessEqual(len(mark), 3)
        self.assertIn("forge", "\n".join(mark))
        self.assertNotIn("on rgb(", "\n".join(mark))

    def test_text_renderer_renders_the_mark(self) -> None:
        out = ir.TextMarkRenderer().renderable()
        self.assertIn("forge", out)

    def test_real_renderer_falls_back_when_lib_missing(self) -> None:
        # textual-image isn't installed in the test env → real renderer degrades
        # to the text mark renderable (string), proving the fallback path.
        out = ir.RealImageRenderer().renderable()
        if isinstance(out, str):
            self.assertIn("forge", out)
        else:  # textual-image present → an Image renderable; just assert non-None
            self.assertIsNotNone(out)

    def test_real_renderer_with_missing_asset_uses_text(self) -> None:
        orig = ir.best_image_path
        ir_module = ir
        ir_module.best_image_path = lambda: None  # type: ignore[assignment]
        try:
            out = ir.RealImageRenderer().renderable()
            self.assertIn("forge", out)
        finally:
            ir_module.best_image_path = orig  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
