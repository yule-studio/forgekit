"""``runtime status`` exposes the coding-execute pipeline for operators.

These tests pin the operator-surface portion of the
``approved → coding_execute`` debugging session described by
``3163b5cf6c9b``. Before the fix the executor service was reported but
its expected job type was unknown, the queue summary hid
``coding_execute`` while no work existed, and there was no way to tell
"executor idle" from "executor missing".

Coverage:

* ``ServiceKind.CODING_EXECUTOR`` resolves to the ``coding_execute`` job
  type on ``ServiceStatus.job_type``.
* The queue summary always carries a ``coding_execute`` row, even on an
  empty queue.
* ``CodingDispatchSummary`` distinguishes "ready waiting" from
  "ready + already dispatched" so operators can tell which side of the
  producer tick they're on.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace as _replace
from datetime import datetime, timezone

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents import (
    Dispatcher,
    WorkflowOrchestrator,
    build_participants_pool,
)
from yule_engineering.agents.coding.authorization import LIFECYCLE_MODE_IMPLEMENTATION
from yule_engineering.agents.job_queue.coding_execute_dispatcher import (
    SESSION_EXTRA_DISPATCH_KEY,
)
from yule_engineering.agents.job_queue.heartbeat import HeartbeatRecord, HeartbeatStore
from yule_engineering.agents.job_queue.store import JobQueue
from yule_engineering.agents.workflow_state import load_session, update_session
from yule_runtime.services import ServiceKind
from yule_engineering.runtime.status import (
    _KIND_TO_JOB_TYPE,
    CodingDispatchSummary,
    build_runtime_status,
)
from pathlib import Path


_FIXED_NOW = 1_700_000_000.0


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _make_proposal_dict(session_id: str) -> dict:
    return {
        "session_id": session_id,
        "user_request": "새 랜딩 hero 정리",
        "executor_role": "backend-engineer",
        "review_roles": ["tech-lead"],
        "participant_roles": ["backend-engineer", "tech-lead"],
        "write_scope": ["web/**"],
        "forbidden_scope": [".env"],
        "reason": "Tech lead 추천",
        "safety_rules": ["user 승인 phrase 도착 전 write 금지"],
        "approval_required": True,
        "metadata": {"repo_full_name": "yule-studio/test-repo"},
        "lifecycle_mode": LIFECYCLE_MODE_IMPLEMENTATION,
        "research_leads": [],
    }


class CodingExecutorServiceKindTests(unittest.TestCase):
    def test_coding_executor_service_kind_maps_to_coding_execute_job_type(self) -> None:
        # The bug surface: `eng-coding-executor` showed ALIVE but its
        # `job_type` field was empty, so operators couldn't pair the
        # service health row with the queue/coding-dispatch rows.
        self.assertEqual(_KIND_TO_JOB_TYPE[ServiceKind.CODING_EXECUTOR], "coding_execute")


class _RuntimeStatusFixture(unittest.TestCase):
    PROFILE_NAME = "engineering"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._prev_db = os.environ.get("YULE_CACHE_DB_PATH")
        os.environ["YULE_CACHE_DB_PATH"] = os.path.join(self._tmp.name, "cache.sqlite3")
        self.queue = JobQueue(db_path=Path(self._tmp.name) / "queue.sqlite3")
        self.heartbeats = HeartbeatStore(db_path=Path(self._tmp.name) / "hb.sqlite3")
        pool = build_participants_pool(Path("."), "engineering-agent")
        self.orchestrator = WorkflowOrchestrator(Dispatcher(pool))

    def tearDown(self) -> None:
        if self._prev_db is None:
            os.environ.pop("YULE_CACHE_DB_PATH", None)
        else:
            os.environ["YULE_CACHE_DB_PATH"] = self._prev_db

    def _approve_with_pending_proposal(self) -> str:
        intake = self.orchestrator.intake(
            prompt="새 랜딩 hero 정리",
            write_requested=True,
        )
        session_id = intake.session.session_id
        session = load_session(session_id)
        assert session is not None
        extra = dict(session.extra or {})
        extra["coding_proposal"] = _make_proposal_dict(session_id)
        extra["coding_job"] = None
        update_session(_replace(session, extra=extra), now=_now_dt())
        self.orchestrator.approve(session_id)
        return session_id


class QueueSummarySurfaceTests(_RuntimeStatusFixture):
    def test_queue_summary_always_includes_coding_execute_even_on_empty_queue(
        self,
    ) -> None:
        report = build_runtime_status(
            profile=self.PROFILE_NAME,
            queue=self.queue,
            heartbeats=self.heartbeats,
            now=_FIXED_NOW,
        )
        names = {jt.job_type for jt in report.job_types}
        self.assertIn("coding_execute", names)
        coding_row = next(jt for jt in report.job_types if jt.job_type == "coding_execute")
        self.assertEqual(coding_row.queued, 0)
        self.assertEqual(coding_row.in_progress, 0)


class CodingDispatchSummaryTests(_RuntimeStatusFixture):
    def test_empty_runtime_reports_zero_summary(self) -> None:
        # No approved sessions, no dispatch markers → all zero, no sample.
        report = build_runtime_status(
            profile=self.PROFILE_NAME,
            queue=self.queue,
            heartbeats=self.heartbeats,
            now=_FIXED_NOW,
        )
        self.assertEqual(report.coding_dispatch, CodingDispatchSummary())

    def test_approved_session_without_dispatch_marker_counted_as_ready(self) -> None:
        # This is the "executor alive but no dispatch" diagnostic case
        # from the bug report. Operator should be able to see N>0 ready
        # sessions and 0 dispatched, which means "approved coding flow
        # reached the queue boundary but producer tick didn't fire".
        session_id = self._approve_with_pending_proposal()

        report = build_runtime_status(
            profile=self.PROFILE_NAME,
            queue=self.queue,
            heartbeats=self.heartbeats,
            now=_FIXED_NOW,
        )
        summary = report.coding_dispatch
        self.assertEqual(summary.ready_sessions, 1)
        self.assertEqual(summary.dispatched_sessions, 0)
        self.assertIn(session_id, summary.sample_session_ids)

    def test_dispatched_session_counted_under_dispatched_not_ready(self) -> None:
        # Once the producer tick has run, the session keeps coding_job=
        # ready BUT carries a dispatch marker. The summary moves it to
        # ``dispatched_sessions`` so operators can see "all caught up".
        session_id = self._approve_with_pending_proposal()
        session = load_session(session_id)
        assert session is not None
        extra = dict(session.extra or {})
        extra[SESSION_EXTRA_DISPATCH_KEY] = {
            "job_id": "job-deadbeef",
            "dispatched_at": "2026-05-14T08:00:00+00:00",
        }
        update_session(_replace(session, extra=extra), now=_now_dt())

        report = build_runtime_status(
            profile=self.PROFILE_NAME,
            queue=self.queue,
            heartbeats=self.heartbeats,
            now=_FIXED_NOW,
        )
        summary = report.coding_dispatch
        self.assertEqual(summary.ready_sessions, 0)
        self.assertEqual(summary.dispatched_sessions, 1)


class CodingExecutorServiceStatusJobTypeTests(_RuntimeStatusFixture):
    def test_coding_executor_service_status_carries_coding_execute_job_type(
        self,
    ) -> None:
        # Stamp a fresh heartbeat for the coding-executor service id so
        # build_runtime_status sees it as ALIVE — same posture as the
        # operator reported (executor up, dispatch missing).
        from yule_runtime.services import list_services

        services = list_services()
        coding_specs = [s for s in services if s.kind == ServiceKind.CODING_EXECUTOR]
        if not coding_specs:
            self.skipTest("coding executor service not registered in this profile")
        spec = coding_specs[0]
        self.heartbeats.record(
            spec.service_id,
            pid=12345,
            now=_FIXED_NOW - 1.0,
        )

        report = build_runtime_status(
            profile=self.PROFILE_NAME,
            queue=self.queue,
            heartbeats=self.heartbeats,
            now=_FIXED_NOW,
        )
        rows = [s for s in report.services if s.service_id == spec.service_id]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].job_type, "coding_execute")


if __name__ == "__main__":
    unittest.main()
