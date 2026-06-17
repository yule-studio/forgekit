"""Avatar image renderer — capability detection + 3-tier renderer priority.

Image-FIRST is the contract, with an explicit 3-tier priority:

1. capable terminal → REAL inline raster,
2. not-capable terminal → IMAGE-DERIVED half-block (still an image, NOT text),
3. only when Pillow / the asset is missing → text/logo mark.

The capability decision and the selection are pure (injectable env / force), so
these tests need no real terminal and no graphics protocol.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.tui import image_renderer as ir

# Optional render deps. CI installs the repo WITHOUT forgekit-console's textual/
# rich/Pillow, so tests that actually render (rich Text half-block, Pillow raster)
# must skip there rather than ImportError. Pure-logic tests (backend classification,
# capability, asset paths) need none of these and always run.
_HAS_RICH = importlib.util.find_spec("rich") is not None
_HAS_PIL = importlib.util.find_spec("PIL") is not None
_HAS_IMAGE_DEPS = _HAS_RICH and _HAS_PIL


def _backend_obj(module_path: str):
    """A fake renderable whose ``type().__module__`` is *module_path* (no deps)."""

    cls = type("Image", (), {"__init__": lambda self, *a, **k: None})
    cls.__module__ = module_path
    return cls()


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

    def test_render_path_is_display_not_raw_source(self) -> None:
        # the console must render the baked DISPLAY asset, never the raw master.
        self.assertNotEqual(ir.best_image_path(), ir.source_image_path())
        self.assertEqual(ir.best_image_path().name, "forgekit-avatar.png")

    def test_source_master_is_the_separate_portrait_file(self) -> None:
        # source/master is its own human-replaceable file, distinct from display.
        self.assertEqual(ir.source_image_path().name, "avatar-source.png")

    def test_display_is_lighter_than_master_not_a_raw_downscale(self) -> None:
        # the display asset is a small crop+tuned derivative, far lighter than the
        # master — evidence it is a baked asset, not the master shipped as-is.
        self.assertLess(
            ir.display_png_path().stat().st_size,
            ir.source_image_path().stat().st_size,
        )


class BakedDisplayAssetTests(unittest.TestCase):
    """The bake pipeline's outputs ship as package assets (canonical + alias)."""

    def test_bake_source_alias_matches_renderer(self) -> None:
        from forgekit_console.assets.avatar import bake

        self.assertEqual(bake.SOURCE, ir.source_image_path())

    def test_runtime_alias_is_the_render_path(self) -> None:
        # the renderer loads the runtime alias (forgekit-avatar.png == display-128).
        from forgekit_console.assets.avatar import bake

        self.assertEqual(bake.ALIAS_PRIMARY, ir.display_png_path())
        self.assertEqual(bake.ALIAS_PRIMARY.name, "forgekit-avatar.png")

    def test_canonical_display_assets_present(self) -> None:
        from forgekit_console.assets.avatar import bake

        self.assertTrue(bake.DISPLAY_128.is_file(), "canonical 128 display missing")
        self.assertTrue(bake.DISPLAY_96.is_file(), "canonical 96 display missing")
        self.assertEqual(bake.DISPLAY_128.name, "forgekit-avatar-display-128.png")
        self.assertEqual(bake.DISPLAY_96.name, "forgekit-avatar-display-96.png")

    def test_aliases_are_byte_identical_to_canonical(self) -> None:
        # alias == canonical (git dedups the blob); they must never drift.
        from forgekit_console.assets.avatar import bake

        self.assertEqual(bake.ALIAS_PRIMARY.read_bytes(), bake.DISPLAY_128.read_bytes())
        self.assertEqual(bake.ALIAS_SMALL.read_bytes(), bake.DISPLAY_96.read_bytes())

    def test_three_source_archives_preserved(self) -> None:
        # all three candidates are kept in-repo so a human can re-pick later.
        d = ir.assets_dir()
        for name in (
            "forgekit-avatar-source-2026-06-17-33.png",
            "forgekit-avatar-source-2026-06-17-38.png",
            "forgekit-avatar-source-2026-06-15-original.png",
        ):
            self.assertTrue((d / name).is_file(), f"source archive missing: {name}")

    def test_master_alias_equals_adopted_archive(self) -> None:
        # avatar-source.png is the ADOPTED original (33), byte-for-byte.
        d = ir.assets_dir()
        self.assertEqual(
            (d / "avatar-source.png").read_bytes(),
            (d / "forgekit-avatar-source-2026-06-17-33.png").read_bytes(),
        )


