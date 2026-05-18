"""Seed-backlog detectors — self-improvement runtime tests."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Mapping

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.lifecycle.self_improvement_seed_detectors import (
    ObservationContext,
    SIGNAL_APPROVAL_NO_MATCHING_REPLY,
    SIGNAL_CODING_CONTINUATION_STALLED,
    SIGNAL_ENGINEERING_WRITE_REPLY_MISMATCH,
    SIGNAL_ISSUELESS_BOOTSTRAP_FAILURE,
    SIGNAL_MEMBER_BOT_PRESENCE_CONFUSION,
    SIGNAL_OBSIDIAN_RENDER_FAILURE,
    SIGNAL_QA_TEST_MISCLASSIFICATION,
    SIGNAL_SUPERVISOR_WATCH_UNKNOWN,
    collect_seed_signals,
    detect_approval_no_matching_reply,
    detect_coding_continuation_stalled,
    detect_engineering_write_reply_mismatch,
    detect_issueless_bootstrap_failure,
    detect_member_bot_presence_confusion,
    detect_obsidian_render_failure,
    detect_qa_test_misclassification,
    detect_supervisor_watch_unknown_surface,
)


_NOW = datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)


def _job(
    *,
    job_type: str,
    state: str = "saved",
    payload: Mapping[str, Any] = None,
    result: Mapping[str, Any] = None,
    job_id: str = "j",
) -> Any:
    return SimpleNamespace(
        job_type=job_type,
        state=SimpleNamespace(value=state),
        payload=payload or {},
        result=result or {},
        job_id=job_id,
    )


def _session(
    *,
    session_id: str = "s",
    prompt: str = "",
    extra: Mapping[str, Any] = None,
) -> Any:
    return SimpleNamespace(
        session_id=session_id, prompt=prompt, extra=extra or {}
    )


class EngineeringWriteReplyMismatchTests(unittest.TestCase):
    def test_no_marker_returns_none(self) -> None:
        jobs = [_job(job_type="approval_post")]
        self.assertIsNone(
            detect_engineering_write_reply_mismatch(jobs=jobs)
        )

    def test_marker_present_returns_signal(self) -> None:
        jobs = [
            _job(
                job_type="approval_post",
                payload={"approval_kind": "engineering_write"},
                result={"last_no_match_reason": "no_matching_approval"},
            )
        ]
        sig = detect_engineering_write_reply_mismatch(jobs=jobs)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.signal, SIGNAL_ENGINEERING_WRITE_REPLY_MISMATCH)

    def test_unrelated_kind_ignored(self) -> None:
        jobs = [
            _job(
                job_type="approval_post",
                payload={"approval_kind": "obsidian_write"},
                result={"last_no_match_reason": "x"},
            )
        ]
        self.assertIsNone(
            detect_engineering_write_reply_mismatch(jobs=jobs)
        )


class ApprovalNoMatchingReplyTests(unittest.TestCase):
    def test_recent_post_returns_none(self) -> None:
        jobs = [
            _job(
                job_type="approval_post",
                state="saved",
                result={
                    "posted_message_id": "m1",
                    "posted_at": _NOW.isoformat(),
                },
            )
        ]
        self.assertIsNone(
            detect_approval_no_matching_reply(
                jobs=jobs, sessions=(), now=_NOW
            )
        )

    def test_old_unmatched_post_returns_signal(self) -> None:
        old = (_NOW - timedelta(seconds=3600)).isoformat()
        jobs = [
            _job(
                job_type="approval_post",
                state="saved",
                result={
                    "posted_message_id": "m1",
                    "posted_at": old,
                },
            )
        ]
        sig = detect_approval_no_matching_reply(
            jobs=jobs, sessions=(), now=_NOW
        )
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.signal, SIGNAL_APPROVAL_NO_MATCHING_REPLY)

    def test_reply_resolved_ignored(self) -> None:
        old = (_NOW - timedelta(seconds=3600)).isoformat()
        jobs = [
            _job(
                job_type="approval_post",
                state="saved",
                result={
                    "posted_message_id": "m1",
                    "posted_at": old,
                    "reply_resolved": True,
                },
            )
        ]
        self.assertIsNone(
            detect_approval_no_matching_reply(
                jobs=jobs, sessions=(), now=_NOW
            )
        )


class QaTestMisclassificationTests(unittest.TestCase):
    def test_coding_intent_misclassified_returns_signal(self) -> None:
        sessions = [
            _session(
                session_id="s1",
                prompt="이 부분 구현해줘",
                extra={"dispatcher_classification": {"label": "qa-test"}},
            )
        ]
        sig = detect_qa_test_misclassification(sessions=sessions)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.signal, SIGNAL_QA_TEST_MISCLASSIFICATION)

    def test_qa_only_prompt_ignored(self) -> None:
        sessions = [
            _session(
                session_id="s1",
                prompt="이 테스트 케이스 좀 봐줘",
                extra={"dispatcher_classification": {"label": "qa-test"}},
            )
        ]
        self.assertIsNone(detect_qa_test_misclassification(sessions=sessions))

    def test_coding_classified_correctly_ignored(self) -> None:
        sessions = [
            _session(
                session_id="s1",
                prompt="이 부분 구현해줘",
                extra={"dispatcher_classification": {"label": "coding"}},
            )
        ]
        self.assertIsNone(detect_qa_test_misclassification(sessions=sessions))


class CodingContinuationStalledTests(unittest.TestCase):
    def test_old_approval_no_dispatch_returns_signal(self) -> None:
        old = (_NOW - timedelta(seconds=3600)).isoformat()
        sessions = [
            _session(
                session_id="s1",
                extra={
                    "coding_proposal": {"executor_role": "backend-engineer"},
                    "github_work_order_progress": {
                        "coding_dispatch_queued": {"at": old, "detail": {}}
                    },
                },
            )
        ]
        sig = detect_coding_continuation_stalled(sessions=sessions, now=_NOW)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.signal, SIGNAL_CODING_CONTINUATION_STALLED)

    def test_recently_approved_ignored(self) -> None:
        recent = _NOW.isoformat()
        sessions = [
            _session(
                session_id="s1",
                extra={
                    "coding_proposal": {"executor_role": "backend-engineer"},
                    "github_work_order_progress": {
                        "coding_dispatch_queued": {"at": recent, "detail": {}}
                    },
                },
            )
        ]
        self.assertIsNone(
            detect_coding_continuation_stalled(sessions=sessions, now=_NOW)
        )

    def test_dispatch_marker_present_ignored(self) -> None:
        old = (_NOW - timedelta(seconds=3600)).isoformat()
        sessions = [
            _session(
                session_id="s1",
                extra={
                    "coding_proposal": {"executor_role": "backend-engineer"},
                    "coding_execute_dispatch": {"job_id": "x"},
                    "github_work_order_progress": {
                        "coding_dispatch_queued": {"at": old, "detail": {}}
                    },
                },
            )
        ]
        self.assertIsNone(
            detect_coding_continuation_stalled(sessions=sessions, now=_NOW)
        )


class SupervisorWatchUnknownTests(unittest.TestCase):
    def test_unknown_surface_old_returns_signal(self) -> None:
        old = (_NOW - timedelta(seconds=600)).isoformat()
        heartbeats = {
            "eng-supervisor-watch": {
                "last_status": "UNKNOWN",
                "last_status_at": old,
                "updated_at": old,
            }
        }
        sig = detect_supervisor_watch_unknown_surface(
            heartbeats=heartbeats, now=_NOW
        )
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.signal, SIGNAL_SUPERVISOR_WATCH_UNKNOWN)
        self.assertIn(
            "eng-supervisor-watch", sig.evidence["service_ids"]
        )

    def test_recent_unknown_ignored(self) -> None:
        recent = _NOW.isoformat()
        heartbeats = {
            "eng-supervisor-watch": {
                "last_status": "UNKNOWN",
                "last_status_at": recent,
                "updated_at": recent,
            }
        }
        self.assertIsNone(
            detect_supervisor_watch_unknown_surface(
                heartbeats=heartbeats, now=_NOW
            )
        )

    def test_known_status_ignored(self) -> None:
        old = (_NOW - timedelta(seconds=600)).isoformat()
        heartbeats = {
            "eng-supervisor-watch": {
                "last_status": "OK",
                "last_status_at": old,
                "updated_at": old,
            }
        }
        self.assertIsNone(
            detect_supervisor_watch_unknown_surface(
                heartbeats=heartbeats, now=_NOW
            )
        )


class ObsidianRenderFailureTests(unittest.TestCase):
    def test_repeated_renderer_failure_returns_signal(self) -> None:
        jobs = [
            _job(
                job_type="obsidian_write",
                state="failed_retryable",
                result={"error": "renderer crashed at line 42"},
                job_id=f"j{i}",
            )
            for i in range(3)
        ]
        sig = detect_obsidian_render_failure(failed_jobs=jobs)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.signal, SIGNAL_OBSIDIAN_RENDER_FAILURE)

    def test_single_failure_ignored(self) -> None:
        jobs = [
            _job(
                job_type="obsidian_write",
                state="failed_retryable",
                result={"error": "renderer crashed"},
            )
        ]
        self.assertIsNone(detect_obsidian_render_failure(failed_jobs=jobs))


class MemberBotPresenceConfusionTests(unittest.TestCase):
    def test_online_but_idle_returns_signal(self) -> None:
        recent_heartbeat = _NOW.isoformat()
        old_activity = (_NOW - timedelta(hours=3)).isoformat()
        heartbeats = {
            "eng-member-bot-backend-engineer": {
                "updated_at": recent_heartbeat,
                "last_status": "ONLINE",
            }
        }
        sessions = [
            _session(extra={"last_activity_at": old_activity})
        ]
        sig = detect_member_bot_presence_confusion(
            heartbeats=heartbeats, sessions=sessions, now=_NOW
        )
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.signal, SIGNAL_MEMBER_BOT_PRESENCE_CONFUSION)

    def test_recent_activity_ignored(self) -> None:
        recent = _NOW.isoformat()
        heartbeats = {
            "eng-member-bot-backend": {"updated_at": recent},
        }
        sessions = [_session(extra={"last_activity_at": recent})]
        self.assertIsNone(
            detect_member_bot_presence_confusion(
                heartbeats=heartbeats, sessions=sessions, now=_NOW
            )
        )


class IssuelessBootstrapFailureTests(unittest.TestCase):
    def test_issue_error_returns_signal(self) -> None:
        jobs = [
            _job(
                job_type="github_work_order",
                state="failed_retryable",
                result={"error": "no issue anchor on bootstrap"},
            )
        ]
        sig = detect_issueless_bootstrap_failure(failed_jobs=jobs)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.signal, SIGNAL_ISSUELESS_BOOTSTRAP_FAILURE)

    def test_unrelated_error_ignored(self) -> None:
        jobs = [
            _job(
                job_type="github_work_order",
                state="failed_retryable",
                result={"error": "rate limited"},
            )
        ]
        self.assertIsNone(
            detect_issueless_bootstrap_failure(failed_jobs=jobs)
        )


class CollectSeedSignalsTests(unittest.TestCase):
    def test_aggregator_returns_severity_descending(self) -> None:
        old = (_NOW - timedelta(seconds=3600)).isoformat()
        observation = ObservationContext(
            jobs=(
                _job(
                    job_type="approval_post",
                    state="saved",
                    result={
                        "posted_message_id": "m1",
                        "posted_at": old,
                    },
                ),
                _job(
                    job_type="approval_post",
                    payload={"approval_kind": "engineering_write"},
                    result={"last_no_match_reason": "x"},
                ),
            ),
            sessions=(
                _session(
                    session_id="s",
                    prompt="이거 구현해줘",
                    extra={"dispatcher_classification": {"label": "qa-test"}},
                ),
            ),
            heartbeats={},
            now=_NOW,
        )
        signals = collect_seed_signals(observation)
        # All three are high severity (sorted by signal id).
        self.assertTrue(len(signals) >= 3)
        for sig in signals:
            self.assertEqual(sig.severity, "high")


if __name__ == "__main__":
    unittest.main()
