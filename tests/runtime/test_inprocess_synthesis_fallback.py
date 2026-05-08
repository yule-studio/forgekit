"""Legacy in-process synthesis path → A-M7.2 fallback wiring.

A-M7-final: ``yule discord up`` 's in-process gateway used to run
its own ``_synthesis_runner`` closure that just called
``synthesize_thread`` over replayed role takes — degrade /
fallback was invisible there. We delegated to the standalone
helper so the same trigger logic applies. This suite proves it.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.role_take_worker import (
    JOB_TYPE_ROLE_TAKE,
    KIND_OPEN,
)
from yule_orchestrator.agents.job_queue.state_machine import JobState
from yule_orchestrator.agents.job_queue.store import JobQueue
from yule_orchestrator.agents.workflow_state import (
    WorkflowSession,
    WorkflowState,
    save_session,
)
from yule_orchestrator.discord.engineering_team_runtime import (
    handle_research_turn_message,
    reset_handled_turns_for_tests,
)


class _LegacySynthesisFixture(unittest.TestCase):
    """Per-test isolation for the in-process synthesis path.

    Each test gets a unique session id so the process-local
    "recently handled" cache (which keys on session_id + role + kind)
    can't bleed across tests. The cache is also explicitly reset in
    setUp as belt-and-braces.
    """

    ROLES = ("tech-lead", "backend-engineer")

    def setUp(self) -> None:  # noqa: D401
        self.SESSION_ID = f"sess-legacy-synth-{self.id().rsplit('.', 1)[-1]}"
        reset_handled_turns_for_tests()
        self.addCleanup(reset_handled_turns_for_tests)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "cache.sqlite3"
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

    def _save_session(self) -> WorkflowSession:
        when = datetime.now(tz=timezone.utc)
        session = WorkflowSession(
            session_id=self.SESSION_ID,
            prompt="legacy synthesis 테스트",
            task_type="research",
            state=WorkflowState.IN_PROGRESS,
            created_at=when,
            updated_at=when,
            role_sequence=self.ROLES,
            extra={"active_research_roles": list(self.ROLES)},
        )
        save_session(session)
        return session

    def _enqueue_role_take(self, *, role: str, target: JobState):
        job = self.queue.enqueue(
            session_id=self.SESSION_ID,
            job_type=JOB_TYPE_ROLE_TAKE,
            role=role,
            payload={"kind": KIND_OPEN},
        )
        self.queue.transition(job.job_id, JobState.ASSIGNED)
        self.queue.transition(job.job_id, JobState.IN_PROGRESS)
        if target == JobState.SAVED:
            self.queue.transition(job.job_id, JobState.SAVED)
        elif target == JobState.FAILED_TERMINAL:
            self.queue.transition(
                job.job_id, JobState.FAILED_RETRYABLE, clear_lease=True
            )
            self.queue.transition(job.job_id, JobState.FAILED_TERMINAL)
        else:
            raise AssertionError(f"unsupported target {target}")

    def _drive_in_process_synthesis(self):
        # The legacy path is hit when a member bot picks up a
        # ``[research-turn:<sid> tech-lead-synthesis]`` marker with
        # role=tech-lead.
        text = f"[research-turn:{self.SESSION_ID} tech-lead-synthesis]"
        return handle_research_turn_message(role="tech-lead", text=text)


class LegacyDegradeTests(_LegacySynthesisFixture):
    def test_partial_failure_surfaces_degrade_banner(self) -> None:
        # tech-lead SAVED, backend-engineer FAILED_TERMINAL.
        # The in-process synthesis runner must produce a degrade
        # banner just like the standalone runner does.
        self._enqueue_role_take(role="tech-lead", target=JobState.SAVED)
        self._enqueue_role_take(
            role="backend-engineer", target=JobState.FAILED_TERMINAL
        )
        outcome = self._drive_in_process_synthesis()
        self.assertIsNotNone(outcome)
        assert outcome is not None
        self.assertTrue(outcome.is_synthesis)
        # Same banner shape that the standalone path emits.
        self.assertIn("[degrade]", outcome.message)
        self.assertIn("backend-engineer", outcome.message)


class LegacyAllRoleFallbackTests(_LegacySynthesisFixture):
    def test_all_failed_terminal_triggers_deterministic_fallback(
        self,
    ) -> None:
        for role in self.ROLES:
            self._enqueue_role_take(role=role, target=JobState.FAILED_TERMINAL)
        outcome = self._drive_in_process_synthesis()
        self.assertIsNotNone(outcome)
        assert outcome is not None
        self.assertTrue(outcome.is_synthesis)
        self.assertIn("fallback으로 생성됨", outcome.message)
        # Approval-required marker surfaces (M5b guard relies on this).
        self.assertIn("승인 필요: yes", outcome.message)


class LegacyPendingRetryTests(_LegacySynthesisFixture):
    def test_failed_retryable_pending_does_not_trigger_fallback(
        self,
    ) -> None:
        # tech-lead SAVED, backend-engineer FAILED_RETRYABLE.
        # The terminal-decision-safe gate must keep the legacy path
        # running normal synthesis without a fallback banner.
        tl = self.queue.enqueue(
            session_id=self.SESSION_ID,
            job_type=JOB_TYPE_ROLE_TAKE,
            role="tech-lead",
            payload={"kind": KIND_OPEN},
        )
        self.queue.transition(tl.job_id, JobState.ASSIGNED)
        self.queue.transition(tl.job_id, JobState.IN_PROGRESS)
        self.queue.transition(tl.job_id, JobState.SAVED)

        be = self.queue.enqueue(
            session_id=self.SESSION_ID,
            job_type=JOB_TYPE_ROLE_TAKE,
            role="backend-engineer",
            payload={"kind": KIND_OPEN},
        )
        self.queue.transition(be.job_id, JobState.ASSIGNED)
        self.queue.transition(be.job_id, JobState.IN_PROGRESS)
        self.queue.transition(
            be.job_id, JobState.FAILED_RETRYABLE, clear_lease=True
        )

        outcome = self._drive_in_process_synthesis()
        self.assertIsNotNone(outcome)
        assert outcome is not None
        self.assertNotIn("fallback으로 생성됨", outcome.message)
        self.assertNotIn("[degrade]", outcome.message)


if __name__ == "__main__":
    unittest.main()
