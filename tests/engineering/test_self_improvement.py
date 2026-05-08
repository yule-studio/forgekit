"""self_improvement — A-M10c detection skeleton tests."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.lifecycle.self_improvement import (
    SEVERITY_HIGH,
    SEVERITY_MEDIUM,
    SIGNAL_DUPLICATE_TOPIC_APPROVAL,
    SIGNAL_EMPTY_KNOWLEDGE_NOTE,
    SIGNAL_FAILED_RETRYABLE_PILEUP,
    SIGNAL_STALE_HEARTBEAT,
    SelfImprovementSignal,
    collect_self_improvement_signals,
    detect_duplicate_topic_approval,
    detect_empty_knowledge_note_attempts,
    detect_failed_retryable_pileup,
    detect_stale_heartbeat,
    render_signals_as_proposal_body,
)


def _job(*, state, job_type="approval_post", payload=None, result=None, job_id="j"):
    return SimpleNamespace(
        state=SimpleNamespace(value=state),
        job_type=job_type,
        payload=payload or {},
        result=result or {},
        job_id=job_id,
    )


class FailedRetryablePileupTests(unittest.TestCase):
    def test_under_threshold_returns_none(self) -> None:
        jobs = [_job(state="failed_retryable") for _ in range(2)]
        self.assertIsNone(detect_failed_retryable_pileup(jobs=jobs, threshold=3))

    def test_over_threshold_returns_signal(self) -> None:
        jobs = [
            _job(state="failed_retryable", job_id=f"j{i}", job_type="approval_post")
            for i in range(5)
        ]
        sig = detect_failed_retryable_pileup(jobs=jobs, threshold=3)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.signal, SIGNAL_FAILED_RETRYABLE_PILEUP)
        self.assertEqual(sig.evidence["count"], 5)
        self.assertIn("approval_post", sig.evidence["job_types"])

    def test_double_threshold_escalates_to_high(self) -> None:
        jobs = [
            _job(state="failed_retryable", job_id=f"j{i}") for i in range(8)
        ]
        sig = detect_failed_retryable_pileup(jobs=jobs, threshold=3)
        assert sig is not None
        self.assertEqual(sig.severity, SEVERITY_HIGH)


class DuplicateTopicApprovalTests(unittest.TestCase):
    def test_two_active_approvals_same_topic_flag(self) -> None:
        jobs = [
            _job(
                state="queued",
                job_type="approval_post",
                payload={"extra": {"topic_key": "k"}},
                job_id="a",
            ),
            _job(
                state="in_progress",
                job_type="approval_post",
                payload={"extra": {"topic_key": "k"}},
                job_id="b",
            ),
        ]
        sig = detect_duplicate_topic_approval(jobs=jobs)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.signal, SIGNAL_DUPLICATE_TOPIC_APPROVAL)
        self.assertIn("k", sig.evidence["topics"])
        self.assertEqual(sig.severity, SEVERITY_HIGH)

    def test_terminal_failed_rows_ignored(self) -> None:
        jobs = [
            _job(
                state="failed_terminal",
                job_type="approval_post",
                payload={"extra": {"topic_key": "k"}},
                job_id="a",
            ),
            _job(
                state="queued",
                job_type="approval_post",
                payload={"extra": {"topic_key": "k"}},
                job_id="b",
            ),
        ]
        self.assertIsNone(detect_duplicate_topic_approval(jobs=jobs))


class StaleHeartbeatTests(unittest.TestCase):
    def test_stale_service_flagged(self) -> None:
        now = datetime(2026, 5, 8, 10, 0, tzinfo=timezone.utc)
        beat = (now - timedelta(seconds=900)).isoformat()
        sig = detect_stale_heartbeat(
            heartbeats={"eng-obsidian-writer": {"updated_at": beat}},
            now=now,
            stale_after_seconds=600,
        )
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.signal, SIGNAL_STALE_HEARTBEAT)
        self.assertIn("eng-obsidian-writer", sig.evidence["stale_service_ids"])

    def test_recent_heartbeat_returns_none(self) -> None:
        now = datetime(2026, 5, 8, 10, 0, tzinfo=timezone.utc)
        beat = (now - timedelta(seconds=30)).isoformat()
        self.assertIsNone(
            detect_stale_heartbeat(
                heartbeats={"x": {"updated_at": beat}},
                now=now,
                stale_after_seconds=600,
            )
        )


class EmptyKnowledgeNoteTests(unittest.TestCase):
    def test_two_or_more_hydration_failures_flag(self) -> None:
        jobs = [
            _job(
                state="failed_retryable",
                job_type="obsidian_write",
                result={"error": "knowledge note ... hydration 부족"},
                job_id=f"j{i}",
            )
            for i in range(3)
        ]
        sig = detect_empty_knowledge_note_attempts(failed_jobs=jobs)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.signal, SIGNAL_EMPTY_KNOWLEDGE_NOTE)
        self.assertEqual(sig.severity, SEVERITY_MEDIUM)

    def test_single_failure_no_signal(self) -> None:
        jobs = [
            _job(
                state="failed_retryable",
                job_type="obsidian_write",
                result={"error": "hydration 부족"},
            )
        ]
        self.assertIsNone(detect_empty_knowledge_note_attempts(failed_jobs=jobs))


class CollectorAndRendererTests(unittest.TestCase):
    def test_collect_returns_signals_high_severity_first(self) -> None:
        jobs = [_job(state="failed_retryable") for _ in range(8)]
        # Add a duplicate topic approval too.
        jobs.extend(
            [
                _job(
                    state="queued",
                    job_type="approval_post",
                    payload={"extra": {"topic_key": "k"}},
                    job_id="a",
                ),
                _job(
                    state="queued",
                    job_type="approval_post",
                    payload={"extra": {"topic_key": "k"}},
                    job_id="b",
                ),
            ]
        )
        signals = collect_self_improvement_signals(jobs=jobs)
        self.assertEqual(len(signals), 2)
        # Both high-severity → tie-broken by signal id alphabetical:
        # duplicate_topic_approval < failed_retryable_pileup
        self.assertEqual(signals[0].severity, SEVERITY_HIGH)
        self.assertEqual(signals[1].severity, SEVERITY_HIGH)

    def test_render_signals_as_proposal_body_includes_each(self) -> None:
        signals = [
            SelfImprovementSignal(
                signal=SIGNAL_FAILED_RETRYABLE_PILEUP,
                severity=SEVERITY_HIGH,
                summary="failed_retryable 누적",
                evidence={"count": 5},
                detected_at="2026-05-08T10:00:00+00:00",
            )
        ]
        body = render_signals_as_proposal_body(signals)
        self.assertIn("self-improvement proposal", body)
        self.assertIn(SIGNAL_FAILED_RETRYABLE_PILEUP, body)
        self.assertIn("failed_retryable 누적", body)
        self.assertIn("제안 조치", body)
        self.assertIn("자동 기록 안내", body)

    def test_empty_signals_render_no_signals_block(self) -> None:
        body = render_signals_as_proposal_body([])
        self.assertIn("감지된 신호 없음", body)


if __name__ == "__main__":
    unittest.main()
