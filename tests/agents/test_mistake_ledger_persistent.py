"""Durable mistake ledger — issue #89 round 1 contract.

Pins the F2 hookify ledger contract:

  * ``record_mistake`` is idempotent on ``(role, pattern, signature)``
    — repeat calls bump ``occurrences`` and advance ``last_seen``.
  * Blocker level escalates one-way (ADVISORY → WARNING → BLOCK); a
    milder later occurrence never relaxes the stored level.
  * ``find_similar`` is token Jaccard with a configurable threshold;
    sub-threshold rows are dropped.
  * ``list_for_role`` returns the most recent unresolved rows.
  * SQLite persistence round-trip — closing and re-opening the ledger
    against the same file returns the same records.
  * ``resolve`` is the only path to flag a row resolved (auto-dismiss
    is not exposed); calling ``resolve`` twice is a no-op.
  * ``prune_old_resolved`` deletes resolved-and-aged rows and leaves
    unresolved rows alone.
  * ``mistake_candidate_from_postmortem`` is deterministic.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.learning.mistake_ledger import (
    BlockerLevel,
    MistakeLedger,
    MistakeRecord,
    jaccard_similarity,
    max_blocker_level,
    mistake_candidate_from_postmortem,
)


class _LedgerTestBase(unittest.TestCase):
    """Shared scaffolding — every test gets a fresh ``:memory:`` ledger."""

    def setUp(self) -> None:
        self.ledger = MistakeLedger(database_path=":memory:")

    def tearDown(self) -> None:
        self.ledger.close()


class RecordMistakeLifecycleTests(_LedgerTestBase):
    def test_first_record_starts_at_one(self) -> None:
        record = self.ledger.record_mistake(
            role="backend-engineer",
            pattern="ci_test_fail",
            signature="auth login test failed",
            when="2026-05-01T00:00:00+00:00",
        )
        self.assertEqual(record.occurrences, 1)
        self.assertEqual(record.role, "backend-engineer")
        self.assertEqual(record.pattern, "ci_test_fail")
        self.assertEqual(record.first_seen, record.last_seen)
        self.assertEqual(record.blocker_level, BlockerLevel.ADVISORY)
        self.assertIsNone(record.resolved_at)
        self.assertIsNone(record.postmortem_ref)

    def test_repeat_bumps_occurrences_and_advances_last_seen(self) -> None:
        first = self.ledger.record_mistake(
            role="backend-engineer",
            pattern="ci_test_fail",
            signature="auth login test failed",
            when="2026-05-01T00:00:00+00:00",
        )
        second = self.ledger.record_mistake(
            role="backend-engineer",
            pattern="ci_test_fail",
            signature="auth login test failed",
            when="2026-05-02T00:00:00+00:00",
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.occurrences, 2)
        self.assertEqual(second.first_seen, "2026-05-01T00:00:00+00:00")
        self.assertEqual(second.last_seen, "2026-05-02T00:00:00+00:00")

    def test_blocker_level_only_escalates(self) -> None:
        # Start at WARNING, then a later ADVISORY claim should not relax.
        self.ledger.record_mistake(
            role="backend-engineer",
            pattern="ci_test_fail",
            signature="auth login failure",
            blocker_level=BlockerLevel.WARNING,
        )
        record = self.ledger.record_mistake(
            role="backend-engineer",
            pattern="ci_test_fail",
            signature="auth login failure",
            blocker_level=BlockerLevel.ADVISORY,
        )
        self.assertEqual(record.blocker_level, BlockerLevel.WARNING)

        # An explicit BLOCK pushes it the rest of the way.
        record = self.ledger.record_mistake(
            role="backend-engineer",
            pattern="ci_test_fail",
            signature="auth login failure",
            blocker_level=BlockerLevel.BLOCK,
        )
        self.assertEqual(record.blocker_level, BlockerLevel.BLOCK)

    def test_distinct_tuple_creates_new_row(self) -> None:
        first = self.ledger.record_mistake(
            role="backend-engineer",
            pattern="ci_test_fail",
            signature="auth login failure",
        )
        second = self.ledger.record_mistake(
            role="backend-engineer",
            pattern="ci_test_fail",
            signature="payments checkout failure",
        )
        self.assertNotEqual(first.id, second.id)
        rows = self.ledger.list_for_role("backend-engineer")
        self.assertEqual({r.id for r in rows}, {first.id, second.id})

    def test_record_mistake_requires_non_empty_role_and_signature(self) -> None:
        with self.assertRaises(ValueError):
            self.ledger.record_mistake(
                role="",
                pattern="ci_test_fail",
                signature="x",
            )
        with self.assertRaises(ValueError):
            self.ledger.record_mistake(
                role="qa",
                pattern="ci_test_fail",
                signature="",
            )

    def test_record_mistake_postmortem_ref_stored(self) -> None:
        record = self.ledger.record_mistake(
            role="qa",
            pattern="missing_regression",
            signature="missing regression check on auth flow",
            postmortem_ref="postmortem://issue-89/audit-1",
        )
        fetched = self.ledger.get(record.id)
        assert fetched is not None
        self.assertEqual(
            fetched.postmortem_ref, "postmortem://issue-89/audit-1"
        )


class FindSimilarTests(_LedgerTestBase):
    def test_exact_signature_match_returns_record(self) -> None:
        self.ledger.record_mistake(
            role="backend-engineer",
            pattern="ci_test_fail",
            signature="auth login test failed on CI",
        )
        matches = self.ledger.find_similar(
            role="backend-engineer",
            signature="auth login test failed on CI",
            threshold=0.9,
        )
        self.assertEqual(len(matches), 1)

    def test_partial_overlap_matches_at_lower_threshold(self) -> None:
        self.ledger.record_mistake(
            role="backend-engineer",
            pattern="ci_test_fail",
            signature="auth login test failed on CI",
        )
        # Different signature with several shared tokens — Jaccard is
        # high enough at 0.4 but not at 0.9.
        low = self.ledger.find_similar(
            role="backend-engineer",
            signature="auth login regression failed",
            threshold=0.3,
        )
        high = self.ledger.find_similar(
            role="backend-engineer",
            signature="auth login regression failed",
            threshold=0.95,
        )
        self.assertGreaterEqual(len(low), 1)
        self.assertEqual(len(high), 0)

    def test_other_role_signatures_are_not_returned(self) -> None:
        self.ledger.record_mistake(
            role="backend-engineer",
            pattern="ci_test_fail",
            signature="auth login test failed",
        )
        self.ledger.record_mistake(
            role="qa-engineer",
            pattern="ci_test_fail",
            signature="auth login test failed",
        )
        backend_matches = self.ledger.find_similar(
            role="backend-engineer",
            signature="auth login test failed",
            threshold=0.5,
        )
        self.assertEqual(len(backend_matches), 1)
        self.assertEqual(backend_matches[0].role, "backend-engineer")

    def test_find_similar_filters_resolved_by_default(self) -> None:
        record = self.ledger.record_mistake(
            role="backend-engineer",
            pattern="ci_test_fail",
            signature="auth login test failed",
        )
        self.ledger.resolve(record.id)
        matches = self.ledger.find_similar(
            role="backend-engineer",
            signature="auth login test failed",
            threshold=0.5,
        )
        self.assertEqual(matches, ())
        with_resolved = self.ledger.find_similar(
            role="backend-engineer",
            signature="auth login test failed",
            threshold=0.5,
            include_resolved=True,
        )
        self.assertEqual(len(with_resolved), 1)


class ListForRoleTests(_LedgerTestBase):
    def test_returns_most_recent_first(self) -> None:
        self.ledger.record_mistake(
            role="backend-engineer",
            pattern="a",
            signature="alpha",
            when="2026-05-01T00:00:00+00:00",
        )
        self.ledger.record_mistake(
            role="backend-engineer",
            pattern="b",
            signature="beta",
            when="2026-05-03T00:00:00+00:00",
        )
        self.ledger.record_mistake(
            role="backend-engineer",
            pattern="c",
            signature="gamma",
            when="2026-05-02T00:00:00+00:00",
        )
        rows = self.ledger.list_for_role("backend-engineer")
        self.assertEqual(
            [r.pattern for r in rows], ["b", "c", "a"]
        )

    def test_respects_limit(self) -> None:
        for i in range(5):
            self.ledger.record_mistake(
                role="backend-engineer",
                pattern=f"p{i}",
                signature=f"signature {i}",
                when=f"2026-05-{i+1:02d}T00:00:00+00:00",
            )
        rows = self.ledger.list_for_role("backend-engineer", limit=2)
        self.assertEqual(len(rows), 2)


class ResolveLifecycleTests(_LedgerTestBase):
    def test_resolve_marks_row_resolved(self) -> None:
        record = self.ledger.record_mistake(
            role="backend-engineer",
            pattern="x",
            signature="signature here",
        )
        resolved = self.ledger.resolve(
            record.id, resolved_at="2026-05-10T00:00:00+00:00"
        )
        assert resolved is not None
        self.assertEqual(resolved.resolved_at, "2026-05-10T00:00:00+00:00")
        self.assertTrue(resolved.is_resolved())

    def test_resolve_unknown_id_returns_none(self) -> None:
        self.assertIsNone(self.ledger.resolve("does-not-exist"))

    def test_resolve_is_idempotent(self) -> None:
        record = self.ledger.record_mistake(
            role="qa",
            pattern="x",
            signature="y",
        )
        first = self.ledger.resolve(
            record.id, resolved_at="2026-05-10T00:00:00+00:00"
        )
        second = self.ledger.resolve(
            record.id, resolved_at="2026-05-12T00:00:00+00:00"
        )
        assert first is not None and second is not None
        # Second call must preserve the original resolved_at stamp.
        self.assertEqual(first.resolved_at, second.resolved_at)

    def test_record_again_after_resolve_reopens_row(self) -> None:
        record = self.ledger.record_mistake(
            role="backend-engineer",
            pattern="x",
            signature="alpha signature",
        )
        self.ledger.resolve(record.id)
        bumped = self.ledger.record_mistake(
            role="backend-engineer",
            pattern="x",
            signature="alpha signature",
        )
        self.assertEqual(bumped.id, record.id)
        self.assertEqual(bumped.occurrences, 2)
        self.assertIsNone(bumped.resolved_at)


class PruneRetentionTests(_LedgerTestBase):
    def test_prune_removes_old_resolved_rows(self) -> None:
        record = self.ledger.record_mistake(
            role="qa",
            pattern="p",
            signature="some signature",
            when="2025-01-01T00:00:00+00:00",
        )
        # Resolve at a date well before our `now` reference.
        self.ledger.resolve(
            record.id, resolved_at="2025-06-01T00:00:00+00:00"
        )
        now = datetime(2026, 5, 1, tzinfo=timezone.utc)
        deleted = self.ledger.prune_old_resolved(
            retention_days=30, now=now
        )
        self.assertEqual(deleted, 1)
        self.assertEqual(self.ledger.all_records(), ())

    def test_prune_leaves_unresolved_rows_alone(self) -> None:
        self.ledger.record_mistake(
            role="qa",
            pattern="p",
            signature="alpha",
            when="2025-01-01T00:00:00+00:00",
        )
        now = datetime(2026, 5, 1, tzinfo=timezone.utc)
        deleted = self.ledger.prune_old_resolved(retention_days=30, now=now)
        self.assertEqual(deleted, 0)
        self.assertEqual(len(self.ledger.all_records()), 1)

    def test_prune_respects_retention_window(self) -> None:
        record = self.ledger.record_mistake(
            role="qa",
            pattern="p",
            signature="alpha",
        )
        # Resolve recently (within retention window).
        self.ledger.resolve(
            record.id, resolved_at="2026-04-25T00:00:00+00:00"
        )
        now = datetime(2026, 5, 1, tzinfo=timezone.utc)
        deleted = self.ledger.prune_old_resolved(retention_days=30, now=now)
        # 6 days < 30 days retention → still kept.
        self.assertEqual(deleted, 0)
        self.assertEqual(len(self.ledger.all_records()), 1)


class PersistenceRoundTripTests(unittest.TestCase):
    def test_on_disk_records_survive_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "mistake.sqlite3"
            first = MistakeLedger(database_path=db_path)
            try:
                first.record_mistake(
                    role="backend-engineer",
                    pattern="ci",
                    signature="auth login failure",
                    blocker_level=BlockerLevel.WARNING,
                    when="2026-05-01T00:00:00+00:00",
                )
            finally:
                first.close()
            second = MistakeLedger(database_path=db_path)
            try:
                rows = second.list_for_role("backend-engineer")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0].signature, "auth login failure")
                self.assertEqual(rows[0].blocker_level, BlockerLevel.WARNING)
                # Recording the same tuple in the reopened ledger bumps
                # the existing row — proof the unique index survived.
                bumped = second.record_mistake(
                    role="backend-engineer",
                    pattern="ci",
                    signature="auth login failure",
                    when="2026-05-02T00:00:00+00:00",
                )
                self.assertEqual(bumped.occurrences, 2)
            finally:
                second.close()


class JaccardSimilarityTests(unittest.TestCase):
    def test_identical_strings_score_one(self) -> None:
        self.assertEqual(
            jaccard_similarity("auth login fail", "auth login fail"), 1.0
        )

    def test_disjoint_strings_score_zero(self) -> None:
        self.assertEqual(
            jaccard_similarity("alpha", "bravo charlie"), 0.0
        )

    def test_partial_overlap_is_between_zero_and_one(self) -> None:
        score = jaccard_similarity("auth login fail", "auth signup fail")
        self.assertGreater(score, 0.0)
        self.assertLess(score, 1.0)


class PostmortemCandidateTests(unittest.TestCase):
    def test_returns_record_with_deterministic_id(self) -> None:
        entry = {
            "action": "failure_postmortem_create",
            "role": "backend-engineer",
            "summary": "auth login regression",
            "reason": "auth login flow flakiness",
            "recorded_at": "2026-05-01T00:00:00+00:00",
            "entry_id": "audit-entry-42",
            "job_type": "coding_execute",
        }
        a = mistake_candidate_from_postmortem(entry)
        b = mistake_candidate_from_postmortem(entry)
        assert a is not None and b is not None
        self.assertEqual(a.id, b.id)
        self.assertEqual(a.signature, "auth login flow flakiness")
        self.assertEqual(a.pattern, "coding_execute")
        self.assertEqual(a.role, "backend-engineer")

    def test_returns_none_when_role_missing(self) -> None:
        self.assertIsNone(
            mistake_candidate_from_postmortem(
                {
                    "action": "failure_postmortem_create",
                    "summary": "no role",
                    "reason": "no role",
                }
            )
        )

    def test_returns_none_when_action_not_postmortem(self) -> None:
        self.assertIsNone(
            mistake_candidate_from_postmortem(
                {
                    "action": "queue_inspect",
                    "role": "backend-engineer",
                    "reason": "should not be a candidate",
                }
            )
        )

    def test_returns_none_when_signature_source_missing(self) -> None:
        self.assertIsNone(
            mistake_candidate_from_postmortem(
                {
                    "action": "failure_postmortem_create",
                    "role": "backend-engineer",
                }
            )
        )

    def test_candidate_persists_into_ledger_without_duplicating(self) -> None:
        entry = {
            "action": "failure_postmortem_create",
            "role": "backend-engineer",
            "reason": "auth login flow flakiness",
            "recorded_at": "2026-05-01T00:00:00+00:00",
            "entry_id": "audit-entry-42",
            "job_type": "coding_execute",
        }
        candidate = mistake_candidate_from_postmortem(entry)
        assert candidate is not None
        ledger = MistakeLedger(database_path=":memory:")
        try:
            first = ledger.record_mistake(
                role=candidate.role,
                pattern=candidate.pattern,
                signature=candidate.signature,
                postmortem_ref=candidate.postmortem_ref,
                blocker_level=candidate.blocker_level,
            )
            second = ledger.record_mistake(
                role=candidate.role,
                pattern=candidate.pattern,
                signature=candidate.signature,
                postmortem_ref=candidate.postmortem_ref,
                blocker_level=candidate.blocker_level,
            )
            self.assertEqual(first.id, second.id)
            self.assertEqual(second.occurrences, 2)
        finally:
            ledger.close()


class MaxBlockerLevelTests(unittest.TestCase):
    def test_max_returns_highest_level(self) -> None:
        self.assertEqual(
            max_blocker_level(
                BlockerLevel.ADVISORY,
                BlockerLevel.BLOCK,
                BlockerLevel.WARNING,
            ),
            BlockerLevel.BLOCK,
        )

    def test_max_of_empty_defaults_advisory(self) -> None:
        self.assertEqual(max_blocker_level(), BlockerLevel.ADVISORY)


if __name__ == "__main__":
    unittest.main()
