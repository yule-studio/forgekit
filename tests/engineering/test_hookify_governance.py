"""Hookify (F2 / issue #89) governance regression — hard-rail integrity gate.

Pulls the F2 hard rails into one suite so a single rename / condition
flip cannot silently regress them:

  1. ``BLOCK`` verdict prevents auto-progress — the verdict carries
     ``recommend_needs_approval=True`` and the suggested action
     references the ``needs_approval`` lane.
  2. The mistake ledger is bounded by an explicit retention API —
     ``prune_old_resolved`` is the only delete path and it ignores
     unresolved rows so the ledger does not silently drop a live
     mistake. Without an explicit operator call the ledger does NOT
     auto-prune.
  3. There is no auto-dismiss — every path that flips
     ``resolved_at`` goes through :meth:`MistakeLedger.resolve`. Bumping
     a record (or recording it many times) never resolves it.
  4. ``mistake_candidate_from_postmortem`` is deterministic — the
     same audit entry always yields the same id / signature, so
     re-running the postmortem producer never duplicates the row.
  5. The preflight pipeline helper exposes ``should_proceed=False`` on
     BLOCK so a caller wiring the hook cannot accidentally enter the
     pipeline before the operator routes the work to ``needs_approval``.
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
    mistake_candidate_from_postmortem,
)
from yule_engineering.agents.learning.preflight import (
    judge_preflight,
    preflight_pipeline_hook,
)


_FIXED_NOW = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)


class _GovTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = MistakeLedger(database_path=":memory:")

    def tearDown(self) -> None:
        self.ledger.close()


class BlockVerdictHardRailTests(_GovTestBase):
    """Hard rail #1 — BLOCK never auto-progresses."""

    def test_block_verdict_recommends_needs_approval(self) -> None:
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
        self.assertTrue(verdict.is_block)
        self.assertTrue(verdict.recommend_needs_approval)
        self.assertIn("needs_approval", verdict.suggested_action)

    def test_block_pipeline_hook_blocks_should_proceed(self) -> None:
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
        # The single boolean a wiring caller branches on — must be
        # False so the worker cannot accidentally enter the pipeline.
        self.assertFalse(result.should_proceed)


class LedgerRetentionHardRailTests(_GovTestBase):
    """Hard rail #2 — the ledger never grows unbounded but only the
    explicit ``prune_old_resolved`` API may delete rows."""

    def test_unresolved_rows_are_never_pruned(self) -> None:
        # Record a *very* old mistake and never resolve it.
        self.ledger.record_mistake(
            role="qa-engineer",
            pattern="missing_regression",
            signature="missing regression guard on auth flow",
            when="2020-01-01T00:00:00+00:00",
        )
        now = datetime(2026, 5, 1, tzinfo=timezone.utc)
        deleted = self.ledger.prune_old_resolved(retention_days=1, now=now)
        # Zero deletes — only resolved rows are eligible.
        self.assertEqual(deleted, 0)
        self.assertEqual(len(self.ledger.all_records()), 1)

    def test_prune_only_runs_when_called(self) -> None:
        record = self.ledger.record_mistake(
            role="qa-engineer",
            pattern="missing_regression",
            signature="missing regression guard",
        )
        self.ledger.resolve(
            record.id, resolved_at="2020-01-01T00:00:00+00:00"
        )
        # Just recording more mistakes does NOT auto-prune the old one
        # — the operator must call prune_old_resolved explicitly.
        for i in range(5):
            self.ledger.record_mistake(
                role="qa-engineer",
                pattern=f"other_{i}",
                signature=f"unrelated signature {i}",
            )
        self.assertEqual(
            len(self.ledger.all_records(include_resolved=True)), 6
        )
        deleted = self.ledger.prune_old_resolved(
            retention_days=30,
            now=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(deleted, 1)


class ResolveExplicitOnlyHardRailTests(_GovTestBase):
    """Hard rail #3 — ``resolved_at`` only flips via :meth:`resolve`."""

    def test_repeated_record_does_not_resolve_row(self) -> None:
        record = self.ledger.record_mistake(
            role="backend-engineer",
            pattern="ci_test_fail",
            signature="auth login test failed",
            blocker_level=BlockerLevel.WARNING,
        )
        for _ in range(10):
            self.ledger.record_mistake(
                role="backend-engineer",
                pattern="ci_test_fail",
                signature="auth login test failed",
            )
        fetched = self.ledger.get(record.id)
        assert fetched is not None
        # Even after 10 bumps the row is still unresolved.
        self.assertIsNone(fetched.resolved_at)
        self.assertEqual(fetched.occurrences, 11)

    def test_record_after_resolve_clears_resolved_stamp(self) -> None:
        # The "re-open on recurrence" rule is intentional — the same
        # mistake biting again must unconditionally raise the
        # preflight signal regardless of an earlier operator dismiss.
        # Without this rule a stale dismissal could silently let a
        # repeating failure through.
        record = self.ledger.record_mistake(
            role="backend-engineer",
            pattern="ci_test_fail",
            signature="auth login test failed",
        )
        self.ledger.resolve(record.id)
        fetched = self.ledger.get(record.id)
        assert fetched is not None
        self.assertIsNotNone(fetched.resolved_at)
        bumped = self.ledger.record_mistake(
            role="backend-engineer",
            pattern="ci_test_fail",
            signature="auth login test failed",
        )
        self.assertIsNone(bumped.resolved_at)


class PostmortemDeterminismHardRailTests(unittest.TestCase):
    """Hard rail #4 — postmortem → candidate is deterministic."""

    def test_same_audit_entry_yields_equal_candidates(self) -> None:
        entry = {
            "action": "failure_postmortem_create",
            "role": "backend-engineer",
            "reason": "auth login flow flakiness",
            "recorded_at": "2026-05-01T00:00:00+00:00",
            "entry_id": "audit-42",
            "job_type": "coding_execute",
        }
        first = mistake_candidate_from_postmortem(entry)
        second = mistake_candidate_from_postmortem(entry)
        assert first is not None and second is not None
        self.assertEqual(first.to_payload(), second.to_payload())


class PreflightHookSurfaceHardRailTests(_GovTestBase):
    """Hard rail #5 — pipeline helper exposes the BLOCK decision clearly."""

    def test_stamp_payload_contains_needs_approval_recommendation(self) -> None:
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
        stamp = result.stamp.get("preflight")
        assert isinstance(stamp, dict)
        self.assertTrue(stamp["recommend_needs_approval"])
        self.assertEqual(stamp["level"], BlockerLevel.BLOCK.value)


if __name__ == "__main__":
    unittest.main()
