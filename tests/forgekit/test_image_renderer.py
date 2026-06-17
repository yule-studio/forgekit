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

    def test_not_capable_selects_pixel_halfblock_not_badge(self) -> None:
        # non-raster default is the PIXEL half-block (image family the operator sees),
        # NOT the badge and NOT the bare text mark. The badge is only a last resort.
        cap = ir.ImageCapability(False)
        self.assertEqual(ir.select_renderer(cap), ir.RENDERER_HALFBLOCK)
        self.assertNotEqual(ir.select_renderer(cap), ir.RENDERER_TEXT)
        self.assertNotEqual(ir.select_renderer(cap), ir.RENDERER_AVATAR_MARK)

    def test_accepts_bare_bool(self) -> None:
        self.assertEqual(ir.select_renderer(True), ir.RENDERER_REAL)
        self.assertEqual(ir.select_renderer(False), ir.RENDERER_HALFBLOCK)

    def test_make_renderer_capable_is_real(self) -> None:
        r = ir.make_renderer(ir.ImageCapability(True))
        self.assertEqual(r.renderer_id, ir.RENDERER_REAL)
        self.assertIsInstance(r, ir.RealImageRenderer)

    def test_make_renderer_incapable_is_pixel_halfblock(self) -> None:
        # incapable terminal gets the PIXEL half-block (not the badge)
        r = ir.make_renderer(ir.ImageCapability(False))
        self.assertEqual(r.renderer_id, ir.RENDERER_HALFBLOCK)
        self.assertIsInstance(r, ir.HalfBlockRenderer)
        self.assertFalse(r.portrait)  # the PIXEL icon source, not the portrait

    def test_force_overrides_select_renderer(self) -> None:
        portrait = ir.make_renderer(env={"FORGEKIT_AVATAR": "portrait"})
        self.assertIsInstance(portrait, ir.HalfBlockRenderer)
        self.assertTrue(portrait.portrait)  # portrait override → detailed portrait
        self.assertIsInstance(ir.make_renderer(env={"FORGEKIT_AVATAR": "mark"}), ir.AvatarMarkRenderer)
        self.assertIsInstance(ir.make_renderer(env={"FORGEKIT_AVATAR": "text"}), ir.TextMarkRenderer)
        self.assertIsInstance(ir.make_renderer(env={"FORGEKIT_AVATAR": "image"}), ir.RealImageRenderer)


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

    def test_runtime_alias_is_the_terminal_icon(self) -> None:
        # the DEFAULT render path is the simplified terminal icon (runtime alias).
        from forgekit_console.assets.avatar import bake

        self.assertEqual(bake.ALIAS_PRIMARY, ir.display_png_path())
        self.assertEqual(bake.ALIAS_PRIMARY.name, "forgekit-avatar.png")

    def test_terminal_icon_assets_present(self) -> None:
        from forgekit_console.assets.avatar import bake

        self.assertTrue(bake.ICON_MASTER.is_file(), "terminal-icon master missing")
        self.assertTrue(bake.ICON_128.is_file(), "terminal-icon 128 missing")
        self.assertTrue(bake.ICON_96.is_file(), "terminal-icon 96 missing")
        self.assertEqual(bake.ICON_128.name, "forgekit-terminal-icon-128.png")

    def test_portrait_assets_kept_for_optin_mode(self) -> None:
        # the detailed portrait is kept (archive / future GUI / FORGEKIT_AVATAR=portrait)
        from forgekit_console.assets.avatar import bake

        self.assertTrue(bake.DISPLAY_128.is_file(), "portrait 128 missing")
        self.assertTrue(bake.DISPLAY_96.is_file(), "portrait 96 missing")
        self.assertEqual(ir.portrait_png_path().name, "forgekit-avatar-display-128.png")

    def test_terminal_icon_is_the_provided_pixel_art(self) -> None:
        # the terminal icon is baked from the PROVIDED pixel-art source (not a
        # re-created silhouette); the runtime alias the renderer loads == that icon.
        from forgekit_console.assets.avatar import bake

        self.assertTrue(bake.ICON_SOURCE.is_file(), "pixel icon source missing")
        self.assertEqual(bake.ICON_SOURCE.name, "forgekit-terminal-icon-source.png")
        self.assertEqual(bake.ALIAS_PRIMARY, ir.display_png_path())  # icon IS the render path
        # icon ≠ portrait (distinct asset families)
        self.assertNotEqual(bake.ICON_128.read_bytes(), bake.DISPLAY_128.read_bytes())

    def test_runtime_aliases_are_byte_identical_to_icon(self) -> None:
        # alias == canonical ICON (git dedups the blob); they must never drift.
        from forgekit_console.assets.avatar import bake

        self.assertEqual(bake.ALIAS_PRIMARY.read_bytes(), bake.ICON_128.read_bytes())
        self.assertEqual(bake.ALIAS_SMALL.read_bytes(), bake.ICON_96.read_bytes())

    def test_default_asset_mode_is_terminal_icon(self) -> None:
        self.assertEqual(ir.avatar_asset_mode({}), ir.ASSET_TERMINAL_ICON)
        self.assertEqual(ir.avatar_asset_mode({"FORGEKIT_AVATAR": "portrait"}), ir.ASSET_PORTRAIT)

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

    def test_avatar_render_is_image_derived_braille_not_text(self) -> None:
        # the PIXEL avatar is an image-DERIVED render — braille (2x4 dots/cell, the
        # render-spike path), NOT the plain text mark. Portrait mode stays half-block.
        from rich.text import Text

        out = ir.HalfBlockRenderer().renderable()  # default = pixel avatar
        self.assertIsInstance(out, Text)
        plain = out.plain
        # braille glyphs live in U+2800..U+28FF
        self.assertTrue(any(0x2800 <= ord(ch) <= 0x28FF for ch in plain), "no braille glyphs")
        self.assertNotIn("forge", plain)  # not the brand text mark
        # the opt-in detailed portrait still uses the grayscale half-block (▀)
        self.assertIn("▀", ir.HalfBlockRenderer(portrait=True).renderable().plain)

    def test_portrait_override_renders_halfblock_image(self) -> None:
        # the half-block portrait is now OPT-IN (FORGEKIT_AVATAR=portrait), not the
        # incapable default — that default is the crisp brand badge.
        from rich.text import Text

        r = ir.make_renderer(env={"FORGEKIT_AVATAR": "portrait"})
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

    def test_avatar_mark_is_crisp_brand_badge(self) -> None:
        # the default non-raster avatar: a clean framed fk badge in brand colours,
        # NO per-pixel colour spans (those are what dither in a poor terminal).
        out = ir.AvatarMarkRenderer().renderable()
        self.assertIsInstance(out, str)
        self.assertEqual(ir.AvatarMarkRenderer().realized_backend(), ir.BACKEND_AVATAR_MARK)
        self.assertIn("f", out)
        self.assertIn("k", out)
        from forgekit_console.tui import theme
        self.assertIn(theme.ACCENT_PRIMARY, out)   # cyan (forge)
        self.assertIn(theme.ACCENT_SECONDARY, out)  # magenta (kit)
        self.assertNotIn("▀", out)  # not a half-block raster
        self.assertNotIn("on rgb(", out)  # not per-pixel colour

    def test_real_renderer_degrades_to_pixel_halfblock_when_not_true_raster(self) -> None:
        # not a true raster → the PIXEL half-block (image family), never the badge
        # directly. (A true-raster terminal yields a textual-image Image.)
        backend = ir.RealImageRenderer().realized_backend()
        if ir.is_true_raster(backend):
            return  # true-raster path
        # non-raster: pixel half-block (image), or the badge/text only as last resort
        self.assertIn(backend, (ir.BACKEND_HALFBLOCK, ir.BACKEND_AVATAR_MARK, ir.BACKEND_TEXT))

    def test_halfblock_badge_is_last_resort_when_asset_missing(self) -> None:
        # the PIXEL half-block falls to the BADGE (last resort) only when Pillow /
        # the asset is missing — NOT to bare text first.
        orig = ir.best_image_path
        ir.best_image_path = lambda: None  # type: ignore[assignment]
        try:
            r = ir.HalfBlockRenderer()
            self.assertEqual(r.realized_backend(), ir.BACKEND_AVATAR_MARK)
            out = r.renderable()  # the fk badge monogram (last resort)
            self.assertIn("f", out)
            self.assertIn("k", out)
        finally:
            ir.best_image_path = orig  # type: ignore[assignment]


