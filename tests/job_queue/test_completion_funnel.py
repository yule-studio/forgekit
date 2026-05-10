"""completion_funnel — Round 4 of #73.

The funnel takes the standardised completion verdict from
:func:`record_completion` and decides whether to drive the autonomy
producer's tick. Pin:

  * done → tick fires.
  * retry_ready → tick fires.
  * needs_approval → tick does NOT fire.
  * blocked → tick does NOT fire (operator surface owns next hop).
  * tick failure does not raise; recorded in the funnel decision.
  * audit history bounded to 32 entries.
  * build_completion_funnel returns a closure that binds the producer
    once and is otherwise functionally equivalent.
"""

from __future__ import annotations

import unittest
from typing import Any, List, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.completion_funnel import (
    COMPLETION_FUNNEL_EXTRA_KEY,
    build_completion_funnel,
    funnel_completion,
)
from yule_orchestrator.agents.job_queue.completion_hook import (
    COMPLETION_BLOCKED,
    COMPLETION_DONE,
    COMPLETION_NEEDS_APPROVAL,
    COMPLETION_RETRY_READY,
    JobCompletionEvent,
)


class _StubReport:
    def __init__(self, summary: str = "stub-tick") -> None:
        self._summary = summary

    def summary_line(self) -> str:
        return self._summary


def _producer_tick_recorder(report: _StubReport):
    calls: List[Mapping[str, Any]] = []

    def _tick():
        calls.append({"ticked_at": "2026-05-09T00:00:00Z"})
        return report

    return _tick, calls


# ---------------------------------------------------------------------------
# Routing decisions
# ---------------------------------------------------------------------------


class CompletionFunnelRoutingTests(unittest.TestCase):
    def test_done_triggers_producer_tick(self) -> None:
        tick, calls = _producer_tick_recorder(_StubReport("ticked-done"))
        outcome = funnel_completion(
            event=JobCompletionEvent(
                job_id="j1",
                job_type="role_take",
                session_id="S",
                status=COMPLETION_DONE,
            ),
            session_extra={},
            producer_tick_fn=tick,
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(outcome.routing.status, COMPLETION_DONE)
        self.assertTrue(outcome.decision.ticked)
        self.assertEqual(outcome.decision.producer_summary, "ticked-done")

    def test_retry_ready_triggers_producer_tick(self) -> None:
        tick, calls = _producer_tick_recorder(_StubReport())
        outcome = funnel_completion(
            event=JobCompletionEvent(
                job_id="j2",
                job_type="coding_execute",
                session_id="S",
                status=COMPLETION_RETRY_READY,
            ),
            producer_tick_fn=tick,
        )
        self.assertEqual(len(calls), 1)
        self.assertTrue(outcome.decision.ticked)

    def test_needs_approval_does_not_tick(self) -> None:
        tick, calls = _producer_tick_recorder(_StubReport())
        outcome = funnel_completion(
            event=JobCompletionEvent(
                job_id="j3",
                job_type="approval_post",
                session_id="S",
                status=COMPLETION_NEEDS_APPROVAL,
            ),
            producer_tick_fn=tick,
        )
        self.assertEqual(calls, [])
        self.assertFalse(outcome.decision.ticked)
        self.assertIn("await", outcome.decision.reason.lower())

    def test_blocked_does_not_tick(self) -> None:
        tick, calls = _producer_tick_recorder(_StubReport())
        outcome = funnel_completion(
            event=JobCompletionEvent(
                job_id="j4",
                job_type="coding_execute",
                session_id="S",
                status=COMPLETION_BLOCKED,
                reason="protected_branch_blocked",
            ),
            producer_tick_fn=tick,
        )
        self.assertEqual(calls, [])
        self.assertFalse(outcome.decision.ticked)


# ---------------------------------------------------------------------------
# Tick failure handling
# ---------------------------------------------------------------------------


class CompletionFunnelTickFailureTests(unittest.TestCase):
    def test_tick_raise_records_reason_but_does_not_propagate(self) -> None:
        def _bad_tick():
            raise RuntimeError("producer broke")

        # Silence the producer-funnel logger so the deliberate raise
        # doesn't print a traceback in test output.
        import logging

        funnel_logger = logging.getLogger(
            "yule_orchestrator.agents.job_queue.completion_funnel"
        )
        previous = funnel_logger.level
        funnel_logger.setLevel(logging.CRITICAL)
        try:
            outcome = funnel_completion(
                event=JobCompletionEvent(
                    job_id="j5",
                    job_type="role_take",
                    session_id="S",
                    status=COMPLETION_DONE,
                ),
                producer_tick_fn=_bad_tick,
            )
        finally:
            funnel_logger.setLevel(previous)
        self.assertFalse(outcome.decision.ticked)
        self.assertIn("raised", outcome.decision.reason)

    def test_tick_missing_records_reason(self) -> None:
        outcome = funnel_completion(
            event=JobCompletionEvent(
                job_id="j6",
                job_type="role_take",
                session_id="S",
                status=COMPLETION_DONE,
            ),
            producer_tick_fn=None,
        )
        self.assertFalse(outcome.decision.ticked)
        self.assertIn("no producer_tick_fn", outcome.decision.reason)


# ---------------------------------------------------------------------------
# Audit stamping
# ---------------------------------------------------------------------------


class CompletionFunnelAuditTests(unittest.TestCase):
    def test_history_bounded_to_32(self) -> None:
        tick, _ = _producer_tick_recorder(_StubReport())
        extra: Mapping[str, Any] = {}
        for i in range(40):
            outcome = funnel_completion(
                event=JobCompletionEvent(
                    job_id=f"j{i}",
                    job_type="role_take",
                    session_id="S",
                    status=COMPLETION_DONE,
                ),
                session_extra=extra,
                producer_tick_fn=tick,
            )
            extra = outcome.new_session_extra
        block = extra[COMPLETION_FUNNEL_EXTRA_KEY]
        self.assertEqual(len(block["history"]), 32)
        # Latest entry is preserved.
        self.assertEqual(block["history"][-1]["job_id"], "j39")


# ---------------------------------------------------------------------------
# Closure builder
# ---------------------------------------------------------------------------


class BuildCompletionFunnelTests(unittest.TestCase):
    def test_factory_binds_producer(self) -> None:
        tick, calls = _producer_tick_recorder(_StubReport("bound-tick"))
        funnel = build_completion_funnel(producer_tick_fn=tick)
        outcome = funnel(
            event=JobCompletionEvent(
                job_id="j7",
                job_type="role_take",
                session_id="S",
                status=COMPLETION_DONE,
            ),
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(outcome.decision.producer_summary, "bound-tick")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
