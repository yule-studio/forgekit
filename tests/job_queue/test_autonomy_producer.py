"""autonomy_producer — Round 4 of #73.

The producer / scheduler ties the existing pieces (next-task selector,
coding_execute_dispatcher, CI retry orchestrator, completion hook)
into one periodic tick. Round 4 lands the producer + lock primitives;
Round 5 adds the discussion follow-up + Claude decision seam.

Pin:

  * tick polls the selector and returns its candidate on the report.
  * approved coding_jobs flow through dispatch_ready_coding_jobs.
  * already-dispatched rows surface as "deduped".
  * intra-process parallel ticks must not double-dispatch the same
    coding_job (lock registry blocks the second call).
  * a sub-producer crash records ``error`` but never poisons others.
  * per-tick lock TTL falls within the documented bound.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.autonomy_lock import (
    AutonomyLockRegistry,
    coding_job_scope,
)
from yule_orchestrator.agents.job_queue.autonomy_producer import (
    AUTONOMY_PRODUCER_HOLDER,
    AutonomyDispatch,
    AutonomyProducer,
    DispatchOutcome,
)
from yule_orchestrator.agents.job_queue.coding_execute_dispatcher import (
    SESSION_EXTRA_DISPATCH_KEY,
    WorkflowSessionState,
)
from yule_orchestrator.agents.job_queue.coding_executor_worker import (
    CodingExecutorWorker,
    JOB_TYPE_CODING_EXECUTE,
)
from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.next_task_selector import (
    SOURCE_APPROVED_CODING_JOB,
    SOURCE_IDLE,
    SOURCE_UNRESOLVED_DISCUSSION,
)
from yule_orchestrator.agents.job_queue.state_machine import JobState
from yule_orchestrator.agents.job_queue.store import JobQueue


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class _FakeSession:
    session_id: str
    extra: Mapping[str, Any] = field(default_factory=dict)
    thread_id: int = 0
    channel_id: int = 0


def _coding_job_dict(
    *,
    session_id: str = "S",
    role: str = "backend-engineer",
    status: str = "ready",
    branch_hint: str = "agent/backend/issue-77",
) -> Mapping[str, Any]:
    return {
        "session_id": session_id,
        "user_request": "do thing",
        "executor_role": role,
        "review_roles": ["tech-lead"],
        "participant_roles": [role, "tech-lead"],
        "write_scope": ["src/**"],
        "forbidden_scope": [".github/workflows/**"],
        "safety_rules": ["no force push"],
        "reason": "fixture",
        "status": status,
        "generated_prompt": "(prompt)",
        "metadata": {
            "repo_full_name": "yule/agent",
            "base_branch": "main",
            "issue_number": 77,
            "branch_hint": branch_hint,
        },
        "approved_at": "2026-05-08T01:00:00+00:00",
        "created_at": "2026-05-08T00:00:00+00:00",
    }


def _build_worker(tmp_dir: Path) -> CodingExecutorWorker:
    db_path = tmp_dir / "queue.sqlite3"
    queue = JobQueue(db_path=db_path)
    heartbeats = HeartbeatStore(db_path=db_path)
    return CodingExecutorWorker(queue=queue, heartbeats=heartbeats)


@dataclass
class _StubSessionState:
    """In-memory :class:`SessionStateLike` driven by the test fixtures."""

    sessions: Sequence[_FakeSession] = ()
    discussion_rows: Sequence[Mapping[str, Any]] = ()

    @property
    def session_loader(self):
        sessions = list(self.sessions)
        return lambda: sessions

    @property
    def update_session_fn(self):
        # No-op so the dispatcher's default update_session (which
        # would call workflow_state on a real WorkflowSession) doesn't
        # spew warnings against our minimal fake.
        def _persist(session, *, now=None):
            return session

        return _persist

    def list_approved_coding_jobs(self) -> Sequence[Mapping[str, Any]]:
        rows: List[Mapping[str, Any]] = []
        for s in self.sessions:
            cj = (s.extra or {}).get("coding_job")
            if isinstance(cj, Mapping) and cj.get("status") == "ready":
                rows.append(
                    {
                        "session_id": s.session_id,
                        "executor_role": cj.get("executor_role"),
                        "coding_job": dict(cj),
                    }
                )
        return rows

    def list_unresolved_discussion_threads(self) -> Sequence[Mapping[str, Any]]:
        return list(self.discussion_rows)


def _fixed_clock(seconds: int = 0):
    base = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)
    counter = {"i": seconds}

    def _clock() -> datetime:
        counter["i"] += 1
        return base.replace(second=min(59, counter["i"]))

    return _clock


# ---------------------------------------------------------------------------
# Selector polling
# ---------------------------------------------------------------------------


class AutonomyProducerSelectorTests(unittest.TestCase):
    def test_idle_when_no_work_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worker = _build_worker(Path(tmp))
            producer = AutonomyProducer(
                session_state=_StubSessionState(),
                coding_executor=worker,
                clock=_fixed_clock(),
            )
            report = producer.tick()
        self.assertEqual(report.next_task_candidate.source, SOURCE_IDLE)
        self.assertFalse(report.has_work())
        self.assertIn("idle", report.summary_line())

    def test_picks_approved_coding_job_first(self) -> None:
        ready = _FakeSession(
            session_id="S1",
            extra={"coding_job": _coding_job_dict(session_id="S1")},
        )
        with tempfile.TemporaryDirectory() as tmp:
            worker = _build_worker(Path(tmp))
            producer = AutonomyProducer(
                session_state=_StubSessionState(sessions=[ready]),
                coding_executor=worker,
                clock=_fixed_clock(),
            )
            report = producer.tick()
        self.assertEqual(
            report.next_task_candidate.source, SOURCE_APPROVED_CODING_JOB
        )
        self.assertTrue(report.has_work())


# ---------------------------------------------------------------------------
# Coding execute sub-producer
# ---------------------------------------------------------------------------


class AutonomyProducerCodingExecuteTests(unittest.TestCase):
    def test_dispatches_ready_coding_job(self) -> None:
        ready = _FakeSession(
            session_id="S1",
            extra={"coding_job": _coding_job_dict(session_id="S1")},
        )
        with tempfile.TemporaryDirectory() as tmp:
            worker = _build_worker(Path(tmp))
            producer = AutonomyProducer(
                session_state=_StubSessionState(sessions=[ready]),
                coding_executor=worker,
                clock=_fixed_clock(),
            )
            report = producer.tick()
            coding = [
                d for d in report.dispatches if d.source == SOURCE_APPROVED_CODING_JOB
            ]
            self.assertEqual(len(coding), 1)
            self.assertEqual(coding[0].outcome, DispatchOutcome.DISPATCHED)
            self.assertIsNotNone(coding[0].job_id)
            # Worker queue must now hold the row.
            active = worker.find_active(
                session_id="S1",
                executor_role="backend-engineer",
                branch_hint="agent/backend/issue-77",
            )
            self.assertIsNotNone(active)
            self.assertEqual(active.job_type, JOB_TYPE_CODING_EXECUTE)

    def test_idempotent_across_two_ticks(self) -> None:
        ready = _FakeSession(
            session_id="S1",
            extra={"coding_job": _coding_job_dict(session_id="S1")},
        )
        with tempfile.TemporaryDirectory() as tmp:
            worker = _build_worker(Path(tmp))
            producer = AutonomyProducer(
                session_state=_StubSessionState(sessions=[ready]),
                coding_executor=worker,
                clock=_fixed_clock(),
            )
            first = producer.tick()
            # Simulate the dispatcher having stamped the marker — the
            # next tick should treat the row as already-dispatched and
            # skip enqueue altogether.
            ready.extra = dict(ready.extra)
            ready.extra[SESSION_EXTRA_DISPATCH_KEY] = {
                "job_id": next(
                    d.job_id for d in first.dispatches if d.job_id
                ),
            }
            second = producer.tick()
            # Only one queue row exists.
            rows = worker._queue.list_for_session("S1")  # type: ignore[attr-defined]
            coding_rows = [
                r for r in rows if r.job_type == JOB_TYPE_CODING_EXECUTE
            ]
            self.assertEqual(len(coding_rows), 1)
            # Second tick had nothing to dispatch.
            coding_dispatches = [
                d
                for d in second.dispatches
                if d.source == SOURCE_APPROVED_CODING_JOB
            ]
            self.assertEqual(coding_dispatches, [])

    def test_lock_held_skips_session_in_same_tick(self) -> None:
        # Pre-locking the (session, role) scope simulates another
        # producer running in parallel. The producer must surface
        # ``locked_by_other`` instead of double-dispatching.
        ready = _FakeSession(
            session_id="S1",
            extra={"coding_job": _coding_job_dict(session_id="S1")},
        )
        registry = AutonomyLockRegistry(default_ttl_seconds=60.0)
        held = registry.acquire(
            coding_job_scope("S1", "backend-engineer"),
            holder="other-producer",
        )
        self.assertIsNotNone(held)
        with tempfile.TemporaryDirectory() as tmp:
            worker = _build_worker(Path(tmp))
            producer = AutonomyProducer(
                session_state=_StubSessionState(sessions=[ready]),
                coding_executor=worker,
                lock_registry=registry,
                clock=_fixed_clock(),
            )
            report = producer.tick()
            coding = [
                d for d in report.dispatches if d.source == SOURCE_APPROVED_CODING_JOB
            ]
            self.assertEqual(len(coding), 1)
            self.assertEqual(coding[0].outcome, DispatchOutcome.LOCKED)
            # Worker queue must still be empty — lock blocked the dispatch.
            rows = worker._queue.list_for_session("S1")  # type: ignore[attr-defined]
            self.assertFalse(rows, f"expected empty rows, got {rows!r}")


# ---------------------------------------------------------------------------
# Discussion follow-up plumbing
# ---------------------------------------------------------------------------


class AutonomyProducerDiscussionPlumbingTests(unittest.TestCase):
    def test_unresolved_discussion_routed_to_dispatcher(self) -> None:
        captured: List[Mapping[str, Any]] = []

        def _dispatch(*, session_id, discussion_row, now, decision_port=None):
            captured.append(
                {"session_id": session_id, "row": dict(discussion_row)}
            )
            return [
                AutonomyDispatch(
                    source=SOURCE_UNRESOLVED_DISCUSSION,
                    outcome=DispatchOutcome.DISPATCHED,
                    session_id=session_id,
                    executor_role="backend-engineer",
                    job_id="role-take-1",
                    reason="missing role take",
                )
            ]

        rows = [
            {
                "session_id": "S2",
                "thread_id": 12,
                "missing_roles": ["backend-engineer"],
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            worker = _build_worker(Path(tmp))
            producer = AutonomyProducer(
                session_state=_StubSessionState(discussion_rows=rows),
                coding_executor=worker,
                followup_dispatch=_dispatch,
                clock=_fixed_clock(),
            )
            report = producer.tick()

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["session_id"], "S2")
        followup = [
            d
            for d in report.dispatches
            if d.source == SOURCE_UNRESOLVED_DISCUSSION
        ]
        self.assertEqual(len(followup), 1)
        self.assertEqual(followup[0].outcome, DispatchOutcome.DISPATCHED)

    def test_followup_dispatcher_crash_recorded_as_error(self) -> None:
        import logging

        def _bad(*, session_id, discussion_row, now, decision_port=None):
            raise RuntimeError("boom")

        rows = [{"session_id": "S2"}]
        producer_logger = logging.getLogger(
            "yule_orchestrator.agents.job_queue.autonomy_producer"
        )
        previous = producer_logger.level
        producer_logger.setLevel(logging.CRITICAL)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                worker = _build_worker(Path(tmp))
                producer = AutonomyProducer(
                    session_state=_StubSessionState(discussion_rows=rows),
                    coding_executor=worker,
                    followup_dispatch=_bad,
                    clock=_fixed_clock(),
                )
                report = producer.tick()
        finally:
            producer_logger.setLevel(previous)
        errors = [d for d in report.dispatches if d.outcome == DispatchOutcome.ERROR]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].source, SOURCE_UNRESOLVED_DISCUSSION)


# ---------------------------------------------------------------------------
# Round 4-bis — CI retry guard via the Claude decision seam
# ---------------------------------------------------------------------------


@dataclass
class _StubGithubState:
    """Minimal GithubStateLike that surfaces one failed CI PR."""

    failed_rows: Sequence[Mapping[str, Any]] = ()
    orphan_rows: Sequence[Mapping[str, Any]] = ()

    def list_failed_ci_active_prs(self) -> Sequence[Mapping[str, Any]]:
        return list(self.failed_rows)

    def list_open_issues_without_session(self) -> Sequence[Mapping[str, Any]]:
        return list(self.orphan_rows)


def _failed_pr_row(
    *,
    pr_number: int = 91,
    repo: str = "yule/agent",
    branch: str = "agent/backend/issue-91",
    reason: str = "tests failed",
    attempt: int = 2,
) -> Mapping[str, Any]:
    return {
        "pr_number": pr_number,
        "repo": repo,
        "branch": branch,
        "head_sha": "deadbeef",
        "reason": reason,
        "ci_retry_attempt": attempt,
    }


class _SkipPort:
    """Decision port that always votes ``skip`` with a fixed reason."""

    def __init__(self, *, reason: str = "rate limit hit") -> None:
        self.calls: List[Any] = []
        self.reason = reason

    def decide(self, *, request):
        self.calls.append(request)
        from yule_orchestrator.agents.job_queue.claude_decision_seam import (
            DecisionResponse,
        )

        return DecisionResponse(
            skip=True, reason=self.reason, metadata={"port": "stub-skip"}
        )


class _AdvancePort:
    """Decision port that always votes ``advance`` (= proceed)."""

    def __init__(self) -> None:
        self.calls: List[Any] = []

    def decide(self, *, request):
        self.calls.append(request)
        from yule_orchestrator.agents.job_queue.claude_decision_seam import (
            DecisionResponse,
        )

        return DecisionResponse(advance=True, reason="ok")


class _RaisingPort:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, *, request):
        self.calls += 1
        raise RuntimeError("port down")


class AutonomyProducerCiRetryGuardTests(unittest.TestCase):
    """Verify the autonomy loop actually calls the decision port.

    Wires the producer with a CI-failed-PR github state, a stub
    completion dispatcher, and a stub :class:`ClaudeDecisionPort`.
    Confirms the producer asks the port *before* the dispatcher fires
    and short-circuits on a ``skip`` verdict.
    """

    def _build_producer(
        self,
        *,
        decision_port,
        completion_dispatch,
        github_state,
    ) -> AutonomyProducer:
        return AutonomyProducer(
            session_state=_StubSessionState(),
            coding_executor=_build_worker(Path(self._tmp.name)),
            github_state=github_state,
            completion_dispatch=completion_dispatch,
            decision_port=decision_port,
            clock=_fixed_clock(),
        )

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_skip_verdict_short_circuits_before_dispatcher(self) -> None:
        port = _SkipPort(reason="window suppressed")
        calls: List[Any] = []

        def _dispatch(*, candidate, now):
            calls.append(candidate)
            return ()

        producer = self._build_producer(
            decision_port=port,
            completion_dispatch=_dispatch,
            github_state=_StubGithubState(failed_rows=[_failed_pr_row()]),
        )
        report = producer.tick()
        self.assertEqual(len(port.calls), 1)
        # The port saw a typed DecisionRequest with kind=retry_guard.
        seen = port.calls[0]
        self.assertEqual(getattr(seen, "kind", ""), "retry_guard")
        # PR fact made it onto the request so a live prompt has it.
        facts = getattr(seen, "facts", {}) or {}
        self.assertEqual(facts.get("pr_number"), 91)
        self.assertEqual(facts.get("attempt"), 2)
        # Dispatcher must NOT have been called.
        self.assertEqual(calls, [])
        # Producer surfaced one SKIPPED dispatch carrying the port's reason.
        ci_dispatches = [
            d for d in report.dispatches if d.source == "ci_failed_pr"
        ]
        self.assertEqual(len(ci_dispatches), 1)
        self.assertEqual(ci_dispatches[0].outcome, DispatchOutcome.SKIPPED)
        self.assertIn("window suppressed", ci_dispatches[0].reason)
        # Branch lock was NOT held — guard rejected before lock acquire.
        self.assertEqual(report.locks_held, ())

    def test_advance_verdict_lets_dispatcher_run(self) -> None:
        port = _AdvancePort()
        calls: List[Any] = []

        def _dispatch(*, candidate, now):
            calls.append(candidate)
            return [
                AutonomyDispatch(
                    source="ci_failed_pr",
                    outcome=DispatchOutcome.DISPATCHED,
                    reason="retry enqueued",
                )
            ]

        producer = self._build_producer(
            decision_port=port,
            completion_dispatch=_dispatch,
            github_state=_StubGithubState(failed_rows=[_failed_pr_row()]),
        )
        report = producer.tick()
        self.assertEqual(len(port.calls), 1)
        self.assertEqual(len(calls), 1)
        outcomes = [d.outcome for d in report.dispatches if d.source == "ci_failed_pr"]
        self.assertIn(DispatchOutcome.DISPATCHED, outcomes)

    def test_port_raise_falls_back_to_dispatcher(self) -> None:
        import logging

        port = _RaisingPort()
        calls: List[Any] = []

        def _dispatch(*, candidate, now):
            calls.append(candidate)
            return [
                AutonomyDispatch(
                    source="ci_failed_pr",
                    outcome=DispatchOutcome.DISPATCHED,
                )
            ]

        producer_logger = logging.getLogger(
            "yule_orchestrator.agents.job_queue.autonomy_producer"
        )
        previous = producer_logger.level
        producer_logger.setLevel(logging.CRITICAL)
        try:
            producer = self._build_producer(
                decision_port=port,
                completion_dispatch=_dispatch,
                github_state=_StubGithubState(failed_rows=[_failed_pr_row()]),
            )
            report = producer.tick()
        finally:
            producer_logger.setLevel(previous)
        # Port was called, raised — dispatcher still ran (fast-path).
        self.assertEqual(port.calls, 1)
        self.assertEqual(len(calls), 1)
        self.assertTrue(report.has_work())

    def test_no_port_means_legacy_fast_path(self) -> None:
        calls: List[Any] = []

        def _dispatch(*, candidate, now):
            calls.append(candidate)
            return [
                AutonomyDispatch(
                    source="ci_failed_pr",
                    outcome=DispatchOutcome.DISPATCHED,
                )
            ]

        producer = self._build_producer(
            decision_port=None,
            completion_dispatch=_dispatch,
            github_state=_StubGithubState(failed_rows=[_failed_pr_row()]),
        )
        report = producer.tick()
        # Dispatcher ran, no decision-port short-circuit anywhere.
        self.assertEqual(len(calls), 1)
        self.assertTrue(report.has_work())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
