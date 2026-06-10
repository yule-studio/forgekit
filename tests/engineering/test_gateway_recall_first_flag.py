"""F16 — gateway recall-first opt-in flag + coverage attach seam.

This test pins the **infrastructure** F16 PR-1 commit 5 lands. The
router does not yet branch on ``prefer_recall_first_gateway`` — a
follow-up commit will rewire ``_run_runtime_preflight`` to call
``decide_gateway``. What this commit *does* land:

  1. ``EngineeringRouteContext.prefer_recall_first_gateway`` field
     with a safe ``False`` default and an env opt-in
     (``YULE_GATEWAY_RECALL_FIRST_ENABLED``).
  2. ``_attach_recall_coverage`` helper that fills
     ``RuntimeRecallResult.coverage`` defensively (any scorer raise
     degrades to low+stale).
  3. The preflight already calls the helper so every observed recall
     result now carries a coverage score for observability.
"""

from __future__ import annotations

import os
import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.runtime import (
    COVERAGE_HIGH,
    COVERAGE_LOW,
    RuntimeRecallResult,
)
from yule_engineering.discord.engineering_channel_router import (
    EngineeringRouteContext,
    _attach_recall_coverage,
    _optional_bool_env,
)


class OptInFlagTests(unittest.TestCase):
    def test_default_flag_is_false(self) -> None:
        ctx = EngineeringRouteContext()
        self.assertFalse(ctx.prefer_recall_first_gateway)

    def test_explicit_true_flag_preserved(self) -> None:
        ctx = EngineeringRouteContext(prefer_recall_first_gateway=True)
        self.assertTrue(ctx.prefer_recall_first_gateway)

    def test_from_env_reads_truthy_value(self) -> None:
        os.environ.pop("YULE_GATEWAY_RECALL_FIRST_ENABLED", None)
        try:
            os.environ["YULE_GATEWAY_RECALL_FIRST_ENABLED"] = "true"
            ctx = EngineeringRouteContext.from_env()
            self.assertTrue(ctx.prefer_recall_first_gateway)
        finally:
            os.environ.pop("YULE_GATEWAY_RECALL_FIRST_ENABLED", None)

    def test_from_env_unset_defaults_to_false(self) -> None:
        os.environ.pop("YULE_GATEWAY_RECALL_FIRST_ENABLED", None)
        ctx = EngineeringRouteContext.from_env()
        self.assertFalse(ctx.prefer_recall_first_gateway)

    def test_from_env_other_truthy_words(self) -> None:
        for raw in ("1", "yes", "on", "TRUE", "Yes"):
            with self.subTest(raw=raw):
                os.environ["YULE_GATEWAY_RECALL_FIRST_ENABLED"] = raw
                try:
                    self.assertTrue(_optional_bool_env("YULE_GATEWAY_RECALL_FIRST_ENABLED"))
                finally:
                    os.environ.pop("YULE_GATEWAY_RECALL_FIRST_ENABLED", None)

    def test_from_env_falsy_values(self) -> None:
        for raw in ("0", "false", "no", "off", ""):
            with self.subTest(raw=raw):
                os.environ["YULE_GATEWAY_RECALL_FIRST_ENABLED"] = raw
                try:
                    self.assertFalse(_optional_bool_env("YULE_GATEWAY_RECALL_FIRST_ENABLED"))
                finally:
                    os.environ.pop("YULE_GATEWAY_RECALL_FIRST_ENABLED", None)


class CoverageAttachTests(unittest.TestCase):
    def test_attach_low_for_empty_recall(self) -> None:
        recall = _attach_recall_coverage(RuntimeRecallResult())
        self.assertIsNotNone(recall.coverage)
        self.assertEqual(recall.coverage.level, COVERAGE_LOW)
        self.assertTrue(recall.coverage.stale)

    def test_attach_preserves_recall_fields(self) -> None:
        original = RuntimeRecallResult(
            matched_session_id="s1",
            confidence="medium",
            reason="x",
        )
        attached = _attach_recall_coverage(original)
        self.assertEqual(attached.matched_session_id, "s1")
        self.assertEqual(attached.confidence, "medium")
        self.assertEqual(attached.reason, "x")
        self.assertIsNotNone(attached.coverage)

    def test_attach_is_idempotent(self) -> None:
        # Running through the helper twice yields the same coverage —
        # important because the preflight + future gateway path may
        # both want to scan the recall result.
        recall = _attach_recall_coverage(RuntimeRecallResult())
        recall2 = _attach_recall_coverage(recall)
        self.assertEqual(recall.coverage.level, recall2.coverage.level)
        self.assertEqual(recall.coverage.stale, recall2.coverage.stale)


if __name__ == "__main__":
    unittest.main()
