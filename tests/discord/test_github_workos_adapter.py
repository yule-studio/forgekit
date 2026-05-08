"""G4 — Discord 업무접수 → GitHub WorkOS bridge adapter tests.

Pins:

  * Simple research / DevOps learning question → no proposal, no
    approval card, no work order ever queued.
  * Clear coding request ("이 버그 고쳐서 PR 올려줘") →
    GitHubWorkOrderProposal with intent_actions, ApprovalWorker
    enqueues exactly one engineering_write card, no
    github_work_order row appears before approval.
  * "GitHub Actions workflow 고쳐줘" → devops-engineer participant +
    github_actions action label, approval still required.
  * Duplicate request (same session + source_message_id) → second
    call returns SKIPPED_DUPLICATE_APPROVAL, queue still has one row.
  * Excluded role (session.extra['excluded_research_roles']) never
    appears in proposal.selected_roles.
  * After approval reply,
    :func:`handle_github_work_approval_reply` enqueues the
    github_work_order row with dry_run=True and the approval triple
    stamped.
  * Existing Obsidian save flow ("Obsidian에 정리해줘") is **not
    routed** by this adapter — the existing M10 flow keeps that
    surface.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.approval_worker import (
    APPROVAL_KIND_ENGINEERING_WRITE,
    APPROVAL_KIND_OBSIDIAN_WRITE,
    ApprovalRequest,
    ApprovalWorker,
)
from yule_orchestrator.agents.job_queue.github_work_order import (
    JOB_TYPE_GITHUB_WORK_ORDER,
)
from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.store import JobQueue
from yule_orchestrator.discord.github_workos_adapter import (
    SKIPPED_DUPLICATE_APPROVAL,
    SKIPPED_NO_CODING_INTENT,
    SKIPPED_OBSIDIAN_INTENT,
    SKIPPED_RESEARCH_ONLY,
    build_github_work_order_proposal,
    detect_obsidian_intent,
    enqueue_github_work_approval,
    handle_github_work_approval_reply,
    should_route_to_github_workos,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _session(
    *,
    session_id: str = "sess-g4",
    lifecycle_mode: Optional[str] = "implementation",
    active_research_roles=("tech-lead", "backend-engineer"),
    excluded_research_roles=(),
    coding_proposal_executor: Optional[str] = None,
    coding_proposal_review=(),
    coding_proposal_participants=(),
):
    extra: dict = {}
    if lifecycle_mode is not None:
        extra["lifecycle_mode"] = lifecycle_mode
    if active_research_roles:
        extra["active_research_roles"] = list(active_research_roles)
    if excluded_research_roles:
        extra["excluded_research_roles"] = list(excluded_research_roles)
    if coding_proposal_executor:
        extra["coding_proposal"] = {
            "executor_role": coding_proposal_executor,
            "review_roles": list(coding_proposal_review),
            "participant_roles": list(coding_proposal_participants),
        }
    return SimpleNamespace(session_id=session_id, extra=extra)


class _AdapterFixture(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401 - test setup
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=self.db_path)
        self.heartbeats = HeartbeatStore(db_path=self.db_path)
        self.posted: list[tuple[ApprovalRequest, str]] = []

        async def post_fn(request, rendered):
            self.posted.append((request, rendered))
            return {"posted_message_id": 12345}

        self.approval_worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=post_fn,
            channel_resolver=lambda: 4242,
        )

    def _github_work_order_rows(self, session_id: str) -> list:
        return [
            job
            for job in self.queue.list_for_session(session_id)
            if job.job_type == JOB_TYPE_GITHUB_WORK_ORDER
        ]


# ---------------------------------------------------------------------------
# Routing / proposal builder
# ---------------------------------------------------------------------------


class ShouldRouteTests(unittest.TestCase):
    def test_research_only_lifecycle_blocks_routing(self) -> None:
        sess = _session(lifecycle_mode="research_only")
        eligible, reason, _ = should_route_to_github_workos(
            session=sess, request_text="이 버그 고쳐서 PR 올려줘"
        )
        self.assertFalse(eligible)
        self.assertEqual(reason, SKIPPED_RESEARCH_ONLY)

    def test_no_coding_intent_blocks_routing(self) -> None:
        sess = _session()
        eligible, reason, _ = should_route_to_github_workos(
            session=sess,
            request_text="DevOps 엔지니어가 되려면 어떤 책을 읽어야 해?",
        )
        self.assertFalse(eligible)
        self.assertEqual(reason, SKIPPED_NO_CODING_INTENT)

    def test_obsidian_intent_blocks_routing(self) -> None:
        sess = _session()
        eligible, reason, _ = should_route_to_github_workos(
            session=sess, request_text="Obsidian에 정리해줘"
        )
        self.assertFalse(eligible)
        self.assertEqual(reason, SKIPPED_OBSIDIAN_INTENT)

    def test_coding_intent_with_implementation_lifecycle_eligible(self) -> None:
        sess = _session()
        eligible, reason, _ = should_route_to_github_workos(
            session=sess, request_text="이 버그 고쳐서 PR 올려줘"
        )
        self.assertTrue(eligible)
        self.assertEqual(reason, "")


class DetectObsidianIntentTests(unittest.TestCase):
    def test_recognises_korean_save_phrases(self) -> None:
        self.assertTrue(detect_obsidian_intent("Obsidian에 정리해줘"))
        self.assertTrue(detect_obsidian_intent("옵시디언에 저장해줘"))
        self.assertTrue(detect_obsidian_intent("vault에 저장하고 싶어"))

    def test_unrelated_text_is_negative(self) -> None:
        self.assertFalse(detect_obsidian_intent("PR 올려줘"))
        self.assertFalse(detect_obsidian_intent(""))


class BuildProposalTests(unittest.TestCase):
    def test_simple_research_request_returns_none(self) -> None:
        sess = _session()
        proposal = build_github_work_order_proposal(
            session=sess,
            request_text="DevOps 책 추천해줘",
            source_channel_id=1,
            source_thread_id=2,
            source_message_id=3,
        )
        self.assertIsNone(proposal)

    def test_clear_coding_request_returns_proposal(self) -> None:
        sess = _session()
        proposal = build_github_work_order_proposal(
            session=sess,
            request_text="이 버그 고쳐서 PR 올려줘",
            source_channel_id=10,
            source_thread_id=20,
            source_message_id=30,
            requested_by="masterway",
        )
        self.assertIsNotNone(proposal)
        self.assertTrue(proposal.coding_required)
        self.assertEqual(proposal.session_id, "sess-g4")
        self.assertEqual(proposal.source_message_id, 30)
        self.assertIn("bug_fix", proposal.intent_actions)
        self.assertIn("pull_request", proposal.intent_actions)
        self.assertIn("tech-lead", proposal.selected_roles)
        # request_summary is a trimmed view of the request text.
        self.assertIn("PR", proposal.request_summary)
        # dry-run is the default — important "no live writes" guard.
        self.assertTrue(proposal.dry_run_default)

    def test_github_actions_request_routes_to_devops(self) -> None:
        sess = _session(active_research_roles=("tech-lead", "devops-engineer"))
        proposal = build_github_work_order_proposal(
            session=sess,
            request_text="GitHub Actions workflow 고쳐줘",
        )
        self.assertIsNotNone(proposal)
        self.assertIn("devops-engineer", proposal.selected_roles)
        self.assertIn("github_actions", proposal.intent_actions)

    def test_excluded_role_is_dropped_from_selected(self) -> None:
        sess = _session(
            active_research_roles=("tech-lead", "frontend-engineer", "backend-engineer"),
            excluded_research_roles=("frontend-engineer",),
        )
        proposal = build_github_work_order_proposal(
            session=sess,
            request_text="이 버그 고쳐서 PR 올려줘",
        )
        self.assertIsNotNone(proposal)
        self.assertIn("frontend-engineer", proposal.excluded_roles)
        self.assertNotIn("frontend-engineer", proposal.selected_roles)
        self.assertIn("backend-engineer", proposal.selected_roles)

    def test_coding_proposal_executor_lands_first(self) -> None:
        sess = _session(
            coding_proposal_executor="backend-engineer",
            coding_proposal_review=("tech-lead",),
            coding_proposal_participants=("qa-engineer",),
        )
        proposal = build_github_work_order_proposal(
            session=sess,
            request_text="이 버그 고쳐서 PR 올려줘",
        )
        self.assertIsNotNone(proposal)
        self.assertIn("backend-engineer", proposal.selected_roles)
        self.assertIn("qa-engineer", proposal.selected_roles)
        # tech-lead always reviews and lands at the front of the list.
        self.assertEqual(proposal.selected_roles[0], "tech-lead")


# ---------------------------------------------------------------------------
# Approval enqueue (pre-approval)
# ---------------------------------------------------------------------------


class EnqueueGithubWorkApprovalTests(_AdapterFixture):
    def test_simple_research_yields_no_approval_card(self) -> None:
        sess = _session()
        outcome = _run(
            enqueue_github_work_approval(
                session=sess,
                request_text="DevOps 책 추천해줘",
                approval_worker=self.approval_worker,
                source_message_id=10,
            )
        )
        self.assertIsNone(outcome.proposal)
        self.assertEqual(outcome.skipped_reason, SKIPPED_NO_CODING_INTENT)
        self.assertEqual(self.posted, [])
        self.assertEqual(self._github_work_order_rows("sess-g4"), [])

    def test_clear_coding_request_posts_one_approval_card(self) -> None:
        sess = _session()
        outcome = _run(
            enqueue_github_work_approval(
                session=sess,
                request_text="이 버그 고쳐서 PR 올려줘",
                approval_worker=self.approval_worker,
                source_channel_id=1,
                source_thread_id=2,
                source_message_id=42,
                requested_by="masterway",
            )
        )
        self.assertIsNotNone(outcome.proposal)
        self.assertIsNone(outcome.skipped_reason)
        self.assertIsNotNone(outcome.approval_job_id)

        # Exactly one approval card was posted to #승인-대기.
        self.assertEqual(len(self.posted), 1)
        request, _rendered = self.posted[0]
        self.assertEqual(request.approval_kind, APPROVAL_KIND_ENGINEERING_WRITE)
        self.assertEqual(request.session_id, "sess-g4")
        self.assertIn("github_work_order_proposal", request.extra)
        # Critically: NO github_work_order row exists yet.
        self.assertEqual(self._github_work_order_rows("sess-g4"), [])

    def test_github_actions_request_posts_devops_card(self) -> None:
        sess = _session(active_research_roles=("tech-lead", "devops-engineer"))
        outcome = _run(
            enqueue_github_work_approval(
                session=sess,
                request_text="GitHub Actions workflow 고쳐줘",
                approval_worker=self.approval_worker,
                source_message_id=99,
            )
        )
        self.assertIsNotNone(outcome.proposal)
        self.assertIn("devops-engineer", outcome.proposal.selected_roles)
        self.assertEqual(len(self.posted), 1)

    def test_duplicate_request_returns_skipped_duplicate(self) -> None:
        sess = _session()
        kw = dict(
            session=sess,
            request_text="이 버그 고쳐서 PR 올려줘",
            approval_worker=self.approval_worker,
            source_message_id=100,
        )
        first = _run(enqueue_github_work_approval(**kw))
        second = _run(enqueue_github_work_approval(**kw))
        self.assertIsNone(first.skipped_reason)
        self.assertEqual(second.skipped_reason, SKIPPED_DUPLICATE_APPROVAL)
        # Approval card was posted exactly once.
        self.assertEqual(len(self.posted), 1)

    def test_excluded_role_never_appears_in_card_extras(self) -> None:
        sess = _session(
            active_research_roles=(
                "tech-lead",
                "frontend-engineer",
                "backend-engineer",
            ),
            excluded_research_roles=("frontend-engineer",),
        )
        outcome = _run(
            enqueue_github_work_approval(
                session=sess,
                request_text="이 버그 고쳐서 PR 올려줘",
                approval_worker=self.approval_worker,
                source_message_id=200,
            )
        )
        self.assertIsNotNone(outcome.proposal)
        self.assertNotIn("frontend-engineer", outcome.proposal.selected_roles)
        request, _ = self.posted[0]
        # The card's extras must not surface the excluded role either.
        self.assertNotIn("frontend-engineer", request.extra["selected_roles"])

    def test_obsidian_request_is_not_routed_to_github(self) -> None:
        sess = _session()
        outcome = _run(
            enqueue_github_work_approval(
                session=sess,
                request_text="Obsidian에 정리해줘",
                approval_worker=self.approval_worker,
                source_message_id=33,
            )
        )
        self.assertIsNone(outcome.proposal)
        self.assertEqual(outcome.skipped_reason, SKIPPED_OBSIDIAN_INTENT)
        self.assertEqual(self.posted, [])


# ---------------------------------------------------------------------------
# Post-approval dispatch
# ---------------------------------------------------------------------------


class HandleApprovalReplyTests(_AdapterFixture):
    def _create_approval(self, *, source_message_id: int = 42):
        sess = _session()
        outcome = _run(
            enqueue_github_work_approval(
                session=sess,
                request_text="이 버그 고쳐서 PR 올려줘",
                approval_worker=self.approval_worker,
                source_channel_id=1,
                source_thread_id=2,
                source_message_id=source_message_id,
                requested_by="masterway",
            )
        )
        request, _ = self.posted[0]
        return outcome, request

    def test_approval_reply_enqueues_dry_run_work_order(self) -> None:
        outcome, request = self._create_approval(source_message_id=42)
        reply = handle_github_work_approval_reply(
            queue=self.queue,
            approval_request=request,
            approval_id=outcome.approval_job_id,
            approved_by="masterway",
        )
        self.assertIsNotNone(reply.work_order)
        self.assertIsNone(reply.skipped_reason)
        self.assertEqual(reply.dispatched_job_id is not None, True)
        # GitHub work order row landed in the queue.
        rows = self._github_work_order_rows("sess-g4")
        self.assertEqual(len(rows), 1)
        payload = rows[0].payload
        # Approval triple stamped + dry-run on by default.
        self.assertEqual(payload["approval_id"], outcome.approval_job_id)
        self.assertEqual(payload["approved_by"], "masterway")
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["session_id"], "sess-g4")
        self.assertEqual(payload["proposal_id"], outcome.proposal.proposal_id)

    def test_approval_reply_dedup_on_second_call(self) -> None:
        outcome, request = self._create_approval(source_message_id=99)
        first = handle_github_work_approval_reply(
            queue=self.queue,
            approval_request=request,
            approval_id=outcome.approval_job_id,
            approved_by="masterway",
        )
        second = handle_github_work_approval_reply(
            queue=self.queue,
            approval_request=request,
            approval_id=outcome.approval_job_id,
            approved_by="masterway",
        )
        self.assertIsNone(first.skipped_reason)
        self.assertEqual(second.skipped_reason, "duplicate_in_flight")
        rows = self._github_work_order_rows("sess-g4")
        self.assertEqual(len(rows), 1)

    def test_obsidian_approval_kind_is_not_routed_to_github(self) -> None:
        # Defensive — if some other surface re-uses our reply handler
        # against an obsidian approval, the helper must refuse.
        approval = ApprovalRequest(
            session_id="sess-x",
            approval_kind=APPROVAL_KIND_OBSIDIAN_WRITE,
            title="Obsidian 저장",
            summary="x",
            requested_action="vault write",
            created_by="op",
            extra={},
        )
        reply = handle_github_work_approval_reply(
            queue=self.queue,
            approval_request=approval,
            approval_id="apv-obs",
            approved_by="op",
        )
        self.assertIsNone(reply.work_order)
        self.assertEqual(reply.skipped_reason, "approval_kind_mismatch")
        self.assertEqual(self._github_work_order_rows("sess-x"), [])

    def test_engineering_approval_without_proposal_payload_skips(self) -> None:
        approval = ApprovalRequest(
            session_id="sess-x",
            approval_kind=APPROVAL_KIND_ENGINEERING_WRITE,
            title="manual write",
            summary="x",
            requested_action="x",
            created_by="op",
            extra={},  # no github_work_order_proposal
        )
        reply = handle_github_work_approval_reply(
            queue=self.queue,
            approval_request=approval,
            approval_id="apv",
            approved_by="op",
        )
        self.assertIsNone(reply.work_order)
        self.assertEqual(reply.skipped_reason, "proposal_payload_missing")


# ---------------------------------------------------------------------------
# Existing Obsidian flow guard — adapter must not interfere
# ---------------------------------------------------------------------------


class ObsidianFlowUntouchedTests(_AdapterFixture):
    def test_obsidian_save_request_does_not_post_engineering_card(self) -> None:
        sess = _session()
        outcome = _run(
            enqueue_github_work_approval(
                session=sess,
                request_text="이 세션 기준으로 Obsidian에 정리해줘",
                approval_worker=self.approval_worker,
                source_message_id=11,
            )
        )
        self.assertEqual(outcome.skipped_reason, SKIPPED_OBSIDIAN_INTENT)
        self.assertEqual(self.posted, [])
        # No queue artifacts at all from this adapter for an Obsidian
        # request — the existing M10 flow keeps owning that surface.
        self.assertEqual(
            list(self.queue.list_for_session("sess-g4")),
            [],
        )


if __name__ == "__main__":
    unittest.main()
