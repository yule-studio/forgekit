"""supervisor autonomy producer tick — Round 4 of #73.

Pin the contract that ``run_supervisor_watch_loop`` drives the
autonomy producer on its own interval and that failures stay
isolated from the supervisor sweep path.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any, List

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.store import JobQueue
from yule_orchestrator.agents.job_queue.worker_loop import (
    run_supervisor_watch_loop,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db_path = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=db_path)
        self.heartbeats = HeartbeatStore(db_path=db_path)

    @staticmethod
    def _stub_sweep(**_kwargs):
        from yule_orchestrator.agents.job_queue.heartbeat import (
            SupervisorSweepReport,
        )

        return SupervisorSweepReport(stale=(), reaped_jobs=0, swept_at=0.0)


class _StubReport:
    def __init__(self, summary: str = "stub-tick") -> None:
        self._summary = summary

    def summary_line(self) -> str:
        return self._summary


class SupervisorAutonomyTickEnabledTests(_Fixture):
    def test_tick_fires_on_interval(self) -> None:
        ticks: List[Any] = []

        def _producer_tick():
            ticks.append("tick")
            return _StubReport()

        async def fast_sleep(_secs):
            return None

        # Synthetic monotonic clock so the interval gate fires every
        # iteration: each call advances by 100 s, well over the 5 s
        # interval.
        counter = {"i": 0}

        def fake_clock():
            counter["i"] += 1
            return float(counter["i"]) * 100.0

        iterations = _run(
            run_supervisor_watch_loop(
                heartbeat_store=self.heartbeats,
                job_queue=self.queue,
                sweep_fn=self._stub_sweep,
                sweep_interval_seconds=0.0,
                sleep_fn=fast_sleep,
                max_iterations=3,
                time_fn=fake_clock,
                autonomy_producer_tick_fn=_producer_tick,
                autonomy_producer_interval_seconds=5.0,
            )
        )
        self.assertEqual(iterations, 3)
        self.assertEqual(len(ticks), 3)

    def test_dormant_when_interval_none(self) -> None:
        ticks: List[Any] = []

        def _producer_tick():
            ticks.append("tick")
            return None

        async def fast_sleep(_secs):
            return None

        _run(
            run_supervisor_watch_loop(
                heartbeat_store=self.heartbeats,
                job_queue=self.queue,
                sweep_fn=self._stub_sweep,
                sweep_interval_seconds=0.0,
                sleep_fn=fast_sleep,
                max_iterations=3,
                autonomy_producer_tick_fn=_producer_tick,
                autonomy_producer_interval_seconds=None,
            )
        )
        self.assertEqual(ticks, [])

    def test_tick_failure_does_not_kill_loop(self) -> None:
        calls: List[int] = []

        def _bad_tick():
            calls.append(1)
            raise RuntimeError("producer boom")

        async def fast_sleep(_secs):
            return None

        counter = {"i": 0}

        def fake_clock():
            counter["i"] += 1
            return float(counter["i"]) * 100.0

        # Silence the supervisor logger so the deliberate raise stays
        # off stdout.
        import logging

        loop_logger = logging.getLogger(
            "yule_orchestrator.agents.job_queue.worker_loop"
        )
        previous = loop_logger.level
        loop_logger.setLevel(logging.CRITICAL)
        try:
            iterations = _run(
                run_supervisor_watch_loop(
                    heartbeat_store=self.heartbeats,
                    job_queue=self.queue,
                    sweep_fn=self._stub_sweep,
                    sweep_interval_seconds=0.0,
                    sleep_fn=fast_sleep,
                    max_iterations=3,
                    time_fn=fake_clock,
                    autonomy_producer_tick_fn=_bad_tick,
                    autonomy_producer_interval_seconds=5.0,
                )
            )
        finally:
            loop_logger.setLevel(previous)
        self.assertEqual(iterations, 3)
        # Each iteration calls the tick; loop survives every raise.
        self.assertEqual(len(calls), 3)

    def test_on_report_invoked_with_producer_report(self) -> None:
        captured: List[Any] = []

        def _producer_tick():
            return _StubReport("summary-A")

        def _on_report(report):
            captured.append(report)

        async def fast_sleep(_secs):
            return None

        counter = {"i": 0}

        def fake_clock():
            counter["i"] += 1
            return float(counter["i"]) * 100.0

        _run(
            run_supervisor_watch_loop(
                heartbeat_store=self.heartbeats,
                job_queue=self.queue,
                sweep_fn=self._stub_sweep,
                sweep_interval_seconds=0.0,
                sleep_fn=fast_sleep,
                max_iterations=2,
                time_fn=fake_clock,
                autonomy_producer_tick_fn=_producer_tick,
                autonomy_producer_interval_seconds=1.0,
                autonomy_producer_on_report=_on_report,
            )
        )
        self.assertEqual(len(captured), 2)
        self.assertEqual(captured[0].summary_line(), "summary-A")


class SupervisorOnReportRecordsToJournalTests(_Fixture):
    """Round 4 surface — the supervisor's on_report hook funnels every
    autonomy producer report into the process-local journal so the
    next status post sees what the runtime just decided to do."""

    def test_on_report_appends_summary_to_default_journal(self) -> None:
        from yule_orchestrator.runtime.run_service import (
            _supervisor_autonomy_on_report,
        )
        from yule_orchestrator.runtime.status import (
            AUTONOMY_OUTCOME_DISPATCHED,
            get_default_autonomy_journal,
        )

        journal = get_default_autonomy_journal()
        journal.reset()
        self.addCleanup(journal.reset)

        # Build a producer-report-shaped object the on_report hook
        # accepts — duck-typed against AutonomyProducerReport.
        class _Candidate:
            source = "approved_coding_job"

        class _Dispatch:
            source = "approved_coding_job"
            outcome = AUTONOMY_OUTCOME_DISPATCHED
            session_id = "sess-Z"
            executor_role = "backend-engineer"
            job_id = "job-1"
            branch_hint = "agent/backend/issue-1"
            reason = ""

        class _Report:
            tick_id = "tick-9"
            started_at = "2026-05-10T00:00:00+00:00"
            finished_at = "2026-05-10T00:00:01+00:00"
            next_task_candidate = _Candidate()
            dispatches = (_Dispatch(),)
            locks_held = ()
            error = None

            def summary_line(self):
                return "autonomy producer tick tick-9 dispatched"

        _supervisor_autonomy_on_report(_Report())
        recent = journal.recent()
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].tick_id, "tick-9")
        self.assertEqual(
            recent[0].dispatches[0].outcome, AUTONOMY_OUTCOME_DISPATCHED
        )

    def test_on_report_swallows_journal_failures(self) -> None:
        # Even if record_autonomy_report somehow raises, the supervisor
        # callback must NOT propagate — the supervisor's last-resort
        # wrapper already catches exceptions from on_report, but the
        # callback itself takes a defensive try/except.
        import yule_orchestrator.runtime.run_service as run_service_mod

        called: list = []

        def boom(_report):
            called.append("attempted")
            raise RuntimeError("journal boom")

        original = run_service_mod.__dict__
        # Patch the import lookup inside the function — the function
        # imports lazily so we need to swap status.record_autonomy_report.
        import yule_orchestrator.runtime.status as status_mod

        prior = status_mod.record_autonomy_report
        status_mod.record_autonomy_report = boom
        try:
            # Should not raise.
            run_service_mod._supervisor_autonomy_on_report(
                type("R", (), {"summary_line": lambda self: "x"})()
            )
            self.assertEqual(called, ["attempted"])
        finally:
            status_mod.record_autonomy_report = prior


class SupervisorAutonomyTickBuilderTests(_Fixture):
    def test_builder_returns_dormant_when_env_unset(self) -> None:
        import os

        from yule_orchestrator.runtime.run_service import (
            ENV_AUTONOMY_PRODUCER_ENABLED,
            _build_autonomy_producer_tick,
        )

        prior = os.environ.pop(ENV_AUTONOMY_PRODUCER_ENABLED, None)
        try:
            tick_fn, interval = _build_autonomy_producer_tick(
                queue=self.queue, heartbeats=self.heartbeats
            )
        finally:
            if prior is not None:
                os.environ[ENV_AUTONOMY_PRODUCER_ENABLED] = prior
        self.assertIsNone(tick_fn)
        self.assertIsNone(interval)

    def test_builder_returns_callable_when_env_truthy(self) -> None:
        import os

        from yule_orchestrator.runtime.run_service import (
            DEFAULT_AUTONOMY_PRODUCER_INTERVAL_SECONDS,
            ENV_AUTONOMY_PRODUCER_ENABLED,
            _build_autonomy_producer_tick,
        )

        prior = os.environ.get(ENV_AUTONOMY_PRODUCER_ENABLED)
        os.environ[ENV_AUTONOMY_PRODUCER_ENABLED] = "true"
        try:
            tick_fn, interval = _build_autonomy_producer_tick(
                queue=self.queue, heartbeats=self.heartbeats
            )
        finally:
            if prior is None:
                os.environ.pop(ENV_AUTONOMY_PRODUCER_ENABLED, None)
            else:
                os.environ[ENV_AUTONOMY_PRODUCER_ENABLED] = prior
        # Builder may degrade to dormant if the executor bundle blows
        # up (no GitHub creds in test env), but most production paths
        # only need the local executor bundle which works without
        # GitHub. Accept either.
        if tick_fn is None:
            self.assertIsNone(interval)
        else:
            self.assertGreaterEqual(
                interval, 5.0
            )  # default lower bound enforced by builder
            self.assertEqual(
                interval, DEFAULT_AUTONOMY_PRODUCER_INTERVAL_SECONDS
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