@unittest.skipUnless(_HAS_IMAGE_DEPS, "needs Pillow + rich")
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

    @unittest.skipUnless(_HAS_RICH, "needs rich for the half-block Text type")
    def test_real_renderer_degrades_to_halfblock_when_not_true_raster(self) -> None:
        # textual-image absent OR resolving to a non-raster backend → the real
        # renderer drops to OUR image-derived half-block (tier 2), not textual-
        # image's cell fallback and not straight to text.
        from rich.text import Text

        out = ir.RealImageRenderer().renderable()
        if isinstance(out, Text):  # tier 2 half-block (expected when not true raster)
            self.assertIn("▀", out.plain)
        elif isinstance(out, str):  # only if Pillow/asset somehow gone → text mark
            self.assertIn("forge", out)
        else:  # a true-raster textual-image Image renderable
            self.assertTrue(ir.is_true_raster(ir.renderable_backend(out)))

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


_ALL_BACKENDS = (
    ir.BACKEND_TGP, ir.BACKEND_SIXEL, ir.BACKEND_HALFCELL, ir.BACKEND_UNICODE,
    ir.BACKEND_HALFBLOCK, ir.BACKEND_TEXT, ir.BACKEND_NONE, ir.BACKEND_UNKNOWN,
)


class BackendClassificationTests(unittest.TestCase):
    """The real fix: classify the actual textual-image backend (no false positives)."""

    def test_only_tgp_and_sixel_are_true_raster(self) -> None:
        self.assertTrue(ir.is_true_raster(ir.BACKEND_TGP))
        self.assertTrue(ir.is_true_raster(ir.BACKEND_SIXEL))
        for fallback in (ir.BACKEND_HALFCELL, ir.BACKEND_UNICODE,
                         ir.BACKEND_HALFBLOCK, ir.BACKEND_TEXT, ir.BACKEND_NONE):
            self.assertFalse(ir.is_true_raster(fallback), fallback)

    def test_renderable_backend_maps_textual_image_classes(self) -> None:
        # the old bug: any non-str object was called "real-image". Now each
        # textual-image backend class maps to its true label.
        self.assertEqual(ir.renderable_backend(_backend_obj("textual_image.renderable.tgp")), ir.BACKEND_TGP)
        self.assertEqual(ir.renderable_backend(_backend_obj("textual_image.renderable.sixel")), ir.BACKEND_SIXEL)
        self.assertEqual(ir.renderable_backend(_backend_obj("textual_image.renderable.halfcell")), ir.BACKEND_HALFCELL)
        self.assertEqual(ir.renderable_backend(_backend_obj("textual_image.renderable.unicode")), ir.BACKEND_UNICODE)

    def test_renderable_backend_maps_our_outputs(self) -> None:
        self.assertEqual(ir.renderable_backend("forge\nkit"), ir.BACKEND_TEXT)
        self.assertEqual(ir.renderable_backend(None), ir.BACKEND_TEXT)
        # our HalfBlockRenderer returns a rich.text.Text → half-block (image-derived)
        self.assertEqual(ir.renderable_backend(_backend_obj("rich.text")), ir.BACKEND_HALFBLOCK)

    def test_halfcell_unicode_are_not_classified_as_real_image(self) -> None:
        for fallback in ("halfcell", "unicode"):
            be = ir.renderable_backend(_backend_obj(f"textual_image.renderable.{fallback}"))
            self.assertFalse(ir.is_true_raster(be), fallback)


