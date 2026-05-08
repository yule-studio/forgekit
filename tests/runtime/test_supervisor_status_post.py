"""run_supervisor_watch_loop status posting hook — A-M7-final tests.

Pin the cadence + safety contract:

  * ``status_post_fn`` fires once per ``status_post_interval_seconds``
    (regardless of how often the sweep ticks)
  * ``status_post_interval_seconds=None`` → posting dormant
  * post failures are caught, never crash the supervisor
  * the post-interval gate advances even on failure so a transient
    outage doesn't hammer Discord every sweep tick
"""

from __future__ import annotations

import asyncio
import unittest
from typing import List, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.heartbeat import (
    HeartbeatStore,
    SupervisorSweepReport,
)
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


def _empty_sweep(*, heartbeat_store, job_queue, deadline_seconds):
    return SupervisorSweepReport(stale=(), reaped_jobs=0, swept_at=0.0)


class _LoopFixture(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401
        self.queue = JobQueue()
        self.heartbeats = HeartbeatStore()


class StatusPostingCadenceTests(_LoopFixture):
    def test_post_fn_fires_once_per_interval(self) -> None:
        post_calls: List[int] = []

        async def fake_post():
            post_calls.append(1)

        async def no_sleep(_secs):
            return None

        # Synthetic clock — bumps 30s each call. Sweep interval 10s,
        # post interval 60s → over 6 sweeps the post fires twice
        # (at t=0 and t=60).
        clock = {"now": 0.0}

        def time_fn():
            clock["now"] += 30.0
            return clock["now"]

        _run(
            run_supervisor_watch_loop(
                heartbeat_store=self.heartbeats,
                job_queue=self.queue,
                sweep_fn=_empty_sweep,
                sweep_interval_seconds=10.0,
                sleep_fn=no_sleep,
                max_iterations=6,
                status_post_fn=fake_post,
                status_post_interval_seconds=60.0,
                time_fn=time_fn,
            )
        )
        # Iteration 1: clock=30, last=None → post + last=30
        # Iteration 2: clock=60, 60-30=30<60 → skip
        # Iteration 3: clock=90, 90-30=60>=60 → post + last=90
        # Iteration 4: clock=120, 120-90=30<60 → skip
        # Iteration 5: clock=150, 150-90=60>=60 → post
        # Iteration 6: clock=180, 180-150=30<60 → skip
        # → 3 posts.
        self.assertEqual(len(post_calls), 3)

    def test_no_post_fn_keeps_loop_dormant(self) -> None:
        async def no_sleep(_secs):
            return None

        iterations = _run(
            run_supervisor_watch_loop(
                heartbeat_store=self.heartbeats,
                job_queue=self.queue,
                sweep_fn=_empty_sweep,
                sweep_interval_seconds=1.0,
                sleep_fn=no_sleep,
                max_iterations=3,
                # Both None → posting dormant.
                status_post_fn=None,
                status_post_interval_seconds=None,
            )
        )
        self.assertEqual(iterations, 3)

    def test_zero_interval_keeps_posting_dormant(self) -> None:
        # Defensive: an operator who sets the interval env to 0
        # should NOT trigger every-tick posting.
        post_calls: List[int] = []

        async def fake_post():
            post_calls.append(1)

        async def no_sleep(_secs):
            return None

        _run(
            run_supervisor_watch_loop(
                heartbeat_store=self.heartbeats,
                job_queue=self.queue,
                sweep_fn=_empty_sweep,
                sweep_interval_seconds=1.0,
                sleep_fn=no_sleep,
                max_iterations=3,
                status_post_fn=fake_post,
                status_post_interval_seconds=0.0,
            )
        )
        self.assertEqual(post_calls, [])

    def test_post_failure_does_not_crash_supervisor(self) -> None:
        post_calls: List[int] = []

        async def boom():
            post_calls.append(1)
            raise RuntimeError("discord 503")

        async def no_sleep(_secs):
            return None

        clock = {"now": 0.0}

        def time_fn():
            clock["now"] += 100.0  # always past the interval
            return clock["now"]

        # Loop runs all iterations even though the post raises every
        # tick — the supervisor must keep ticking past Discord outages.
        iterations = _run(
            run_supervisor_watch_loop(
                heartbeat_store=self.heartbeats,
                job_queue=self.queue,
                sweep_fn=_empty_sweep,
                sweep_interval_seconds=1.0,
                sleep_fn=no_sleep,
                max_iterations=4,
                status_post_fn=boom,
                status_post_interval_seconds=60.0,
                time_fn=time_fn,
            )
        )
        self.assertEqual(iterations, 4)
        self.assertEqual(len(post_calls), 4)


# ---------------------------------------------------------------------------
# A-M12 — self-improvement detect + dispatch hook on the supervisor loop
# ---------------------------------------------------------------------------


class SelfImprovementDispatchCadenceTests(_LoopFixture):
    """The supervisor watch loop runs detect_fn at the cadence and
    forwards each (signal, plan) pair to dispatch_fn. The loop never
    enqueues a queue row itself — that surface is what M13 e2e relies
    on. Failures in detect_fn / dispatch_fn must not crash the loop."""

    def _signal(self):
        from yule_orchestrator.agents.lifecycle.self_improvement import (
            SEVERITY_MEDIUM,
            SIGNAL_FAILED_RETRYABLE_PILEUP,
            SelfImprovementSignal,
        )

        return SelfImprovementSignal(
            signal=SIGNAL_FAILED_RETRYABLE_PILEUP,
            severity=SEVERITY_MEDIUM,
            summary="8 failed_retryable jobs",
            evidence={"count": 8},
            detected_at="2026-05-08T10:00:00+00:00",
        )

    def test_dispatch_fn_receives_signal_and_plan(self) -> None:
        from yule_orchestrator.agents.lifecycle.self_improvement import (
            SelfImprovementProposal,
        )

        captured = []
        signal = self._signal()

        def detect_fn():
            return [signal]

        def dispatch_fn(sig, plan):
            captured.append((sig, plan))

        async def no_sleep(_secs):
            return None

        clock = {"now": 0.0}

        def time_fn():
            clock["now"] += 50.0
            return clock["now"]

        _run(
            run_supervisor_watch_loop(
                heartbeat_store=self.heartbeats,
                job_queue=self.queue,
                sweep_fn=_empty_sweep,
                sweep_interval_seconds=1.0,
                sleep_fn=no_sleep,
                max_iterations=3,
                self_improvement_detect_fn=detect_fn,
                self_improvement_dispatch_fn=dispatch_fn,
                self_improvement_interval_seconds=60.0,
                time_fn=time_fn,
            )
        )
        # iter1: clock=50, last=None → detect (1)
        # iter2: clock=100, 100-50=50<60 → skip
        # iter3: clock=150, 150-50=100>=60 → detect (2)
        self.assertEqual(len(captured), 2)
        first_sig, first_plan = captured[0]
        self.assertIs(first_sig, signal)
        self.assertIsInstance(first_plan, SelfImprovementProposal)
        # Plan is fully formed — auto-executable L2 write request.
        self.assertTrue(first_plan.is_auto_executable)
        self.assertEqual(
            first_plan.write_request.note_kind, "failure-postmortem"
        )

    def test_no_callbacks_keeps_self_improvement_dormant(self) -> None:
        # When detect_fn / dispatch_fn / interval are absent, the
        # loop must not import the planner module and must not raise.
        async def no_sleep(_secs):
            return None

        iterations = _run(
            run_supervisor_watch_loop(
                heartbeat_store=self.heartbeats,
                job_queue=self.queue,
                sweep_fn=_empty_sweep,
                sweep_interval_seconds=1.0,
                sleep_fn=no_sleep,
                max_iterations=3,
            )
        )
        self.assertEqual(iterations, 3)

    def test_zero_interval_keeps_dormant(self) -> None:
        captured = []

        def detect_fn():
            return [self._signal()]

        def dispatch_fn(sig, plan):
            captured.append((sig, plan))

        async def no_sleep(_secs):
            return None

        _run(
            run_supervisor_watch_loop(
                heartbeat_store=self.heartbeats,
                job_queue=self.queue,
                sweep_fn=_empty_sweep,
                sweep_interval_seconds=1.0,
                sleep_fn=no_sleep,
                max_iterations=3,
                self_improvement_detect_fn=detect_fn,
                self_improvement_dispatch_fn=dispatch_fn,
                self_improvement_interval_seconds=0.0,
            )
        )
        self.assertEqual(captured, [])

    def test_detect_failure_does_not_crash_supervisor(self) -> None:
        captured = []

        def detect_fn():
            raise RuntimeError("queue read failed")

        def dispatch_fn(sig, plan):  # pragma: no cover - never reached
            captured.append((sig, plan))

        async def no_sleep(_secs):
            return None

        clock = {"now": 0.0}

        def time_fn():
            clock["now"] += 100.0
            return clock["now"]

        iterations = _run(
            run_supervisor_watch_loop(
                heartbeat_store=self.heartbeats,
                job_queue=self.queue,
                sweep_fn=_empty_sweep,
                sweep_interval_seconds=1.0,
                sleep_fn=no_sleep,
                max_iterations=3,
                self_improvement_detect_fn=detect_fn,
                self_improvement_dispatch_fn=dispatch_fn,
                self_improvement_interval_seconds=60.0,
                time_fn=time_fn,
            )
        )
        self.assertEqual(iterations, 3)
        self.assertEqual(captured, [])

    def test_dispatch_failure_does_not_crash_supervisor(self) -> None:
        dispatch_calls = []

        def detect_fn():
            return [self._signal()]

        def dispatch_fn(sig, plan):
            dispatch_calls.append((sig, plan))
            raise RuntimeError("approval queue down")

        async def no_sleep(_secs):
            return None

        clock = {"now": 0.0}

        def time_fn():
            clock["now"] += 100.0
            return clock["now"]

        iterations = _run(
            run_supervisor_watch_loop(
                heartbeat_store=self.heartbeats,
                job_queue=self.queue,
                sweep_fn=_empty_sweep,
                sweep_interval_seconds=1.0,
                sleep_fn=no_sleep,
                max_iterations=3,
                self_improvement_detect_fn=detect_fn,
                self_improvement_dispatch_fn=dispatch_fn,
                self_improvement_interval_seconds=60.0,
                time_fn=time_fn,
            )
        )
        self.assertEqual(iterations, 3)
        # Dispatch was attempted on every interval-eligible tick.
        self.assertEqual(len(dispatch_calls), 3)

    def test_async_detect_and_dispatch_supported(self) -> None:
        captured = []
        signal = self._signal()

        async def detect_fn():
            return [signal]

        async def dispatch_fn(sig, plan):
            captured.append((sig, plan))

        async def no_sleep(_secs):
            return None

        clock = {"now": 0.0}

        def time_fn():
            clock["now"] += 100.0
            return clock["now"]

        _run(
            run_supervisor_watch_loop(
                heartbeat_store=self.heartbeats,
                job_queue=self.queue,
                sweep_fn=_empty_sweep,
                sweep_interval_seconds=1.0,
                sleep_fn=no_sleep,
                max_iterations=2,
                self_improvement_detect_fn=detect_fn,
                self_improvement_dispatch_fn=dispatch_fn,
                self_improvement_interval_seconds=60.0,
                time_fn=time_fn,
            )
        )
        self.assertEqual(len(captured), 2)
        self.assertIs(captured[0][0], signal)


if __name__ == "__main__":
    unittest.main()
