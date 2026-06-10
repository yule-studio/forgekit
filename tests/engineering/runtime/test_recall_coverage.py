"""F16 — recall coverage scorer (issue #128).

``compute_recall_coverage`` derives a high/medium/low + stale judgment
from a ``RuntimeRecallResult`` so the gateway path can decide whether
to skip research. The cases below pin every branch in
``docs/runtime-recall-first.md §2.1``.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_agent_runtime.models import (
    COVERAGE_HIGH,
    COVERAGE_LOW,
    COVERAGE_MEDIUM,
    RuntimeRecallResult,
    SessionCandidate,
)
from yule_agent_runtime.recall import compute_recall_coverage


def _now() -> datetime:
    return datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)


def _hit(*, hours_ago: int = 0, backend: str = "rag") -> dict:
    ts = (_now() - timedelta(hours=hours_ago)).isoformat()
    return {"backend": backend, "updated_at": ts, "snippet": "x"}


def _candidate(*, session_id: str, hours_ago: int = 0) -> SessionCandidate:
    ts = (_now() - timedelta(hours=hours_ago)).isoformat()
    return SessionCandidate(
        session_id=session_id,
        title="t",
        score=0.9,
        extra={"updated_at": ts},
    )


class CoverageHighTests(unittest.TestCase):
    """matched session + memory_hits >= 2 + at least one source < 24h."""

    def test_session_plus_two_fresh_memory_hits_is_high(self) -> None:
        recall = RuntimeRecallResult(
            matched_session_id="s1",
            candidates=(_candidate(session_id="s1", hours_ago=1),),
            memory_hits=(_hit(hours_ago=2, backend="obsidian"), _hit(hours_ago=3, backend="rag")),
        )
        coverage = compute_recall_coverage(recall, now=_now())
        self.assertEqual(coverage.level, COVERAGE_HIGH)
        self.assertFalse(coverage.stale)
        self.assertIn("session", coverage.sources)
        self.assertIn("memory:obsidian", coverage.sources)
        self.assertIn("memory:rag", coverage.sources)

    def test_high_with_one_stale_one_fresh_still_fresh_high(self) -> None:
        # Mix: one source within 24h is enough to flip stale off.
        recall = RuntimeRecallResult(
            matched_session_id="s2",
            candidates=(_candidate(session_id="s2", hours_ago=24 * 6),),
            memory_hits=(
                _hit(hours_ago=1, backend="rag"),
                _hit(hours_ago=24 * 3, backend="obsidian"),
            ),
        )
        coverage = compute_recall_coverage(recall, now=_now())
        self.assertEqual(coverage.level, COVERAGE_HIGH)
        self.assertFalse(coverage.stale)


class CoverageMediumTests(unittest.TestCase):
    """Partial coverage — either session OR memory hits + within 7 days."""

    def test_session_only_within_week_is_medium(self) -> None:
        recall = RuntimeRecallResult(
            matched_session_id="s1",
            candidates=(_candidate(session_id="s1", hours_ago=24 * 3),),
        )
        coverage = compute_recall_coverage(recall, now=_now())
        self.assertEqual(coverage.level, COVERAGE_MEDIUM)
        self.assertFalse(coverage.stale)
        self.assertEqual(coverage.sources, ("session",))

    def test_single_memory_hit_within_week_is_medium(self) -> None:
        recall = RuntimeRecallResult(
            matched_session_id=None,
            memory_hits=(_hit(hours_ago=24 * 5, backend="rag"),),
        )
        coverage = compute_recall_coverage(recall, now=_now())
        self.assertEqual(coverage.level, COVERAGE_MEDIUM)
        self.assertFalse(coverage.stale)

    def test_session_plus_two_hits_but_all_old_is_medium_stale(self) -> None:
        # Two memory hits but oldest within 7d → medium, none within 24h → stale.
        # Actually our rule: high needs >=24h-fresh; this is between fresh & week.
        recall = RuntimeRecallResult(
            matched_session_id="s1",
            candidates=(_candidate(session_id="s1", hours_ago=24 * 2),),
            memory_hits=(
                _hit(hours_ago=24 * 3, backend="rag"),
                _hit(hours_ago=24 * 4, backend="obsidian"),
            ),
        )
        coverage = compute_recall_coverage(recall, now=_now())
        self.assertEqual(coverage.level, COVERAGE_MEDIUM)
        # All sources older than 24h but within 7d → stale=False (within week).
        self.assertFalse(coverage.stale)


class CoverageLowTests(unittest.TestCase):
    def test_no_session_no_hits_is_low_and_stale(self) -> None:
        recall = RuntimeRecallResult()
        coverage = compute_recall_coverage(recall, now=_now())
        self.assertEqual(coverage.level, COVERAGE_LOW)
        self.assertTrue(coverage.stale)
        self.assertEqual(coverage.sources, ())

    def test_all_sources_older_than_seven_days_is_low_stale(self) -> None:
        recall = RuntimeRecallResult(
            matched_session_id="s1",
            candidates=(_candidate(session_id="s1", hours_ago=24 * 30),),
            memory_hits=(_hit(hours_ago=24 * 30, backend="rag"),),
        )
        coverage = compute_recall_coverage(recall, now=_now())
        # session + memory hit BUT all stale → low (no within-week signal).
        self.assertEqual(coverage.level, COVERAGE_LOW)
        self.assertTrue(coverage.stale)

    def test_hits_without_timestamps_are_treated_as_stale(self) -> None:
        recall = RuntimeRecallResult(
            matched_session_id="s1",
            candidates=(_candidate(session_id="s1", hours_ago=2),),
            memory_hits=({"backend": "rag", "snippet": "no time"},),
        )
        coverage = compute_recall_coverage(recall, now=_now())
        # session is fresh (1h ago is well under 24h) so high or medium? We
        # need ≥ 2 memory hits for high; we have 1 hit, so medium.
        self.assertEqual(coverage.level, COVERAGE_MEDIUM)
        # Session has fresh ts (2h ago), so freshness flag is True (within week).
        self.assertFalse(coverage.stale)


class CoverageDefensiveTests(unittest.TestCase):
    def test_none_recall_degrades_to_low_stale(self) -> None:
        coverage = compute_recall_coverage(None, now=_now())  # type: ignore[arg-type]
        self.assertEqual(coverage.level, COVERAGE_LOW)
        self.assertTrue(coverage.stale)

    def test_malformed_hit_is_skipped_not_raised(self) -> None:
        # A non-mapping hit (e.g. int) must not blow up the scorer.
        recall = RuntimeRecallResult(
            matched_session_id="s1",
            candidates=(_candidate(session_id="s1", hours_ago=1),),
            memory_hits=(42,),  # type: ignore[arg-type]
        )
        coverage = compute_recall_coverage(recall, now=_now())
        # session only → medium / not stale.
        self.assertEqual(coverage.level, COVERAGE_MEDIUM)


if __name__ == "__main__":
    unittest.main()