class DiagnosticsTests(unittest.TestCase):
    """FORGEKIT_DEBUG_RENDERERS — backend-accurate diagnostics."""

    def test_debug_flag_reads_env(self) -> None:
        self.assertFalse(ir.debug_renderers_enabled({}))
        self.assertFalse(ir.debug_renderers_enabled({"FORGEKIT_DEBUG_RENDERERS": "0"}))
        for on in ("1", "true", "on", "yes", "TRUE"):
            self.assertTrue(ir.debug_renderers_enabled({"FORGEKIT_DEBUG_RENDERERS": on}), on)

    def test_image_library_status_separates_import_from_raster(self) -> None:
        ok, reason, backend = ir.image_library_status()
        self.assertIsInstance(ok, bool)
        self.assertTrue(reason)
        self.assertIn(backend, _ALL_BACKENDS)
        # import-ok must NOT imply true raster: the backend may be a fallback.
        if not ok:
            self.assertEqual(backend, ir.BACKEND_NONE)

    def test_diagnose_renderers_fields_are_known_backends(self) -> None:
        # env-portable: assert structure + vocabulary, not a specific backend.
        diag = ir.diagnose_renderers({"TERM_PROGRAM": "iterm.app"})
        self.assertIn(diag.avatar_backend, _ALL_BACKENDS)
        self.assertIn(diag.brand_backend, _ALL_BACKENDS)
        self.assertEqual(diag.avatar_true_raster, ir.is_true_raster(diag.avatar_backend))
        self.assertEqual(diag.brand_true_raster, ir.is_true_raster(diag.brand_backend))
        self.assertIn(diag.lib_backend, _ALL_BACKENDS)
        self.assertTrue(diag.capability_reason)

    def test_diagnose_forced_text_never_true_raster(self) -> None:
        diag = ir.diagnose_renderers({"FORGEKIT_AVATAR": "text"})
        self.assertEqual(diag.avatar_selected, ir.RENDERER_HALFBLOCK)
        self.assertEqual(diag.brand_selected, ir.RENDERER_BRAND_TEXT)
        self.assertFalse(diag.avatar_true_raster)
        self.assertFalse(diag.brand_true_raster)


class TrueRasterPolicyTests(unittest.TestCase):
    """Policy: use textual-image ONLY for a true raster; else our cleaner fallback."""

    def _install_fake_backend(self, module_path: str) -> None:
        import sys
        import types

        self._saved = {k: sys.modules.get(k) for k in ("textual_image", "textual_image.renderable")}
        sys.modules["textual_image"] = types.ModuleType("textual_image")
        mod = types.ModuleType("textual_image.renderable")
        cls = type("Image", (), {"__init__": lambda self, *a, **k: None})
        cls.__module__ = module_path
        mod.Image = cls
        sys.modules["textual_image.renderable"] = mod

    def tearDown(self) -> None:
        import sys

        for key, val in getattr(self, "_saved", {}).items():
            if val is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = val

    def test_true_raster_backend_is_used_for_avatar(self) -> None:
        self._install_fake_backend("textual_image.renderable.tgp")
        out = ir.RealImageRenderer().renderable()
        self.assertEqual(ir.renderable_backend(out), ir.BACKEND_TGP)
        self.assertTrue(ir.is_true_raster(ir.renderable_backend(out)))

    def test_halfcell_avatar_falls_to_our_fallback_not_cell(self) -> None:
        self._install_fake_backend("textual_image.renderable.halfcell")
        be = ir.renderable_backend(ir.RealImageRenderer().renderable())
        # never textual-image's own halfcell/unicode — our half-block or text mark
        self.assertIn(be, (ir.BACKEND_HALFBLOCK, ir.BACKEND_TEXT))
        self.assertFalse(ir.is_true_raster(be))

    def test_true_raster_backend_is_used_for_brand(self) -> None:
        self._install_fake_backend("textual_image.renderable.sixel")
        out = ir.BrandBannerRenderer().renderable()
        self.assertEqual(ir.renderable_backend(out), ir.BACKEND_SIXEL)

    def test_halfcell_brand_falls_to_text_wordmark(self) -> None:
        self._install_fake_backend("textual_image.renderable.unicode")
        out = ir.BrandBannerRenderer().renderable()
        self.assertIsInstance(out, str)
        self.assertIn("forge", out)


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