_ALL_BACKENDS = (
    ir.BACKEND_TGP, ir.BACKEND_SIXEL, ir.BACKEND_HALFCELL, ir.BACKEND_UNICODE,
    ir.BACKEND_HALFBLOCK, ir.BACKEND_AVATAR_MARK, ir.BACKEND_BRAND_TEXT,
    ir.BACKEND_TEXT, ir.BACKEND_NONE, ir.BACKEND_UNKNOWN,
)
_ALL_POLICIES = (
    ir.POLICY_TRUE_RASTER, ir.POLICY_MANAGED_FALLBACK, ir.POLICY_HARD_FALLBACK,
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

    def test_prime_image_backend_never_raises_and_returns_label(self) -> None:
        # early-probe entrypoint: returns a backend label (none when absent), no throw.
        be = ir.prime_image_backend()
        self.assertIn(be, _ALL_BACKENDS)

    def test_image_library_status_separates_import_from_raster(self) -> None:
        ok, reason, backend = ir.image_library_status()
        self.assertIsInstance(ok, bool)
        self.assertTrue(reason)
        self.assertIn(backend, _ALL_BACKENDS)
        # import-ok must NOT imply true raster: the backend may be a fallback.
        if not ok:
            self.assertEqual(backend, ir.BACKEND_NONE)

    def test_diagnose_renderers_fields_are_known_backends_and_policies(self) -> None:
        # env-portable: assert structure + vocabulary, not a specific backend.
        diag = ir.diagnose_renderers({"TERM_PROGRAM": "iterm.app"})
        self.assertIn(diag.avatar_backend, _ALL_BACKENDS)
        self.assertIn(diag.brand_backend, _ALL_BACKENDS)
        self.assertEqual(diag.avatar_true_raster, ir.is_true_raster(diag.avatar_backend))
        self.assertEqual(diag.brand_true_raster, ir.is_true_raster(diag.brand_backend))
        self.assertIn(diag.avatar_policy, _ALL_POLICIES)
        self.assertIn(diag.brand_policy, _ALL_POLICIES)
        # policy is derived from the realized backend, consistently
        self.assertEqual(diag.avatar_policy, ir.policy_state(diag.avatar_backend))
        self.assertEqual(diag.brand_policy, ir.policy_state(diag.brand_backend))
        self.assertIn(diag.lib_backend, _ALL_BACKENDS)
        self.assertTrue(diag.capability_reason)

    def test_diagnose_forced_text_is_hard_fallback(self) -> None:
        diag = ir.diagnose_renderers({"FORGEKIT_AVATAR": "text"})
        self.assertEqual(diag.avatar_selected, ir.RENDERER_TEXT)
        self.assertEqual(diag.avatar_policy, ir.POLICY_HARD_FALLBACK)
        self.assertFalse(diag.avatar_true_raster)
        self.assertFalse(diag.brand_true_raster)

    def test_policy_state_mapping(self) -> None:
        self.assertEqual(ir.policy_state(ir.BACKEND_TGP), ir.POLICY_TRUE_RASTER)
        self.assertEqual(ir.policy_state(ir.BACKEND_SIXEL), ir.POLICY_TRUE_RASTER)
        for managed in (ir.BACKEND_AVATAR_MARK, ir.BACKEND_BRAND_TEXT, ir.BACKEND_HALFBLOCK):
            self.assertEqual(ir.policy_state(managed), ir.POLICY_MANAGED_FALLBACK, managed)
        for hard in (ir.BACKEND_TEXT, ir.BACKEND_NONE, ir.BACKEND_UNKNOWN):
            self.assertEqual(ir.policy_state(hard), ir.POLICY_HARD_FALLBACK, hard)


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
        r = ir.RealImageRenderer()
        self.assertEqual(r.realized_backend(), ir.BACKEND_TGP)
        self.assertEqual(ir.renderable_backend(r.renderable()), ir.BACKEND_TGP)
        self.assertTrue(ir.is_true_raster(r.realized_backend()))

    def test_halfcell_avatar_falls_to_pixel_halfblock_not_cell(self) -> None:
        self._install_fake_backend("textual_image.renderable.halfcell")
        r = ir.RealImageRenderer()
        # never textual-image's own halfcell/unicode — OUR pixel half-block (image),
        # or the badge/text only as a last resort (no Pillow/asset in this env).
        be = r.realized_backend()
        self.assertIn(be, (ir.BACKEND_HALFBLOCK, ir.BACKEND_AVATAR_MARK, ir.BACKEND_TEXT))
        self.assertEqual(ir.policy_state(be), ir.POLICY_MANAGED_FALLBACK)

    def test_true_raster_backend_is_used_for_brand(self) -> None:
        self._install_fake_backend("textual_image.renderable.sixel")
        r = ir.BrandBannerRenderer()
        self.assertEqual(r.realized_backend(), ir.BACKEND_SIXEL)
        self.assertEqual(ir.renderable_backend(r.renderable()), ir.BACKEND_SIXEL)

    def test_halfcell_brand_falls_to_text_wordmark(self) -> None:
        self._install_fake_backend("textual_image.renderable.unicode")
        r = ir.BrandBannerRenderer()
        self.assertEqual(r.realized_backend(), ir.BACKEND_BRAND_TEXT)
        out = r.renderable()
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
