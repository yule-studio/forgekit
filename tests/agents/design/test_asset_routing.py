"""P0-I stage 3 commit 6 — design-to-code asset routing tests."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.design.asset_routing import (
    APPLE_TOUCH_ICON_SIZE,
    FAVICON_RASTER_SIZES,
    SVG_RASTER_FALLBACK_BYTES,
    SURFACE_FAVICON,
    SURFACE_ICON,
    SURFACE_ILLUSTRATION,
    SURFACE_LOGO,
    build_handoff_packet,
    recommend_format,
    validate_asset_name,
)


# ---------------------------------------------------------------------------
# Name validator
# ---------------------------------------------------------------------------


class NameValidatorTests(unittest.TestCase):
    def test_logo_primary_valid(self) -> None:
        v = validate_asset_name("logo-primary")
        self.assertTrue(v.valid)
        self.assertEqual(v.surface, SURFACE_LOGO)
        self.assertEqual(v.intent, "primary")

    def test_logo_primary_light_valid(self) -> None:
        v = validate_asset_name("logo-primary-light")
        self.assertTrue(v.valid)
        self.assertEqual(v.intent, "primary-light")

    def test_icon_status_success_valid(self) -> None:
        v = validate_asset_name("icon-status-success")
        self.assertTrue(v.valid)
        self.assertEqual(v.surface, SURFACE_ICON)

    def test_favicon_light_valid(self) -> None:
        v = validate_asset_name("favicon-light")
        self.assertTrue(v.valid)
        self.assertEqual(v.surface, SURFACE_FAVICON)

    def test_uppercase_rejected(self) -> None:
        v = validate_asset_name("Logo-primary")
        self.assertFalse(v.valid)
        self.assertIn("uppercase_characters", v.failure_reasons)

    def test_whitespace_rejected(self) -> None:
        v = validate_asset_name("logo primary")
        self.assertFalse(v.valid)
        self.assertIn("contains_whitespace", v.failure_reasons)

    def test_double_hyphen_rejected(self) -> None:
        v = validate_asset_name("logo--primary")
        self.assertFalse(v.valid)
        self.assertIn("double_hyphen", v.failure_reasons)

    def test_unknown_surface_rejected(self) -> None:
        v = validate_asset_name("widget-primary")
        self.assertFalse(v.valid)
        self.assertIn("unknown_surface_prefix", v.failure_reasons)

    def test_missing_intent_rejected(self) -> None:
        v = validate_asset_name("logo")
        self.assertFalse(v.valid)
        self.assertTrue(
            "missing_intent" in v.failure_reasons
            or "unknown_surface_prefix" in v.failure_reasons
        )

    def test_empty_rejected(self) -> None:
        v = validate_asset_name("")
        self.assertFalse(v.valid)
        self.assertTrue(any("empty" in r for r in v.failure_reasons))


# ---------------------------------------------------------------------------
# Format recommendation
# ---------------------------------------------------------------------------


class FormatRecommendationTests(unittest.TestCase):
    def test_logo_default_svg(self) -> None:
        rec = recommend_format(surface=SURFACE_LOGO)
        self.assertEqual(rec.primary_format, "svg")
        self.assertFalse(rec.boundary_crossing)

    def test_photographic_forces_raster(self) -> None:
        rec = recommend_format(surface=SURFACE_ILLUSTRATION, is_photographic=True)
        self.assertEqual(rec.primary_format, "raster")
        self.assertIn("실사", rec.reason or "")

    def test_favicon_emits_raster_matrix(self) -> None:
        rec = recommend_format(surface=SURFACE_FAVICON)
        self.assertEqual(rec.primary_format, "svg")
        # 5 PNG sizes + apple-touch-icon = 6 raster items.
        self.assertEqual(
            len(rec.raster_required_for), len(FAVICON_RASTER_SIZES) + 1
        )
        self.assertIn(
            f"apple-touch-icon-{APPLE_TOUCH_ICON_SIZE}.png",
            rec.raster_required_for,
        )

    def test_svg_too_large_flips_to_raster(self) -> None:
        rec = recommend_format(
            surface=SURFACE_ILLUSTRATION,
            svg_estimated_bytes=SVG_RASTER_FALLBACK_BYTES + 1,
        )
        self.assertEqual(rec.primary_format, "raster")
        self.assertTrue(rec.boundary_crossing)

    def test_complex_illustration_marks_boundary(self) -> None:
        rec = recommend_format(
            surface=SURFACE_ILLUSTRATION, is_complex_illustration=True
        )
        # Primary stays svg unless other signals flip it.
        self.assertEqual(rec.primary_format, "svg")
        self.assertTrue(rec.boundary_crossing)


# ---------------------------------------------------------------------------
# Handoff packet
# ---------------------------------------------------------------------------


class HandoffPacketTests(unittest.TestCase):
    def test_valid_name_yields_no_blockers(self) -> None:
        p = build_handoff_packet(
            name="logo-primary",
            dimensions={
                "의미": "메인 브랜드 로고",
                "형태": "wordmark + symbol",
                "컬러": "primary-500 / on-primary",
                "비율": "1:1 viewBox 48",
                "용도": "header / favicon",
            },
            color_tokens=("primary-500", "on-primary"),
            view_box="48 48",
        )
        self.assertEqual(p.surface, SURFACE_LOGO)
        self.assertEqual(p.intent, "primary")
        self.assertEqual(p.blockers, ())
        self.assertIsNotNone(p.format_recommendation)

    def test_invalid_name_blockers_set(self) -> None:
        p = build_handoff_packet(name="Logo-primary")
        self.assertGreater(len(p.blockers), 0)
        self.assertIn("uppercase_characters", p.blockers)

    def test_hex_color_blocks_handoff(self) -> None:
        p = build_handoff_packet(
            name="icon-status-success",
            color_tokens=("#22cc55",),
        )
        self.assertIn("hex_color_literal", p.blockers)

    def test_favicon_handoff_carries_raster_matrix(self) -> None:
        p = build_handoff_packet(name="favicon-light")
        self.assertEqual(p.surface, SURFACE_FAVICON)
        assert p.format_recommendation is not None
        raster = p.format_recommendation["raster_required_for"]
        self.assertEqual(
            len(raster), len(FAVICON_RASTER_SIZES) + 1
        )

    def test_to_dict_round_trip_shape(self) -> None:
        p = build_handoff_packet(
            name="logo-primary",
            color_tokens=("primary-500",),
            view_box="48 48",
        )
        payload = p.to_dict()
        self.assertEqual(payload["name"], "logo-primary")
        self.assertEqual(payload["color_tokens"], ["primary-500"])
        self.assertEqual(payload["blockers"], [])


if __name__ == "__main__":
    unittest.main()
