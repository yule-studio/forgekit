"""completion_hook — Phase 2 of #73."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.completion_hook import (
    COMPLETION_BLOCKED,
    COMPLETION_DONE,
    COMPLETION_NEEDS_APPROVAL,
    COMPLETION_RETRY_READY,
    JobCompletionEvent,
    normalise_completion_status,
    record_completion,
)
from yule_engineering.agents.lifecycle.agent_ops_log import (
    SESSION_EXTRA_KEY,
)


class NormaliseStatusTests(unittest.TestCase):
    def test_done_aliases(self) -> None:
        for raw in ("done", "saved", "completed", "OK", "success"):
            with self.subTest(raw=raw):
                self.assertEqual(normalise_completion_status(raw), COMPLETION_DONE)

    def test_retry_aliases(self) -> None:
        for raw in ("retry_ready", "failed_retryable", "retry", "transient"):
            self.assertEqual(normalise_completion_status(raw), COMPLETION_RETRY_READY)

    def test_needs_approval_aliases(self) -> None:
        for raw in ("needs_approval", "pending_approval", "approval_required"):
            self.assertEqual(normalise_completion_status(raw), COMPLETION_NEEDS_APPROVAL)

    def test_default_blocked(self) -> None:
        for raw in ("", "weird", "failed_terminal", "manual"):
            self.assertEqual(normalise_completion_status(raw), COMPLETION_BLOCKED)


class RecordCompletionTests(unittest.TestCase):
    def _event(self, **overrides) -> JobCompletionEvent:
        base = {
            "job_id": "job-1",
            "job_type": "research_collect",
            "session_id": "sess-1",
            "status": "saved",
            "reason": "all good",
            "role": "tech-lead",
            "metadata": {},
            "completed_at": "2026-05-09T10:00:00+00:00",
        }
        base.update(overrides)
        return JobCompletionEvent(**base)

    def test_done_routes_to_select_next(self) -> None:
        new_extra, routing = record_completion(event=self._event())
        self.assertEqual(routing.status, COMPLETION_DONE)
        self.assertTrue(routing.should_select_next)
        self.assertIsNone(routing.blocking_reason)
        self.assertIsNotNone(routing.audit_entry_id)
        # Audit appended.
        self.assertIn(SESSION_EXTRA_KEY, new_extra)
        self.assertEqual(len(new_extra[SESSION_EXTRA_KEY]), 1)

    def test_retry_ready_routes_to_retry_same(self) -> None:
        _, routing = record_completion(
            event=self._event(status="failed_retryable", reason="transient HTTP 500")
        )
        self.assertEqual(routing.status, COMPLETION_RETRY_READY)
        self.assertTrue(routing.should_select_next)
        self.assertEqual(routing.recommended_source, "retry_same")

    def test_needs_approval_defers_selector(self) -> None:
        _, routing = record_completion(event=self._event(status="pending_approval"))
        self.assertEqual(routing.status, COMPLETION_NEEDS_APPROVAL)
        self.assertFalse(routing.should_select_next)
        self.assertEqual(routing.blocking_reason, "awaiting_human_approval")

    def test_blocked_defers_selector_with_reason(self) -> None:
        _, routing = record_completion(
            event=self._event(
                status="manual", reason="secret missing — operator action required"
            )
        )
        self.assertEqual(routing.status, COMPLETION_BLOCKED)
        self.assertFalse(routing.should_select_next)
        self.assertIn("secret missing", routing.blocking_reason or "")

    def test_done_recommended_source_per_job_type(self) -> None:
        cases = {
            "research_collect": "deliberation_after_research",
            "role_take": "synthesis_after_takes",
            "approval_post": "obsidian_or_coding_after_approval",
            "obsidian_write": "next_task_default",
            "coding_execute": "next_task_default",
            "unknown_job_type": "next_task_default",
        }
        for job_type, expected in cases.items():
            with self.subTest(job_type=job_type):
                _, routing = record_completion(event=self._event(job_type=job_type))
                self.assertEqual(routing.recommended_source, expected)

    def test_extra_is_immutable_input_preserved(self) -> None:
        original = {"foo": "bar"}
        new_extra, _ = record_completion(event=self._event(), session_extra=original)
        self.assertEqual(original, {"foo": "bar"})
        self.assertEqual(new_extra["foo"], "bar")


if __name__ == "__main__":
    unittest.main()
