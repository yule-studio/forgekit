"""P0-I stage 3 commit 4 — growth ledger capture tests."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.lifecycle.growth_ledger import (
    EVENT_DECISION_MADE,
    EVENT_REFERENCE_USED,
    EVENT_REGRET,
    EVENT_RETROSPECTIVE,
    EVENT_RISK_SURFACED,
    GrowthEvent,
    PromotionCandidate,
    append_growth_event,
    build_reference_event,
    build_regret_event,
    build_retrospective_event,
    build_risk_event,
    compute_promotion_candidates,
    read_ledger,
    summarize_for_status,
)


_FIXED_NOW = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Append + round-trip
# ---------------------------------------------------------------------------


class AppendGrowthEventTests(unittest.TestCase):
    def test_first_event_stamps_recorded_at(self) -> None:
        extra: dict = {}
        event = build_reference_event(
            summary="RFC 7519 JWT spec 검토",
            source_url="https://datatracker.ietf.org/doc/html/rfc7519",
            role="backend-engineer",
        )
        persisted = append_growth_event(extra, event, now=_FIXED_NOW)
        self.assertEqual(
            persisted.recorded_at, "2026-05-14T12:00:00+00:00"
        )
        self.assertEqual(len(extra["growth_ledger"]), 1)
        self.assertEqual(
            extra["growth_ledger"][0]["kind"], EVENT_REFERENCE_USED
        )
        self.assertEqual(extra["growth_ledger"][0]["role"], "backend-engineer")

    def test_subsequent_events_append_not_replace(self) -> None:
        extra: dict = {}
        append_growth_event(extra, build_reference_event(summary="a"))
        append_growth_event(extra, build_decision_event(summary="b"))
        self.assertEqual(len(extra["growth_ledger"]), 2)
        kinds = [entry["kind"] for entry in extra["growth_ledger"]]
        self.assertEqual(kinds, [EVENT_REFERENCE_USED, EVENT_DECISION_MADE])

    def test_existing_recorded_at_preserved(self) -> None:
        extra: dict = {}
        event = GrowthEvent(
            kind=EVENT_REFERENCE_USED,
            summary="prebaked",
            recorded_at="2025-01-01T00:00:00+00:00",
        )
        persisted = append_growth_event(extra, event, now=_FIXED_NOW)
        self.assertEqual(persisted.recorded_at, "2025-01-01T00:00:00+00:00")


def build_decision_event(*, summary: str, pattern_tag=None):
    return GrowthEvent(kind=EVENT_DECISION_MADE, summary=summary, pattern_tag=pattern_tag)


# ---------------------------------------------------------------------------
# Read ledger
# ---------------------------------------------------------------------------


class ReadLedgerTests(unittest.TestCase):
    def test_empty_extra(self) -> None:
        self.assertEqual(read_ledger({}), ())

    def test_returns_growth_events(self) -> None:
        extra: dict = {}
        append_growth_event(extra, build_reference_event(summary="x"))
        events = read_ledger(extra)
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], GrowthEvent)
        self.assertEqual(events[0].summary, "x")


# ---------------------------------------------------------------------------
# Promotion candidates
# ---------------------------------------------------------------------------


class PromotionCandidateTests(unittest.TestCase):
    def test_repeated_pattern_tag_three_times_yields_candidate(self) -> None:
        extra: dict = {}
        for _ in range(3):
            append_growth_event(
                extra,
                build_regret_event(
                    summary="forum follow-up 에서 session 못 찾음",
                    pattern_tag="forum-no-session",
                ),
            )
        candidates = compute_promotion_candidates(extra)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].pattern_tag, "forum-no-session")
        self.assertEqual(candidates[0].occurrence_count, 3)

    def test_distinct_kinds_yields_candidate_even_under_repeat_threshold(self) -> None:
        extra: dict = {}
        # 2 occurrences but 2 distinct kinds → distinct-kind threshold (≥2) hits.
        append_growth_event(
            extra,
            build_regret_event(summary="X", pattern_tag="X"),
        )
        append_growth_event(
            extra,
            build_risk_event(summary="X risk", pattern_tag="X"),
        )
        candidates = compute_promotion_candidates(extra)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].pattern_tag, "X")
        self.assertEqual(len(candidates[0].distinct_kinds), 2)

    def test_no_pattern_tag_no_candidate(self) -> None:
        extra: dict = {}
        for _ in range(5):
            append_growth_event(extra, build_reference_event(summary="ref"))
        self.assertEqual(compute_promotion_candidates(extra), ())

    def test_below_threshold_no_candidate(self) -> None:
        extra: dict = {}
        append_growth_event(
            extra,
            build_regret_event(summary="X1", pattern_tag="Y"),
        )
        append_growth_event(
            extra,
            build_regret_event(summary="X2", pattern_tag="Y"),
        )
        # 2 occurrences of the same kind — fails both repeat (≥3) AND
        # distinct-kind (≥2) thresholds.
        self.assertEqual(compute_promotion_candidates(extra), ())


# ---------------------------------------------------------------------------
# Status summary
# ---------------------------------------------------------------------------


class StatusSummaryTests(unittest.TestCase):
    def test_empty_ledger_returns_none(self) -> None:
        self.assertIsNone(summarize_for_status({}))

    def test_summary_counts_by_kind(self) -> None:
        extra: dict = {}
        append_growth_event(extra, build_reference_event(summary="a"))
        append_growth_event(extra, build_reference_event(summary="b"))
        append_growth_event(
            extra,
            GrowthEvent(kind=EVENT_DECISION_MADE, summary="d"),
        )
        append_growth_event(extra, build_risk_event(summary="r"))
        line = summarize_for_status(extra)
        self.assertIsNotNone(line)
        assert line is not None
        self.assertIn("🌱", line)
        self.assertIn("references 2", line)
        self.assertIn("decisions 1", line)
        self.assertIn("risks 1", line)

    def test_summary_includes_promotion_candidate_tag(self) -> None:
        extra: dict = {}
        for i in range(3):
            append_growth_event(
                extra,
                build_regret_event(
                    summary=f"X#{i}", pattern_tag="repeated-error"
                ),
            )
        line = summarize_for_status(extra)
        assert line is not None
        self.assertIn("promotion 후보", line)
        self.assertIn("repeated-error", line)


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


class RoundTripTests(unittest.TestCase):
    def test_from_dict_round_trip(self) -> None:
        original = GrowthEvent(
            kind=EVENT_RETROSPECTIVE,
            summary="다음엔 RepoContract 먼저 확인",
            pattern_tag="missing-repo-contract",
            role="tech-lead",
            severity="major",
            recorded_at="2026-05-14T00:00:00+00:00",
        )
        payload = original.to_dict()
        restored = GrowthEvent.from_dict(payload)
        self.assertEqual(restored.kind, original.kind)
        self.assertEqual(restored.summary, original.summary)
        self.assertEqual(restored.pattern_tag, original.pattern_tag)
        self.assertEqual(restored.role, original.role)
        self.assertEqual(restored.severity, original.severity)
        self.assertEqual(restored.recorded_at, original.recorded_at)


# ---------------------------------------------------------------------------
# Append updates promotion candidates atomically
# ---------------------------------------------------------------------------


class AtomicPromotionUpdateTests(unittest.TestCase):
    def test_extra_growth_promotion_candidates_recomputed_per_append(self) -> None:
        extra: dict = {}
        for _ in range(3):
            append_growth_event(
                extra,
                build_regret_event(
                    summary="x", pattern_tag="recur-tag"
                ),
            )
        # The append helper writes growth_promotion_candidates next to
        # growth_ledger so callers can read both at once.
        self.assertIn("growth_promotion_candidates", extra)
        cands = extra["growth_promotion_candidates"]
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["pattern_tag"], "recur-tag")


if __name__ == "__main__":
    unittest.main()
