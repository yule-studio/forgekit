"""Preflight judgement seam — issue #89 round 1 contract.

Pins the F2 preflight contract:

  * Empty ledger → ADVISORY verdict with no matched mistakes.
  * Matched ADVISORY-only records → ADVISORY verdict, suggested_action
    == "주의".
  * Matched WARNING records → WARNING verdict, suggested_action ==
    "재검토 권장".
  * Any matched BLOCK record → BLOCK verdict, suggested_action ==
    "needs_approval 로 라우팅", ``recommend_needs_approval == True``.
  * The verdict level is the *maximum* of matched mistake levels.
  * ``preflight_pipeline_hook`` wraps the verdict in a pipeline-ready
    bundle that sets ``should_proceed=False`` only on BLOCK.
  * ``judge_preflight`` is deterministic given a pinned ``now``.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.learning.mistake_ledger import (
    BlockerLevel,
    MistakeLedger,
)
from yule_engineering.agents.learning.preflight import (
    DEFAULT_PREFLIGHT_SIMILARITY,
    PreflightHookResult,
    PreflightVerdict,
    judge_preflight,
    preflight_pipeline_hook,
)


_FIXED_NOW = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)


class _PreflightTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = MistakeLedger(database_path=":memory:")

    def tearDown(self) -> None:
        self.ledger.close()


class EmptyLedgerTests(_PreflightTestBase):
    def test_empty_ledger_returns_advisory_with_no_matches(self) -> None:
        verdict = judge_preflight(
            role="backend-engineer",
            task_signature="auth login regression",
            ledger=self.ledger,
            now=_FIXED_NOW,
        )
        self.assertEqual(verdict.level, BlockerLevel.ADVISORY)
        self.assertEqual(verdict.matched_mistakes, ())
        self.assertEqual(verdict.suggested_action, "주의")
        self.assertFalse(verdict.recommend_needs_approval)
        self.assertIn("매칭 없음", verdict.reason)

    def test_missing_role_short_circuits_advisory(self) -> None:
        verdict = judge_preflight(
            role="",
            task_signature="auth login regression",
            ledger=self.ledger,
            now=_FIXED_NOW,
        )
        self.assertEqual(verdict.level, BlockerLevel.ADVISORY)
        self.assertEqual(verdict.matched_mistakes, ())
        self.assertIn("role 미지정", verdict.reason)

    def test_missing_signature_short_circuits_advisory(self) -> None:
        verdict = judge_preflight(
            role="backend-engineer",
            task_signature="",
            ledger=self.ledger,
            now=_FIXED_NOW,
        )
        self.assertEqual(verdict.level, BlockerLevel.ADVISORY)
        self.assertEqual(verdict.matched_mistakes, ())
        self.assertIn("task_signature 미지정", verdict.reason)


class VerdictMatrixTests(_PreflightTestBase):
    def test_advisory_only_match_returns_advisory(self) -> None:
        self.ledger.record_mistake(
            role="backend-engineer",
            pattern="ci_test_fail",
            signature="auth login test failure",
            blocker_level=BlockerLevel.ADVISORY,
        )
        verdict = judge_preflight(
            role="backend-engineer",
            task_signature="auth login test failure",
            ledger=self.ledger,
            now=_FIXED_NOW,
        )
        self.assertEqual(verdict.level, BlockerLevel.ADVISORY)
        self.assertEqual(verdict.suggested_action, "주의")
        self.assertEqual(len(verdict.matched_mistakes), 1)

    def test_warning_match_returns_warning(self) -> None:
        self.ledger.record_mistake(
            role="backend-engineer",
            pattern="ci_test_fail",
            signature="auth login test failure",
            blocker_level=BlockerLevel.WARNING,
        )
        verdict = judge_preflight(
            role="backend-engineer",
            task_signature="auth login test failure",
            ledger=self.ledger,
            now=_FIXED_NOW,
        )
        self.assertEqual(verdict.level, BlockerLevel.WARNING)
        self.assertEqual(verdict.suggested_action, "재검토 권장")
        self.assertFalse(verdict.recommend_needs_approval)

    def test_block_match_returns_block_with_needs_approval(self) -> None:
        self.ledger.record_mistake(
            role="backend-engineer",
            pattern="force_push",
            signature="force push to main branch",
            blocker_level=BlockerLevel.BLOCK,
        )
        verdict = judge_preflight(
            role="backend-engineer",
            task_signature="force push to main branch",
            ledger=self.ledger,
            now=_FIXED_NOW,
        )
        self.assertEqual(verdict.level, BlockerLevel.BLOCK)
        self.assertEqual(
            verdict.suggested_action, "needs_approval 로 라우팅"
        )
        self.assertTrue(verdict.recommend_needs_approval)

    def test_max_level_is_chosen_across_matches(self) -> None:
        self.ledger.record_mistake(
            role="backend-engineer",
            pattern="ci_test_fail",
            signature="auth login test failure",
            blocker_level=BlockerLevel.ADVISORY,
        )
        self.ledger.record_mistake(
            role="backend-engineer",
            pattern="force_push",
            signature="auth login force push violation",
            blocker_level=BlockerLevel.BLOCK,
        )
        verdict = judge_preflight(
            role="backend-engineer",
            task_signature="auth login test failure violation",
            ledger=self.ledger,
            now=_FIXED_NOW,
            similarity_threshold=0.2,
        )
        # Both records contain "auth login" tokens and beat the lower
        # threshold; the highest level (BLOCK) must win.
        self.assertEqual(verdict.level, BlockerLevel.BLOCK)
        self.assertTrue(verdict.recommend_needs_approval)
        self.assertEqual(len(verdict.matched_mistakes), 2)


class ReasonSurfaceTests(_PreflightTestBase):
    def test_reason_includes_top_matched_patterns(self) -> None:
        for i in range(5):
            self.ledger.record_mistake(
                role="backend-engineer",
                pattern=f"ci_fail_{i}",
                signature=f"auth login flow regression {i}",
                blocker_level=BlockerLevel.WARNING,
            )
        verdict = judge_preflight(
            role="backend-engineer",
            task_signature="auth login flow regression",
            ledger=self.ledger,
            similarity_threshold=0.3,
            now=_FIXED_NOW,
        )
        self.assertEqual(verdict.level, BlockerLevel.WARNING)
        # Reason must mention at least one of the top patterns.
        self.assertTrue(
            any(f"ci_fail_{i}" in verdict.reason for i in range(5)),
            f"reason missing matched pattern reference: {verdict.reason}",
        )

    def test_resolved_mistakes_do_not_contribute(self) -> None:
        record = self.ledger.record_mistake(
            role="backend-engineer",
            pattern="ci_test_fail",
            signature="auth login test failure",
            blocker_level=BlockerLevel.BLOCK,
        )
        self.ledger.resolve(record.id)
        verdict = judge_preflight(
            role="backend-engineer",
            task_signature="auth login test failure",
            ledger=self.ledger,
            now=_FIXED_NOW,
        )
        self.assertEqual(verdict.level, BlockerLevel.ADVISORY)
        self.assertEqual(verdict.matched_mistakes, ())


class DeterminismTests(_PreflightTestBase):
    def test_same_inputs_yield_equal_verdict_payload(self) -> None:
        self.ledger.record_mistake(
            role="backend-engineer",
            pattern="ci_test_fail",
            signature="auth login test failure",
            blocker_level=BlockerLevel.WARNING,
        )
        a = judge_preflight(
            role="backend-engineer",
            task_signature="auth login test failure",
            ledger=self.ledger,
            now=_FIXED_NOW,
        )
        b = judge_preflight(
            role="backend-engineer",
            task_signature="auth login test failure",
            ledger=self.ledger,
            now=_FIXED_NOW,
        )
        self.assertEqual(a.to_payload(), b.to_payload())

    def test_default_threshold_constant_is_stable(self) -> None:
        # Constant pin — if this value is changed the regression
        # tests above need to be re-verified.
        self.assertGreaterEqual(DEFAULT_PREFLIGHT_SIMILARITY, 0.0)
        self.assertLessEqual(DEFAULT_PREFLIGHT_SIMILARITY, 1.0)


class PipelineHookTests(_PreflightTestBase):
    def test_advisory_keeps_should_proceed_true(self) -> None:
        result = preflight_pipeline_hook(
            role="backend-engineer",
            task_signature="auth login test failure",
            ledger=self.ledger,
            now=_FIXED_NOW,
        )
        self.assertIsInstance(result, PreflightHookResult)
        self.assertTrue(result.should_proceed)
        self.assertEqual(result.verdict.level, BlockerLevel.ADVISORY)
        self.assertIn("preflight", result.stamp)

    def test_block_drops_should_proceed_to_false(self) -> None:
        self.ledger.record_mistake(
            role="backend-engineer",
            pattern="force_push",
            signature="force push to main",
            blocker_level=BlockerLevel.BLOCK,
        )
        result = preflight_pipeline_hook(
            role="backend-engineer",
            task_signature="force push to main",
            ledger=self.ledger,
            now=_FIXED_NOW,
        )
        self.assertFalse(result.should_proceed)
        self.assertTrue(result.verdict.recommend_needs_approval)
        self.assertEqual(
            result.stamp["preflight"]["suggested_action"],
            "needs_approval 로 라우팅",
        )


if __name__ == "__main__":
    unittest.main()
