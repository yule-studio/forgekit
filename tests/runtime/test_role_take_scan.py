"""runtime.fallback.scan_role_take_results — A-M7.2 unit tests.

Pin the role-bucketing contract exposed via
:class:`RoleTakeStatusReport`:

  * SAVED row → completed
  * FAILED_RETRYABLE pending (no SAVED, no FAILED_TERMINAL) → pending
  * FAILED_TERMINAL only → failed
  * No row at all → missing
  * Synthesis-kind rows are skipped (self-referential)
  * ``terminal_decision_safe`` blocks all-fail trigger when retry pending
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Tuple

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.role_take_worker import (
    JOB_TYPE_ROLE_TAKE,
    KIND_OPEN,
    KIND_SYNTHESIS,
)
from yule_orchestrator.agents.job_queue.state_machine import JobState
from yule_orchestrator.agents.job_queue.store import JobQueue
from yule_orchestrator.runtime.fallback import (
    RoleTakeStatusReport,
    scan_role_take_results,
)


class _ScannerFixture(unittest.TestCase):
    SESSION_ID: str = "sess-scan-1"
    EXPECTED: Tuple[str, ...] = ("tech-lead", "backend-engineer", "qa-engineer")

    def setUp(self) -> None:  # noqa: D401
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=self._db)

    def _enqueue_role_take(
        self, *, role: str, kind: str = KIND_OPEN
    ):
        return self.queue.enqueue(
            session_id=self.SESSION_ID,
            job_type=JOB_TYPE_ROLE_TAKE,
            role=role,
            payload={"kind": kind},
        )

    def _drive_to(self, job, target: JobState) -> None:
        # QUEUED → ASSIGNED → IN_PROGRESS → target. The store
        # tolerates direct transitions for these edges.
        self.queue.transition(job.job_id, JobState.ASSIGNED)
        self.queue.transition(job.job_id, JobState.IN_PROGRESS)
        if target == JobState.SAVED:
            self.queue.transition(job.job_id, JobState.SAVED)
        elif target == JobState.FAILED_RETRYABLE:
            self.queue.transition(
                job.job_id,
                JobState.FAILED_RETRYABLE,
                clear_lease=True,
            )
        elif target == JobState.FAILED_TERMINAL:
            self.queue.transition(
                job.job_id,
                JobState.FAILED_RETRYABLE,
                clear_lease=True,
            )
            self.queue.transition(job.job_id, JobState.FAILED_TERMINAL)
        else:
            raise AssertionError(f"unsupported target {target}")


class HappyPathTests(_ScannerFixture):
    def test_all_completed_marks_every_role_completed(self) -> None:
        for role in self.EXPECTED:
            j = self._enqueue_role_take(role=role)
            self._drive_to(j, JobState.SAVED)
        scan = scan_role_take_results(
            queue=self.queue,
            session_id=self.SESSION_ID,
            expected_roles=self.EXPECTED,
        )
        self.assertEqual(scan.completed_roles, self.EXPECTED)
        self.assertEqual(scan.failed_roles, ())
        self.assertEqual(scan.pending_roles, ())
        self.assertEqual(scan.missing_roles, ())
        self.assertFalse(scan.degrade_required)
        self.assertFalse(scan.all_terminally_failed)
        self.assertTrue(scan.terminal_decision_safe)

    def test_synthesis_kind_rows_are_ignored(self) -> None:
        # tech-lead has both an "open" SAVED row and a "synthesis"
        # SAVED row. The scanner must classify tech-lead based on
        # the open row only — counting synthesis would be
        # self-referential because the caller IS the synthesis.
        j_open = self._enqueue_role_take(role="tech-lead", kind=KIND_OPEN)
        self._drive_to(j_open, JobState.SAVED)
        j_synth = self._enqueue_role_take(
            role="tech-lead", kind=KIND_SYNTHESIS
        )
        self._drive_to(j_synth, JobState.FAILED_TERMINAL)
        scan = scan_role_take_results(
            queue=self.queue,
            session_id=self.SESSION_ID,
            expected_roles=("tech-lead",),
        )
        self.assertEqual(scan.completed_roles, ("tech-lead",))
        self.assertEqual(scan.failed_roles, ())


class PartialFailureTests(_ScannerFixture):
    def test_one_terminal_failure_marks_role_failed(self) -> None:
        # backend-engineer: terminal failure
        j_be = self._enqueue_role_take(role="backend-engineer")
        self._drive_to(j_be, JobState.FAILED_TERMINAL)
        # tech-lead: completed
        j_tl = self._enqueue_role_take(role="tech-lead")
        self._drive_to(j_tl, JobState.SAVED)
        # qa-engineer: never enqueued

        scan = scan_role_take_results(
            queue=self.queue,
            session_id=self.SESSION_ID,
            expected_roles=self.EXPECTED,
        )
        self.assertEqual(scan.completed_roles, ("tech-lead",))
        self.assertEqual(scan.failed_roles, ("backend-engineer",))
        self.assertEqual(scan.missing_roles, ("qa-engineer",))
        self.assertEqual(scan.pending_roles, ())
        self.assertTrue(scan.terminal_decision_safe)
        # Degrade required (not all-fail).
        self.assertTrue(scan.degrade_required)
        self.assertFalse(scan.all_terminally_failed)

    def test_completed_then_failed_terminal_keeps_completed(self) -> None:
        # Same role has BOTH a SAVED row (earlier "open" take) and
        # a later FAILED_TERMINAL row (e.g. chained "turn"). SAVED
        # wins — the role contributed.
        j_ok = self._enqueue_role_take(role="tech-lead", kind=KIND_OPEN)
        self._drive_to(j_ok, JobState.SAVED)
        j_fail = self._enqueue_role_take(role="tech-lead", kind="turn")
        self._drive_to(j_fail, JobState.FAILED_TERMINAL)
        scan = scan_role_take_results(
            queue=self.queue,
            session_id=self.SESSION_ID,
            expected_roles=("tech-lead",),
        )
        self.assertEqual(scan.completed_roles, ("tech-lead",))
        self.assertEqual(scan.failed_roles, ())


class PendingRetryTests(_ScannerFixture):
    def test_failed_retryable_blocks_all_terminal_decision(self) -> None:
        # Both roles fail terminally except one which is still
        # retrying. Scanner reports terminal_decision_safe=False
        # so the synthesis runner must NOT trigger fallback.
        j_be = self._enqueue_role_take(role="backend-engineer")
        self._drive_to(j_be, JobState.FAILED_TERMINAL)
        j_tl = self._enqueue_role_take(role="tech-lead")
        self._drive_to(j_tl, JobState.FAILED_RETRYABLE)
        scan = scan_role_take_results(
            queue=self.queue,
            session_id=self.SESSION_ID,
            expected_roles=("tech-lead", "backend-engineer"),
        )
        self.assertEqual(scan.pending_roles, ("tech-lead",))
        self.assertEqual(scan.failed_roles, ("backend-engineer",))
        self.assertFalse(scan.terminal_decision_safe)
        # neither all-fail nor degrade-required while a retry is pending
        self.assertFalse(scan.all_terminally_failed)
        self.assertFalse(scan.degrade_required)


class AllTerminalTests(_ScannerFixture):
    def test_all_failed_terminal_triggers_all_terminally_failed(self) -> None:
        for role in self.EXPECTED:
            j = self._enqueue_role_take(role=role)
            self._drive_to(j, JobState.FAILED_TERMINAL)
        scan = scan_role_take_results(
            queue=self.queue,
            session_id=self.SESSION_ID,
            expected_roles=self.EXPECTED,
        )
        self.assertTrue(scan.all_terminally_failed)
        self.assertTrue(scan.terminal_decision_safe)
        self.assertEqual(scan.failed_roles, self.EXPECTED)


class EmptyInputTests(unittest.TestCase):
    def test_empty_session_or_roles_returns_all_missing(self) -> None:
        # No queue access for the empty-input early return.
        class _DummyQueue:
            def list_for_session(self, session_id, *, states=()):
                raise AssertionError(
                    "scanner must not query for empty inputs"
                )

        scan = scan_role_take_results(
            queue=_DummyQueue(),
            session_id="",
            expected_roles=("tech-lead",),
        )
        self.assertEqual(scan.missing_roles, ("tech-lead",))
        self.assertEqual(scan.completed_roles, ())
        self.assertFalse(scan.all_terminally_failed)


if __name__ == "__main__":
    unittest.main()
