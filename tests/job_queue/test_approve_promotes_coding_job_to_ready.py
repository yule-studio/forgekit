"""Approved coding sessions actually reach the executor (session 3163b5cf6c9b).

Reproduces the bug the operator filed: an ``/engineer_approve`` slash
command flipped a write-requested session into ``approved`` but left
``session.extra["coding_job"]`` either missing or stuck at
``pending-approval``. The dispatcher's ready-scan
(``iter_ready_coding_jobs``) therefore never saw the session and
``coding_execute`` was never enqueued — exactly matching the user's
``progress_notes=[] / summary=null / state=approved`` symptoms even
though ``eng-coding-executor`` reported ``ALIVE``.

These tests pin the fix: ``WorkflowOrchestrator.approve()`` promotes a
pending ``coding_proposal`` into a ``coding_job`` with ``status="ready"``
so the dispatcher hands the work to the executor on the next tick.
"""

from __future__ import annotations

import os
import tempfile
import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents import (
    Dispatcher,
    WorkflowOrchestrator,
    build_participants_pool,
)
from yule_engineering.agents.coding.authorization import (
    CodingAuthorizationProposal,
    LIFECYCLE_MODE_IMPLEMENTATION,
)
from yule_engineering.agents.coding.job import STATUS_READY
from yule_engineering.agents.job_queue.coding_execute_dispatcher import (
    SESSION_EXTRA_DISPATCH_KEY,
    iter_ready_coding_jobs,
)
from yule_engineering.agents.workflow_state import load_session, update_session
from datetime import datetime, timezone
from pathlib import Path


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_proposal_dict(session_id: str) -> dict:
    proposal = CodingAuthorizationProposal(
        session_id=session_id,
        user_request="새 랜딩 hero 정리",
        executor_role="backend-engineer",
        review_roles=("tech-lead",),
        participant_roles=("backend-engineer", "tech-lead"),
        write_scope=("web/**",),
        forbidden_scope=(".env",),
        reason="Tech lead 추천",
        safety_rules=("user 승인 phrase 도착 전 production write 금지",),
        approval_required=True,
        metadata={"repo_full_name": "yule-studio/test-repo"},
        lifecycle_mode=LIFECYCLE_MODE_IMPLEMENTATION,
        research_leads=(),
    )
    # Mirror Discord coding-gate's serialization format.
    return {
        "session_id": proposal.session_id,
        "user_request": proposal.user_request,
        "executor_role": proposal.executor_role,
        "review_roles": list(proposal.review_roles),
        "participant_roles": list(proposal.participant_roles),
        "write_scope": list(proposal.write_scope),
        "forbidden_scope": list(proposal.forbidden_scope),
        "reason": proposal.reason,
        "safety_rules": list(proposal.safety_rules),
        "approval_required": proposal.approval_required,
        "metadata": dict(proposal.metadata),
        "lifecycle_mode": proposal.lifecycle_mode,
        "research_leads": list(proposal.research_leads),
    }


class ApprovePromotesCodingJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._prev_db = os.environ.get("YULE_CACHE_DB_PATH")
        os.environ["YULE_CACHE_DB_PATH"] = os.path.join(self._tmp.name, "cache.sqlite3")
        pool = build_participants_pool(Path("."), "engineering-agent")
        self.orchestrator = WorkflowOrchestrator(Dispatcher(pool))

    def tearDown(self) -> None:
        if self._prev_db is None:
            os.environ.pop("YULE_CACHE_DB_PATH", None)
        else:
            os.environ["YULE_CACHE_DB_PATH"] = self._prev_db

    def _intake_with_pending_proposal(self) -> str:
        intake = self.orchestrator.intake(
            prompt="새 랜딩 hero 정리",
            write_requested=True,
        )
        session_id = intake.session.session_id

        # Mimic Discord coding-proposal persistence (``_persist_coding_proposal``):
        # write ``extra["coding_proposal"]`` AND clear any prior coding_job.
        session = load_session(session_id)
        assert session is not None
        new_extra = dict(session.extra or {})
        new_extra["coding_proposal"] = _make_proposal_dict(session_id)
        new_extra["coding_job"] = None
        from dataclasses import replace as _replace

        updated = _replace(session, extra=new_extra)
        update_session(updated, now=_now())
        return session_id

    def test_approve_promotes_pending_proposal_to_ready_coding_job(self) -> None:
        session_id = self._intake_with_pending_proposal()

        approved = self.orchestrator.approve(session_id)

        self.assertEqual(approved.state.value, "approved")
        coding_job = (approved.extra or {}).get("coding_job")
        self.assertIsInstance(coding_job, dict)
        assert isinstance(coding_job, dict)
        self.assertEqual(coding_job["status"], STATUS_READY)
        self.assertEqual(coding_job["executor_role"], "backend-engineer")
        # Approved timestamp gets stamped — guards against the
        # build_coding_job_from_proposal contract drift.
        self.assertTrue(coding_job.get("approved_at"))

    def test_approve_is_idempotent_when_ready_coding_job_already_persisted(
        self,
    ) -> None:
        # Pre-seed the session with a fully ready coding_job (e.g. the
        # Discord chat-phrase approval path ran first). approve() must
        # leave it untouched so the chat path's audit fields survive.
        session_id = self._intake_with_pending_proposal()
        from dataclasses import replace as _replace

        session = load_session(session_id)
        assert session is not None
        pre_existing_job = {
            "session_id": session_id,
            "executor_role": "backend-engineer",
            "status": STATUS_READY,
            "approved_at": "2026-05-14T07:00:00+00:00",
            "approved_via": "discord-chat-phrase",
        }
        extra = dict(session.extra or {})
        extra["coding_job"] = pre_existing_job
        update_session(_replace(session, extra=extra), now=_now())

        approved = self.orchestrator.approve(session_id)
        coding_job = (approved.extra or {}).get("coding_job")
        self.assertIsInstance(coding_job, dict)
        assert isinstance(coding_job, dict)
        # approve() did not overwrite the chat-path audit fields.
        self.assertEqual(coding_job.get("approved_via"), "discord-chat-phrase")
        self.assertEqual(coding_job["status"], STATUS_READY)

    def test_approve_without_pending_proposal_does_not_synth_coding_job(
        self,
    ) -> None:
        # A research-only intake never had a coding_proposal. approve()
        # must NOT invent one — that would let dispatcher pick the
        # session and dispatch a bogus coding_execute.
        intake = self.orchestrator.intake(prompt="문서만 조사", write_requested=False)
        approved = self.orchestrator.approve(intake.session.session_id)
        self.assertEqual(approved.state.value, "approved")
        self.assertNotIn("coding_job", approved.extra or {})

    def test_iter_ready_coding_jobs_picks_up_session_after_approve(self) -> None:
        # End-to-end pin: approve() → iter_ready sees the session →
        # dispatcher would enqueue coding_execute on the next tick.
        session_id = self._intake_with_pending_proposal()
        self.orchestrator.approve(session_id)

        ready = list(iter_ready_coding_jobs())
        ready_ids = {item.session_id for item in ready}
        self.assertIn(session_id, ready_ids)

    def test_iter_ready_coding_jobs_skips_session_after_dispatch_marker_lands(
        self,
    ) -> None:
        # Once the dispatcher has stamped session.extra["coding_execute_dispatch"],
        # the next iteration must not yield the session again
        # (dispatch is idempotent; double-enqueue would burn executor cycles).
        session_id = self._intake_with_pending_proposal()
        self.orchestrator.approve(session_id)

        from dataclasses import replace as _replace

        session = load_session(session_id)
        assert session is not None
        extra = dict(session.extra or {})
        extra[SESSION_EXTRA_DISPATCH_KEY] = {
            "job_id": "job-abc123",
            "dispatched_at": "2026-05-14T07:30:00+00:00",
        }
        update_session(_replace(session, extra=extra), now=_now())

        ready = list(iter_ready_coding_jobs())
        ready_ids = {item.session_id for item in ready}
        self.assertNotIn(session_id, ready_ids)

        # But include_dispatched=True still sees it — that's how the
        # operator-surface counter distinguishes "ready waiting" from
        # "ready + already dispatched".
        all_ready = list(iter_ready_coding_jobs(include_dispatched=True))
        all_ids = {item.session_id for item in all_ready}
        self.assertIn(session_id, all_ids)


if __name__ == "__main__":
    unittest.main()
