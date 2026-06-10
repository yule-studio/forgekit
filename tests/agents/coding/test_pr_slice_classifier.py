"""P0-I stage 3 commit 3 — PR slice classifier unit tests."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.coding.pr_slice_classifier import (
    PRSliceClassification,
    PR_SIZE_WARNING_THRESHOLD,
    SLICE_CREATE,
    SLICE_DELETE,
    SLICE_DOCS_ONLY,
    SLICE_HOTFIX,
    SLICE_MIXED,
    SLICE_READ,
    SLICE_TEST_ONLY,
    SLICE_TINY_CONFIG,
    SLICE_UPDATE,
    classify_pr_slice,
)


# ---------------------------------------------------------------------------
# Exception slices (test_only / docs_only)
# ---------------------------------------------------------------------------


class ExceptionFileTests(unittest.TestCase):
    def test_all_tests_only(self) -> None:
        r = classify_pr_slice(
            title="Add regression tests",
            changed_files=("tests/foo/test_x.py", "tests/bar/test_y.py"),
            changed_lines=100,
            test_lines=100,
        )
        self.assertEqual(r.primary_slice, SLICE_TEST_ONLY)
        self.assertEqual(r.changed_lines_excluding_tests, 0)
        self.assertFalse(r.size_warning)

    def test_all_docs_only(self) -> None:
        r = classify_pr_slice(
            title="Update docs",
            changed_files=(
                "docs/policy-stack-stage3-enforcement-layer.md",
                "policies/runtime/agents/engineering-agent/growth-loop.md",
            ),
        )
        self.assertEqual(r.primary_slice, SLICE_DOCS_ONLY)

    def test_mixed_test_and_impl_not_test_only(self) -> None:
        r = classify_pr_slice(
            title="New module",
            changed_files=("src/foo/x.py", "tests/foo/test_x.py"),
            changed_lines=200,
            test_lines=100,
        )
        self.assertNotEqual(r.primary_slice, SLICE_TEST_ONLY)
        self.assertEqual(r.changed_lines_excluding_tests, 100)


# ---------------------------------------------------------------------------
# Emoji prefix (high confidence)
# ---------------------------------------------------------------------------


class EmojiPrefixTests(unittest.TestCase):
    def test_sparkles_create(self) -> None:
        r = classify_pr_slice(title="✨ New module")
        self.assertEqual(r.primary_slice, SLICE_CREATE)
        self.assertEqual(r.confidence, 1.0)

    def test_hammer_update(self) -> None:
        r = classify_pr_slice(title="🔨 Refactor helper")
        self.assertEqual(r.primary_slice, SLICE_UPDATE)

    def test_fire_delete(self) -> None:
        r = classify_pr_slice(title="🔥 Remove dead flag")
        self.assertEqual(r.primary_slice, SLICE_DELETE)

    def test_bug_hotfix(self) -> None:
        r = classify_pr_slice(title="🐞 hotfix: queue race")
        self.assertEqual(r.primary_slice, SLICE_HOTFIX)
        self.assertTrue(r.is_exception)

    def test_check_test_only(self) -> None:
        r = classify_pr_slice(title="✅ Add regression test")
        self.assertEqual(r.primary_slice, SLICE_TEST_ONLY)

    def test_memo_docs_only(self) -> None:
        r = classify_pr_slice(title="📝 audit doc")
        self.assertEqual(r.primary_slice, SLICE_DOCS_ONLY)


# ---------------------------------------------------------------------------
# Keyword scan (lower confidence)
# ---------------------------------------------------------------------------


class KeywordScanTests(unittest.TestCase):
    def test_create_keyword(self) -> None:
        r = classify_pr_slice(title="Introduce new helper module")
        self.assertEqual(r.primary_slice, SLICE_CREATE)
        self.assertLessEqual(r.confidence, 0.6)

    def test_update_keyword(self) -> None:
        r = classify_pr_slice(title="Refactor existing wrapper")
        self.assertEqual(r.primary_slice, SLICE_UPDATE)

    def test_delete_keyword(self) -> None:
        r = classify_pr_slice(title="Deprecate old endpoint")
        self.assertEqual(r.primary_slice, SLICE_DELETE)

    def test_read_keyword(self) -> None:
        r = classify_pr_slice(
            title="Surface status metric",
            body="진단 가능하도록 metric 조회 노출",
        )
        self.assertEqual(r.primary_slice, SLICE_READ)


# ---------------------------------------------------------------------------
# Mixed detection
# ---------------------------------------------------------------------------


class MixedDetectionTests(unittest.TestCase):
    def test_create_and_delete_keywords_yield_mixed(self) -> None:
        r = classify_pr_slice(
            title="Add new module and remove old one",
        )
        self.assertEqual(r.primary_slice, SLICE_MIXED)
        self.assertTrue(r.is_mixed)
        self.assertIsNotNone(r.split_recommendation)
        self.assertIn("분할", r.split_recommendation or "")

    def test_emoji_plus_secondary_keywords_promotes_to_mixed(self) -> None:
        # ✨ (create) + body mentions remove → MIXED.
        r = classify_pr_slice(
            title="✨ Add helper",
            body="기존 deprecated path 도 제거",
        )
        self.assertEqual(r.primary_slice, SLICE_MIXED)
        self.assertGreaterEqual(len(r.secondary_slices), 2)


# ---------------------------------------------------------------------------
# Tiny config
# ---------------------------------------------------------------------------


class TinyConfigTests(unittest.TestCase):
    def test_tiny_config_file(self) -> None:
        r = classify_pr_slice(
            title="bump version",
            changed_files=("pyproject.toml",),
            changed_lines=2,
            test_lines=0,
        )
        self.assertEqual(r.primary_slice, SLICE_TINY_CONFIG)

    def test_config_over_10_lines_not_tiny(self) -> None:
        r = classify_pr_slice(
            title="env overhaul",
            changed_files=("pyproject.toml",),
            changed_lines=50,
        )
        self.assertNotEqual(r.primary_slice, SLICE_TINY_CONFIG)


# ---------------------------------------------------------------------------
# Size warning + split recommendation
# ---------------------------------------------------------------------------


class SizeWarningTests(unittest.TestCase):
    def test_over_800_impl_lines_warns(self) -> None:
        r = classify_pr_slice(
            title="✨ Big new module",
            changed_files=("src/foo.py",),
            changed_lines=1200,
            test_lines=0,
        )
        self.assertEqual(r.primary_slice, SLICE_CREATE)
        self.assertTrue(r.size_warning)
        self.assertIsNotNone(r.split_recommendation)
        self.assertIn("800", r.split_recommendation or "")

    def test_test_lines_excluded_from_size_warning(self) -> None:
        # 1000 changed, 700 test → 300 impl < threshold → no warning.
        r = classify_pr_slice(
            title="✨ Module + tests",
            changed_files=("src/foo.py", "tests/foo/test_x.py"),
            changed_lines=1000,
            test_lines=700,
        )
        self.assertEqual(r.changed_lines_excluding_tests, 300)
        self.assertFalse(r.size_warning)

    def test_under_threshold_no_warning(self) -> None:
        r = classify_pr_slice(title="✨ Small module", changed_lines=500)
        self.assertFalse(r.size_warning)


# ---------------------------------------------------------------------------
# Status summary line
# ---------------------------------------------------------------------------


class StatusSummaryLineTests(unittest.TestCase):
    def test_mixed_line_warns(self) -> None:
        r = classify_pr_slice(
            title="Add and remove things",
        )
        line = r.status_summary_line()
        self.assertIn("⚠️", line)
        self.assertIn("MIXED", line)

    def test_size_warning_in_line(self) -> None:
        r = classify_pr_slice(
            title="✨ Big new module",
            changed_lines=1500,
            test_lines=0,
        )
        self.assertIn("⚠️", r.status_summary_line())
        self.assertIn("800", r.status_summary_line())


class RoundTripTests(unittest.TestCase):
    def test_to_dict_full_round_trip(self) -> None:
        r = classify_pr_slice(
            title="✨ New module",
            changed_lines=200,
        )
        payload = r.to_dict()
        self.assertEqual(payload["primary_slice"], SLICE_CREATE)
        self.assertFalse(payload["size_warning"])
        self.assertEqual(payload["changed_lines_excluding_tests"], 200)


if __name__ == "__main__":
    unittest.main()
