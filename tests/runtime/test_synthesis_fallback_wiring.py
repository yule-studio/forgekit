"""Synthesis runner degrade/fallback wiring — A-M7.2 integration tests.

Drive ``_default_build_synthesis_outcome`` end-to-end with a real
temp SQLite queue + a real :class:`WorkflowSession`, varying the
state of the seeded role_take rows so we observe each branch:

  * all roles SAVED → normal synthesis, no fallback audit
  * partial failure (some FAILED_TERMINAL, some SAVED) → degrade
    banner + audit (``human_approval_required=False``)
  * all roles FAILED_TERMINAL → deterministic fallback synthesis
    + audit (``human_approval_required=True``), output marked
    "fallback으로 생성됨"
  * any FAILED_RETRYABLE pending → no fallback, no degrade banner
    (the retry might still complete)
  * audit persistence flows into ``session.extra['fallback_audits']``
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from unittest import mock

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.role_take_worker import (
    JOB_TYPE_ROLE_TAKE,
    KIND_OPEN,
)
from yule_engineering.agents.job_queue.standalone_runners import (
    _default_build_synthesis_outcome,
)
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue
from yule_engineering.agents.workflow_state import (
    WorkflowSession,
    WorkflowState,
    save_session,
)


class _SynthesisRunnerFixture(unittest.TestCase):
    SESSION_ID: str = "sess-synth-fallback-1"
    EXPECTED_ROLES = (
        "tech-lead",
        "backend-engineer",
        "qa-engineer",
    )

    def setUp(self) -> None:  # noqa: D401
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        # Pin the workflow_state cache to our tmp tree so save_session
        # / load_session inside persist_fallback_audit see the same
        # session this test seeded. Without the override the tests
        # would land on the bootstrap-default cache and the audit
        # bucket would silently land on a stranger session row.
        self._env = mock.patch.dict(
            os.environ,
            {
                "YULE_CACHE_DB_PATH": str(self._db),
                "YULE_REPO_ROOT": str(self._tmp.name),
            },
        )
        self._env.start()
        self.addCleanup(self._env.stop)

        self.queue = JobQueue(db_path=self._db)
        self.session = self._save_session()

        # Captured arguments + return outcomes for each branch the
        # default synthesis runner can take. Tests override what they
        # need.
        self.audited: List = []

        def audit_persist(record):
            self.audited.append(record)
            return True

        self.audit_persist = audit_persist

    def _save_session(self) -> WorkflowSession:
        when = datetime.now(tz=timezone.utc)
        session = WorkflowSession(
            session_id=self.SESSION_ID,
            prompt="결정 노트 정리 부탁",
            task_type="research",
            state=WorkflowState.IN_PROGRESS,
            created_at=when,
            updated_at=when,
            role_sequence=self.EXPECTED_ROLES,
            extra={"active_research_roles": list(self.EXPECTED_ROLES)},
        )
        save_session(session)
        return session

    def _enqueue(self, *, role: str, kind: str = KIND_OPEN):
        return self.queue.enqueue(
            session_id=self.SESSION_ID,
            job_type=JOB_TYPE_ROLE_TAKE,
            role=role,
            payload={"kind": kind},
        )

    def _drive(self, job, target: JobState) -> None:
        self.queue.transition(job.job_id, JobState.ASSIGNED)
        self.queue.transition(job.job_id, JobState.IN_PROGRESS)
        if target == JobState.SAVED:
            self.queue.transition(job.job_id, JobState.SAVED)
        elif target == JobState.FAILED_RETRYABLE:
            self.queue.transition(
                job.job_id, JobState.FAILED_RETRYABLE, clear_lease=True
            )
        elif target == JobState.FAILED_TERMINAL:
            self.queue.transition(
                job.job_id, JobState.FAILED_RETRYABLE, clear_lease=True
            )
            self.queue.transition(job.job_id, JobState.FAILED_TERMINAL)
        else:
            raise AssertionError(f"unsupported target: {target}")

    def _seed_synthesis_text(self, text: str = "기존 합의안 본문") -> None:
        # Pre-stamp session.extra['research_synthesis_text'] so the
        # default branch (no degrade, no fallback) reuses the cached
        # text instead of recomputing.
        from dataclasses import replace as _replace
        from yule_engineering.agents.workflow_state import (
            update_session,
        )

        extra = dict(self.session.extra or {})
        extra["research_synthesis_text"] = text
        updated = _replace(self.session, extra=extra)
        update_session(updated, now=datetime.now(tz=timezone.utc))
        self.session = updated

    def _run_synthesis(self):
        return _default_build_synthesis_outcome(
            role="tech-lead",
            session_id=self.SESSION_ID,
            session=self.session,
            pack_loader=lambda _s: None,
            queue_factory=lambda: self.queue,
            audit_persist_fn=self.audit_persist,
        )


# ---------------------------------------------------------------------------
# Default path — no failures, no degrade
# ---------------------------------------------------------------------------


class NormalSynthesisTests(_SynthesisRunnerFixture):
    def test_all_completed_returns_synthesis_without_audit(self) -> None:
        for role in self.EXPECTED_ROLES:
            j = self._enqueue(role=role)
            self._drive(j, JobState.SAVED)
        self._seed_synthesis_text("정상 합의안")
        outcome = self._run_synthesis()
        self.assertTrue(outcome.is_synthesis)
        # No degrade banner present.
        self.assertNotIn("[degrade]", outcome.message)
        self.assertNotIn("fallback으로 생성됨", outcome.message)
        # No audit persisted.
        self.assertEqual(self.audited, [])
        # Cached synthesis text flowed through.
        self.assertIn("정상 합의안", outcome.message)


# ---------------------------------------------------------------------------
# Partial failure — degrade banner + audit
# ---------------------------------------------------------------------------


class PartialFailureDegradeTests(_SynthesisRunnerFixture):
    def test_failed_terminal_role_yields_degrade_banner_and_audit(
        self,
    ) -> None:
        # tech-lead + qa-engineer SAVED, backend-engineer FAILED_TERMINAL
        for role in ("tech-lead", "qa-engineer"):
            j = self._enqueue(role=role)
            self._drive(j, JobState.SAVED)
        bad = self._enqueue(role="backend-engineer")
        self._drive(bad, JobState.FAILED_TERMINAL)
        self._seed_synthesis_text("성공 role 합의안 본문")

        outcome = self._run_synthesis()
        # Degrade banner prepended; cached synthesis text preserved.
        self.assertIn("[degrade]", outcome.message)
        self.assertIn("backend-engineer", outcome.message)
        self.assertIn("성공 role 합의안 본문", outcome.message)
        # Audit recorded with the degraded authority — degrade does
        # NOT require human approval (low/medium risk path).
        self.assertEqual(len(self.audited), 1)
        record = self.audited[0]
        self.assertEqual(record.fallback_authority, "degraded_synthesis")
        self.assertEqual(record.failed_roles, ("backend-engineer",))
        self.assertFalse(record.human_approval_required)


# ---------------------------------------------------------------------------
# Pending retry — no fallback, no degrade
# ---------------------------------------------------------------------------


class PendingRetryDeferralTests(_SynthesisRunnerFixture):
    def test_failed_retryable_pending_blocks_fallback_trigger(
        self,
    ) -> None:
        # Two roles SAVED, one in FAILED_RETRYABLE → scanner reports
        # terminal_decision_safe=False. Synthesis must NOT add a
        # banner, and audit must NOT fire.
        for role in ("tech-lead", "qa-engineer"):
            j = self._enqueue(role=role)
            self._drive(j, JobState.SAVED)
        retrying = self._enqueue(role="backend-engineer")
        self._drive(retrying, JobState.FAILED_RETRYABLE)
        self._seed_synthesis_text("미완성 합의안")

        outcome = self._run_synthesis()
        self.assertNotIn("[degrade]", outcome.message)
        self.assertNotIn("fallback으로 생성됨", outcome.message)
        self.assertEqual(self.audited, [])

    def test_failed_terminal_alongside_failed_retryable_does_not_fallback(
        self,
    ) -> None:
        # Two FAILED_TERMINAL + one FAILED_RETRYABLE. scanner says
        # all_terminally_failed=False AND degrade_required=False
        # because of the pending retry — synthesis stays neutral.
        for role in ("tech-lead", "backend-engineer"):
            j = self._enqueue(role=role)
            self._drive(j, JobState.FAILED_TERMINAL)
        retrying = self._enqueue(role="qa-engineer")
        self._drive(retrying, JobState.FAILED_RETRYABLE)
        self._seed_synthesis_text("partial 합의안")

        outcome = self._run_synthesis()
        self.assertNotIn("fallback으로 생성됨", outcome.message)
        self.assertNotIn("[degrade]", outcome.message)
        self.assertEqual(self.audited, [])


# ---------------------------------------------------------------------------
# All-role fallback — deterministic synthesis + audit + approval-required
# ---------------------------------------------------------------------------


class AllRoleFallbackTests(_SynthesisRunnerFixture):
    def test_all_failed_terminal_triggers_deterministic_fallback(
        self,
    ) -> None:
        for role in self.EXPECTED_ROLES:
            j = self._enqueue(role=role)
            self._drive(j, JobState.FAILED_TERMINAL)

        outcome = self._run_synthesis()
        # Output plainly labelled as fallback so the operator can
        # never mistake it for a real consensus.
        self.assertIn("fallback으로 생성됨", outcome.message)
        # Approval requirement surfaces in the synthesis text via
        # render_synthesis (forced approval_required=True).
        self.assertIn("승인 필요: yes", outcome.message)
        # Audit captured with the all-fail authority.
        self.assertEqual(len(self.audited), 1)
        record = self.audited[0]
        self.assertEqual(
            record.fallback_authority, "deterministic_template"
        )
        self.assertTrue(record.human_approval_required)
        # All expected roles tagged as failed.
        self.assertEqual(set(record.failed_roles), set(self.EXPECTED_ROLES))


# ---------------------------------------------------------------------------
# Audit persistence — round-trip through the default workflow_state path
# ---------------------------------------------------------------------------


class AuditPersistenceTests(_SynthesisRunnerFixture):
    def test_default_persist_writes_to_session_extra(self) -> None:
        # Don't override audit_persist this time — exercise the real
        # ``persist_fallback_audit`` path through the workflow_state
        # cache the fixture pointed at our tmp dir.
        for role in self.EXPECTED_ROLES:
            j = self._enqueue(role=role)
            self._drive(j, JobState.FAILED_TERMINAL)

        _default_build_synthesis_outcome(
            role="tech-lead",
            session_id=self.SESSION_ID,
            session=self.session,
            pack_loader=lambda _s: None,
            queue_factory=lambda: self.queue,
            # No audit_persist_fn → uses persist_fallback_audit default.
        )

        # Reload the session via the production loader so we see the
        # bucket that the fallback audit wrote.
        from yule_engineering.agents.workflow_state import load_session

        reloaded = load_session(self.SESSION_ID)
        assert reloaded is not None
        bucket = (reloaded.extra or {}).get("fallback_audits") or []
        self.assertEqual(len(bucket), 1)
        self.assertEqual(
            bucket[0]["fallback_authority"], "deterministic_template"
        )
        self.assertTrue(bucket[0]["human_approval_required"])


# ---------------------------------------------------------------------------
# Resilience — scanner failure must NOT block synthesis
# ---------------------------------------------------------------------------


class ScannerFailureSwallowedTests(_SynthesisRunnerFixture):
    def test_queue_factory_raises_falls_through_to_legacy_synthesis(
        self,
    ) -> None:
        def boom():
            raise RuntimeError("queue temporarily unavailable")

        # No role_take rows seeded — synthesis must still run.
        outcome = _default_build_synthesis_outcome(
            role="tech-lead",
            session_id=self.SESSION_ID,
            session=self.session,
            pack_loader=lambda _s: None,
            queue_factory=boom,
            audit_persist_fn=self.audit_persist,
        )
        self.assertTrue(outcome.is_synthesis)
        # Neither degrade nor fallback fires when scanner raises.
        self.assertNotIn("fallback으로 생성됨", outcome.message)
        self.assertNotIn("[degrade]", outcome.message)
        # Audit not invoked.
        self.assertEqual(self.audited, [])


if __name__ == "__main__":
    unittest.main()
