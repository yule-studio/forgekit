"""forgekit console avatar tiers + fallback (pure-ish, no terminal)."""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.tui import avatar


class AvatarTests(unittest.TestCase):
    def test_baked_asset_present_and_markup(self) -> None:
        lines = avatar.load_baked_asset()
        self.assertIsNotNone(lines)
        self.assertGreater(len(lines), 0)
        # half-block Rich markup
        self.assertIn("▀", lines[0])

    def test_render_avatar_never_empty(self) -> None:
        self.assertTrue(avatar.render_avatar())

    def test_text_brandmark_always_available(self) -> None:
        mark = avatar.text_brandmark()
        self.assertTrue(mark)
        self.assertTrue(any("forge" in line for line in mark))

    def test_render_falls_back_to_brandmark_when_asset_missing(self) -> None:
        # monkeypatch the asset path to a non-existent file
        orig = avatar._asset_path
        avatar._asset_path = lambda: Path("/nonexistent/forgekit-avatar.txt")
        try:
            out = avatar.render_avatar()  # no image_path → brandmark
            self.assertEqual(out, avatar.text_brandmark())
        finally:
            avatar._asset_path = orig

    def test_render_from_missing_image_is_none(self) -> None:
        self.assertIsNone(avatar.render_from_image(Path("/nonexistent/x.jpg")))

    def test_mini_brandmark_is_small_and_crisp(self) -> None:
        # the default mark must be tiny (never a raster block) and contain no
        # per-pixel colour spans (which is what pixelates in a poor terminal).
        mark = avatar.mini_brandmark()
        self.assertLessEqual(len(mark), 3)
        self.assertTrue(any("forge" in line for line in mark))
        self.assertNotIn("on rgb(", "\n".join(mark))


if __name__ == "__main__":
    unittest.main()
