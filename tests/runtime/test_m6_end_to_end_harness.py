"""A-M6 end-to-end harness regression.

Drives the closed engineering production path through the queue
without a live Discord client:

  사용자 인테이크 →
    ResearchWorker.run_one (research_collect) →
    RoleTakeWorker × N (role_take open) →
    tech-lead synthesis (role_take synthesis) →
    ApprovalWorker.run_one (approval_post) →
    approval_reply_router → handle_approval_reply →
    ObsidianWriterWorker.process_job (obsidian_write) →
    write_fn 호출 + 큐 SAVED.

External boundaries (LLM provider, Discord network, vault filesystem)
are injected as stubs so the harness exercises the queue + state
machine + adapter wiring exactly as production does. The point is
to catch contract drift between A-M3..A-M5b workers and the
A-M6.1a..b-2 entrypoints; not to re-test what the per-worker unit
suites already cover.

Layout:

  * Happy-path checkpoints (10) — one driver test per stage so a
    failure pinpoints which seam broke.
  * Regression cases (11) — duplicate approve, reject, channel
    mismatch, no match, post failure, missing approval metadata,
    heartbeats, state transitions, failed_retryable resilience,
    ApprovalWorker / ObsidianWriterWorker dedup.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.approval_reply import (
    ApprovalIntent,
    handle_approval_reply,
)
from yule_engineering.agents.job_queue.approval_worker import (
    APPROVAL_KIND_OBSIDIAN_WRITE,
    JOB_TYPE_APPROVAL_POST,
    SERVICE_ID_APPROVAL_WORKER,
    SKIPPED_APPROVAL_CHANNEL_UNSET,
    SKIPPED_DUPLICATE as APPROVAL_SKIPPED_DUPLICATE,
    ApprovalRequest,
    ApprovalWorker,
)
from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.obsidian_writer_worker import (
    JOB_TYPE_OBSIDIAN_WRITE,
    NOTE_KIND_KNOWLEDGE,
    SERVICE_ID_OBSIDIAN_WRITER,
    SKIPPED_APPROVAL_REQUIRED,
    SKIPPED_DUPLICATE as OBSIDIAN_SKIPPED_DUPLICATE,
    ObsidianWriteRequest,
    ObsidianWriterWorker,
)
from yule_engineering.agents.job_queue.research_worker import (
    JOB_TYPE_RESEARCH_COLLECT,
    SERVICE_ID_RESEARCH_WORKER,
    ResearchWorker,
)
from yule_engineering.agents.job_queue.role_take_worker import (
    JOB_TYPE_ROLE_TAKE,
    KIND_OPEN,
    KIND_SYNTHESIS,
    KIND_TURN,
    RoleTakeWorker,
    service_id_for_role,
)
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Shared fixture — fresh queue + workers + injected stubs per test
# ---------------------------------------------------------------------------


class _HarnessFixture(unittest.TestCase):
    """Wire one engineering session worth of workers around a temp
    SQLite + temp vault. Per-test isolation so `add` / `pick` /
    transitions never see leakage between cases.
    """

    SESSION_ID: str = "sess-m6-e2e-1"
    APPROVAL_CHANNEL_ID: int = 80001
    SOURCE_THREAD_ID: int = 80002
    SOURCE_MESSAGE_ID: int = 80003
    ROLE_SEQUENCE: Tuple[str, ...] = (
        "tech-lead",
        "backend-engineer",
        "qa-engineer",
        "devops-engineer",
        "ai-engineer",
        "frontend-engineer",
        "product-designer",
    )

    def setUp(self) -> None:  # noqa: D401
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        self._vault = Path(self._tmp.name) / "vault"
        self._vault.mkdir()

        self.queue = JobQueue(db_path=self._db)
        self.heartbeats = HeartbeatStore(db_path=self._db)

        # ----- ResearchWorker stub runner -----------------------------------
        # The runner returns a frozen dict that the test asserts against;
        # the worker only needs *something* truthy to call it a success.
        self.research_runner_calls: List[Any] = []

        async def research_runner(job):
            self.research_runner_calls.append(job)
            # mimic what the real runner stashes onto session.extra
            return {
                "session_id": job.session_id,
                "research_pack": {"sources": [{"title": "src-1"}]},
                "checkpoints": ["collected"],
            }

        self.research_runner = research_runner
        self.research_worker = ResearchWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
        )

        # ----- RoleTakeWorker stub runner -----------------------------------
        self.role_take_runner_calls: List[Any] = []

        def role_take_runner(job):
            self.role_take_runner_calls.append(job)
            return {
                "role": job.role,
                "kind": (job.payload or {}).get("kind"),
                "message": f"{job.role} take",
            }

        self.role_take_runner = role_take_runner

        # ----- ApprovalWorker stub post_fn ----------------------------------
        self.posted_cards: List[Tuple[ApprovalRequest, str]] = []

        async def post_fn(request: ApprovalRequest, rendered: str):
            self.posted_cards.append((request, rendered))
            return {
                "posted_message_id": 91000 + len(self.posted_cards),
                "channel_id": self.APPROVAL_CHANNEL_ID,
            }

        self.post_fn = post_fn
        self.approval_worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=post_fn,
            channel_resolver=lambda: self.APPROVAL_CHANNEL_ID,
        )

        # ----- ObsidianWriterWorker stub render/write -----------------------
        self.rendered_notes: List[ObsidianWriteRequest] = []
        self.written_notes: List[Tuple[Any, Path, ObsidianWriteRequest]] = []

        def render_fn(request: ObsidianWriteRequest):
            self.rendered_notes.append(request)
            return SimpleNamespace(
                title=request.title,
                kind=request.note_kind,
                content=f"{request.title}\n\nrendered",
            )

        def write_fn(note: Any, vault_root: Path, request: ObsidianWriteRequest):
            self.written_notes.append((note, vault_root, request))
            return SimpleNamespace(
                target_path=vault_root / f"{request.title}.md",
                written=True,
                dry_run=False,
                suffix_applied=False,
            )

        self.render_fn = render_fn
        self.write_fn = write_fn
        self.obsidian_worker = ObsidianWriterWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            render_fn=render_fn,
            write_fn=write_fn,
            vault_root_resolver=lambda _r: self._vault,
        )

    # --- helpers --------------------------------------------------------

    def _drive_research(self) -> Any:
        return _run(
            self.research_worker.run_one(
                session_id=self.SESSION_ID,
                runner=self.research_runner,
            )
        )

    def _drive_role_takes(
        self, *, kind: str = KIND_OPEN
    ) -> List[Any]:
        outcomes: List[Any] = []
        for role in self.ROLE_SEQUENCE:
            worker = RoleTakeWorker(
                queue=self.queue,
                heartbeats=self.heartbeats,
                role_filter=role,
            )
            outcomes.append(
                worker.run_one(
                    session_id=self.SESSION_ID,
                    role=role,
                    kind=kind,
                    runner=self.role_take_runner,
                )
            )
        return outcomes

    def _seed_obsidian_approval(
        self,
        *,
        title: str = "결정 노트",
        source_message_id: Optional[int] = None,
        decision_id: str = "dec-e2e-1",
    ) -> str:
        request = ApprovalRequest(
            session_id=self.SESSION_ID,
            approval_kind=APPROVAL_KIND_OBSIDIAN_WRITE,
            title=title,
            summary="3 source 검토",
            requested_action="vault decisions 저장",
            created_by="tech-lead",
            source_channel_id=self.APPROVAL_CHANNEL_ID,
            source_thread_id=self.SOURCE_THREAD_ID,
            source_message_id=source_message_id or self.SOURCE_MESSAGE_ID,
            extra={"decision_id": decision_id, "policy_level": "L3_HUMAN_REQUIRED"},
        )
        outcome = _run(self.approval_worker.run_one(request))
        assert outcome.job is not None
        assert outcome.skipped_reason is None, outcome.skipped_reason
        return outcome.job.job_id


# ---------------------------------------------------------------------------
# Happy-path checkpoints — one stage per test class so a failure
# fingers the broken seam.
# ---------------------------------------------------------------------------


class HappyPathCheckpointsTests(_HarnessFixture):
    """All 10 stages of the production path, exercised in order."""

    def test_01_research_collect_runs_and_saves(self) -> None:
        outcome = self._drive_research()
        self.assertEqual(len(self.research_runner_calls), 1)
        self.assertIsNotNone(outcome.job)
        self.assertEqual(outcome.job.state, JobState.SAVED)
        self.assertEqual(outcome.job.job_type, JOB_TYPE_RESEARCH_COLLECT)
        # runner result available to the caller (router uses this to
        # build the EngineeringResearchLoopReport in production).
        self.assertEqual(
            outcome.runner_result["session_id"], self.SESSION_ID
        )

    def test_02_role_takes_fan_out_to_seven_roles(self) -> None:
        self._drive_research()
        outcomes = self._drive_role_takes(kind=KIND_OPEN)
        self.assertEqual(len(outcomes), len(self.ROLE_SEQUENCE))
        # Every role take landed SAVED.
        for outcome in outcomes:
            self.assertIsNotNone(outcome.job)
            self.assertEqual(outcome.job.state, JobState.SAVED)
            self.assertEqual(outcome.job.job_type, JOB_TYPE_ROLE_TAKE)
        # Runner saw each role exactly once with kind=open.
        roles_seen = {
            (j.role, (j.payload or {}).get("kind"))
            for j in self.role_take_runner_calls
        }
        self.assertEqual(
            roles_seen,
            {(role, KIND_OPEN) for role in self.ROLE_SEQUENCE},
        )

    def test_03_synthesis_role_take_can_run_alongside_open(self) -> None:
        # Open + synthesis for tech-lead are different rows by
        # (session, role, kind) — both should land SAVED without
        # the dedup guard mistaking one for the other.
        self._drive_research()
        self._drive_role_takes(kind=KIND_OPEN)
        worker = RoleTakeWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            role_filter="tech-lead",
        )
        synth = worker.run_one(
            session_id=self.SESSION_ID,
            role="tech-lead",
            kind=KIND_SYNTHESIS,
            runner=self.role_take_runner,
        )
        self.assertIsNotNone(synth.job)
        self.assertEqual(synth.job.state, JobState.SAVED)
        # Two tech-lead role_take rows exist now (open + synthesis).
        tl_rows = [
            j for j in self.queue.list_for_session(self.SESSION_ID)
            if j.job_type == JOB_TYPE_ROLE_TAKE and j.role == "tech-lead"
        ]
        self.assertEqual(len(tl_rows), 2)
        kinds = {(j.payload or {}).get("kind") for j in tl_rows}
        self.assertEqual(kinds, {KIND_OPEN, KIND_SYNTHESIS})

    def test_04_approval_post_card_is_broadcast(self) -> None:
        self._drive_research()
        self._drive_role_takes()
        approval_id = self._seed_obsidian_approval()
        self.assertEqual(len(self.posted_cards), 1)
        request, rendered = self.posted_cards[0]
        self.assertEqual(request.session_id, self.SESSION_ID)
        self.assertEqual(request.approval_kind, APPROVAL_KIND_OBSIDIAN_WRITE)
        # Render is the load-bearing markdown card the user reads.
        self.assertIn("승인 요청 — Obsidian 저장", rendered)
        self.assertIn(self.SESSION_ID, rendered)
        # Saved row carries the posted_message_id for cross-reference.
        approval_job = self.queue.get(approval_id)
        assert approval_job is not None
        self.assertEqual(approval_job.state, JobState.SAVED)
        self.assertIn("posted_message_id", approval_job.result)

    def test_05_approval_audit_metadata_round_trips(self) -> None:
        # decision_id + policy_level seeded via ApprovalRequest.extra
        # must survive the queue trip and remain in the saved row's
        # payload — production handoff to obsidian write relies on it.
        self._drive_research()
        self._drive_role_takes()
        approval_id = self._seed_obsidian_approval(
            decision_id="dec-audit-99"
        )
        approval_job = self.queue.get(approval_id)
        assert approval_job is not None
        extra = (approval_job.payload or {}).get("extra") or {}
        self.assertEqual(extra.get("decision_id"), "dec-audit-99")
        self.assertEqual(extra.get("policy_level"), "L3_HUMAN_REQUIRED")

    def test_06_approval_reply_enqueues_obsidian_write(self) -> None:
        self._drive_research()
        self._drive_role_takes()
        approval_id = self._seed_obsidian_approval()

        outcome = handle_approval_reply(
            queue=self.queue,
            obsidian_worker=self.obsidian_worker,
            text="이대로 저장",
            session_id=self.SESSION_ID,
            approved_by="masterway",
            source_message_id=self.SOURCE_MESSAGE_ID,
            source_thread_id=self.SOURCE_THREAD_ID,
            approved_at="2026-05-07T13:00:00+00:00",
        )
        self.assertEqual(outcome.intent, ApprovalIntent.APPROVE)
        self.assertEqual(outcome.approval_job_id, approval_id)
        self.assertIsNotNone(outcome.write_job_id)
        self.assertIsNone(outcome.skipped_reason)
        # Audit fields surface on the outcome so the gateway can log
        # without re-reading the queue.
        self.assertEqual(outcome.audit.get("approved_by"), "masterway")
        self.assertEqual(
            outcome.audit.get("approved_at"), "2026-05-07T13:00:00+00:00"
        )

    def test_07_obsidian_write_landed_in_queued_state_after_reply(self) -> None:
        self._drive_research()
        self._drive_role_takes()
        self._seed_obsidian_approval()

        handle_approval_reply(
            queue=self.queue,
            obsidian_worker=self.obsidian_worker,
            text="승인",
            session_id=self.SESSION_ID,
            approved_by="masterway",
            source_message_id=self.SOURCE_MESSAGE_ID,
            source_thread_id=self.SOURCE_THREAD_ID,
        )
        write_rows = [
            j for j in self.queue.list_for_session(
                self.SESSION_ID, states=[JobState.QUEUED]
            )
            if j.job_type == JOB_TYPE_OBSIDIAN_WRITE
        ]
        self.assertEqual(len(write_rows), 1)
        payload = write_rows[0].payload or {}
        # ApprovalGuard fields must all be populated so the worker
        # doesn't reject the row as missing-approval.
        self.assertTrue(payload.get("approval_id"))
        self.assertTrue(payload.get("approved_by"))
        self.assertTrue(payload.get("approved_at"))
        self.assertEqual(payload.get("note_kind"), NOTE_KIND_KNOWLEDGE)

    def test_08_obsidian_writer_processes_queued_write(self) -> None:
        self._drive_research()
        self._drive_role_takes()
        self._seed_obsidian_approval()
        handle_approval_reply(
            queue=self.queue,
            obsidian_worker=self.obsidian_worker,
            text="승인",
            session_id=self.SESSION_ID,
            approved_by="masterway",
            source_message_id=self.SOURCE_MESSAGE_ID,
            source_thread_id=self.SOURCE_THREAD_ID,
        )
        # Worker side — pick + process_job replays what the standalone
        # eng-obsidian-writer service does.
        picked = self.queue.pick(
            worker_id="harness-writer-1",
            job_types=[JOB_TYPE_OBSIDIAN_WRITE],
        )
        self.assertIsNotNone(picked)
        assert picked is not None
        outcome = _run(self.obsidian_worker.process_job(picked))
        self.assertIsNotNone(outcome.job)
        self.assertEqual(outcome.job.state, JobState.SAVED)
        self.assertIsNone(outcome.skipped_reason)

    def test_09_write_fn_received_full_request(self) -> None:
        self._drive_research()
        self._drive_role_takes()
        self._seed_obsidian_approval()
        handle_approval_reply(
            queue=self.queue,
            obsidian_worker=self.obsidian_worker,
            text="승인",
            session_id=self.SESSION_ID,
            approved_by="masterway",
            source_message_id=self.SOURCE_MESSAGE_ID,
            source_thread_id=self.SOURCE_THREAD_ID,
        )
        picked = self.queue.pick(
            worker_id="harness-writer-1",
            job_types=[JOB_TYPE_OBSIDIAN_WRITE],
        )
        assert picked is not None
        _run(self.obsidian_worker.process_job(picked))

        self.assertEqual(len(self.written_notes), 1)
        _note, vault_root, request = self.written_notes[0]
        self.assertEqual(request.session_id, self.SESSION_ID)
        self.assertEqual(request.note_kind, NOTE_KIND_KNOWLEDGE)
        # Approval triple stays attached on the request the writer sees.
        self.assertTrue(request.approval_id)
        self.assertEqual(request.approved_by, "masterway")
        self.assertTrue(request.approved_at)
        self.assertEqual(vault_root, self._vault)

    def test_10_full_pipeline_end_to_end_with_audit_chain(self) -> None:
        # One sweep: research → roles → approval → reply → write,
        # then assert the audit chain (approval_id appears in the
        # saved obsidian_write row) so a future change can't drop
        # the cross-reference.
        self._drive_research()
        self._drive_role_takes()
        approval_id = self._seed_obsidian_approval()

        outcome = handle_approval_reply(
            queue=self.queue,
            obsidian_worker=self.obsidian_worker,
            text="이대로 저장",
            session_id=self.SESSION_ID,
            approved_by="masterway",
            source_message_id=self.SOURCE_MESSAGE_ID,
            source_thread_id=self.SOURCE_THREAD_ID,
        )
        write_job_id = outcome.write_job_id
        self.assertIsNotNone(write_job_id)

        picked = self.queue.pick(
            worker_id="harness-writer-1",
            job_types=[JOB_TYPE_OBSIDIAN_WRITE],
        )
        assert picked is not None
        write_outcome = _run(self.obsidian_worker.process_job(picked))
        assert write_outcome.job is not None
        self.assertEqual(write_outcome.job.state, JobState.SAVED)

        # Audit — saved write row's payload must reference the
        # approval row id; result summary must record the title +
        # vault root the writer used.
        saved = self.queue.get(write_job_id)
        assert saved is not None
        self.assertEqual(
            (saved.payload or {}).get("approval_id"), approval_id
        )
        self.assertEqual(
            saved.result.get("title"), "결정 노트"
        )
        self.assertEqual(
            saved.result.get("vault_root"), str(self._vault)
        )


# ---------------------------------------------------------------------------
# Regression cases — the failure modes the supervisor must keep
# visible without the worker getting stuck.
# ---------------------------------------------------------------------------


class DuplicateApproveRegressionTests(_HarnessFixture):
    def test_second_approve_does_not_enqueue_second_write(self) -> None:
        self._seed_obsidian_approval()
        first = handle_approval_reply(
            queue=self.queue,
            obsidian_worker=self.obsidian_worker,
            text="승인",
            session_id=self.SESSION_ID,
            approved_by="masterway",
        )
        second = handle_approval_reply(
            queue=self.queue,
            obsidian_worker=self.obsidian_worker,
            text="승인",
            session_id=self.SESSION_ID,
            approved_by="masterway",
        )
        self.assertIsNone(first.skipped_reason)
        self.assertEqual(second.skipped_reason, "duplicate_obsidian_write")
        write_rows = [
            j for j in self.queue.list_for_session(self.SESSION_ID)
            if j.job_type == JOB_TYPE_OBSIDIAN_WRITE
        ]
        self.assertEqual(len(write_rows), 1)


class RejectRegressionTests(_HarnessFixture):
    def test_reject_records_audit_and_skips_write(self) -> None:
        self._seed_obsidian_approval(decision_id="dec-rej-1")
        captured: List[Dict[str, Any]] = []

        def fake_persist(**kwargs):
            captured.append(kwargs)

        outcome = handle_approval_reply(
            queue=self.queue,
            obsidian_worker=self.obsidian_worker,
            text="저장하지 마",
            session_id=self.SESSION_ID,
            approved_by="masterway",
            persist_rejection_fn=fake_persist,
            approved_at="2026-05-07T14:00:00+00:00",
        )
        self.assertEqual(outcome.intent, ApprovalIntent.REJECT)
        self.assertTrue(outcome.rejection_recorded)
        self.assertEqual(outcome.audit.get("decision_id"), "dec-rej-1")
        # No write enqueued on reject.
        write_rows = [
            j for j in self.queue.list_for_session(self.SESSION_ID)
            if j.job_type == JOB_TYPE_OBSIDIAN_WRITE
        ]
        self.assertEqual(write_rows, [])
        # Persist hook captured the rejection metadata.
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["rejected_by"], "masterway")
        self.assertEqual(captured[0]["reason"], "저장하지 마")


class ChannelMismatchRegressionTests(_HarnessFixture):
    def test_message_outside_approval_channel_does_not_route(self) -> None:
        # Importing here keeps the heavy adapter out of the module
        # load path for the queue-only tests above.
        from yule_engineering.discord.approval.reply_router import (
            route_approval_channel_message,
        )

        self._seed_obsidian_approval()
        sent: List[str] = []

        async def send_chunks(_channel, text, *args, **kwargs):
            sent.append(text)

        msg = SimpleNamespace(
            channel=SimpleNamespace(id=99999, name="random-channel"),
            author=SimpleNamespace(
                id=1, bot=False, name="masterway", global_name="masterway"
            ),
            content="승인",
            id=12345,
        )
        result = _run(
            route_approval_channel_message(
                message=msg,
                bot_user=SimpleNamespace(id=0),
                queue=self.queue,
                obsidian_worker=self.obsidian_worker,
                approval_channel_id=self.APPROVAL_CHANNEL_ID,
                send_chunks=send_chunks,
            )
        )
        self.assertFalse(result.handled)
        self.assertEqual(sent, [])
        # Queue is unchanged — no obsidian_write enqueued.
        write_rows = [
            j for j in self.queue.list_for_session(self.SESSION_ID)
            if j.job_type == JOB_TYPE_OBSIDIAN_WRITE
        ]
        self.assertEqual(write_rows, [])


class NoMatchingApprovalRegressionTests(_HarnessFixture):
    def test_approve_with_no_pending_card_returns_skipped(self) -> None:
        outcome = handle_approval_reply(
            queue=self.queue,
            obsidian_worker=self.obsidian_worker,
            text="승인",
            session_id="sess-no-card",
            approved_by="masterway",
        )
        self.assertEqual(outcome.intent, ApprovalIntent.APPROVE)
        self.assertEqual(outcome.skipped_reason, "no_matching_approval")
        self.assertIsNone(outcome.write_job_id)


class ApprovalPostFailureRegressionTests(_HarnessFixture):
    def test_post_fn_raises_marks_failed_retryable(self) -> None:
        # Replace the worker with one whose post_fn always raises so
        # we observe the FAILED_RETRYABLE transition + lease cleared.
        async def broken_post(_request, _rendered):
            raise RuntimeError("discord 5xx")

        worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=broken_post,
            channel_resolver=lambda: self.APPROVAL_CHANNEL_ID,
        )
        request = ApprovalRequest(
            session_id=self.SESSION_ID,
            approval_kind=APPROVAL_KIND_OBSIDIAN_WRITE,
            title="결정 노트",
            summary="",
            requested_action="",
            created_by="tech-lead",
        )
        with self.assertRaises(RuntimeError):
            _run(worker.run_one(request))
        rows = [
            j for j in self.queue.list_for_session(self.SESSION_ID)
            if j.job_type == JOB_TYPE_APPROVAL_POST
        ]
        self.assertEqual(len(rows), 1)
        # FAILED_RETRYABLE so the M2 reaper / a retry pass picks it up.
        self.assertEqual(rows[0].state, JobState.FAILED_RETRYABLE)
        # Lease cleared so the next worker can claim it.
        self.assertIsNone(rows[0].picked_by)
        # Error captured (one-line, error-type prefixed).
        self.assertIn("RuntimeError", rows[0].result.get("error", ""))


class ApprovalChannelUnsetRegressionTests(_HarnessFixture):
    def test_channel_unset_is_failed_retryable_with_constant(self) -> None:
        # Channel resolver returns None — production manifestation of
        # "DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID env unset". Worker
        # marks FAILED_RETRYABLE with the SKIPPED_APPROVAL_CHANNEL_UNSET
        # constant so an operator's diagnostic can match exact values.
        worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=self.post_fn,
            channel_resolver=lambda: None,
        )
        request = ApprovalRequest(
            session_id=self.SESSION_ID,
            approval_kind=APPROVAL_KIND_OBSIDIAN_WRITE,
            title="결정 노트",
            summary="",
            requested_action="",
            created_by="tech-lead",
        )
        outcome = _run(worker.run_one(request))
        self.assertEqual(
            outcome.skipped_reason, SKIPPED_APPROVAL_CHANNEL_UNSET
        )
        rows = [
            j for j in self.queue.list_for_session(self.SESSION_ID)
            if j.job_type == JOB_TYPE_APPROVAL_POST
        ]
        self.assertEqual(rows[0].state, JobState.FAILED_RETRYABLE)
        self.assertEqual(
            rows[0].result.get("error"), SKIPPED_APPROVAL_CHANNEL_UNSET
        )


class MissingApprovalMetadataRegressionTests(_HarnessFixture):
    def test_obsidian_write_without_approval_triple_is_blocked(self) -> None:
        # Producer error — knowledge note enqueued without an
        # approval triple. Worker MUST refuse with
        # SKIPPED_APPROVAL_REQUIRED instead of writing.
        request = ObsidianWriteRequest(
            session_id=self.SESSION_ID,
            note_kind=NOTE_KIND_KNOWLEDGE,
            title="우회 시도",
            approval_id=None,
            approved_by=None,
            approved_at=None,
        )
        outcome = _run(self.obsidian_worker.run_one(request))
        self.assertEqual(
            outcome.skipped_reason, SKIPPED_APPROVAL_REQUIRED
        )
        # write_fn NEVER called — the bypass guard worked.
        self.assertEqual(self.written_notes, [])
        rows = [
            j for j in self.queue.list_for_session(self.SESSION_ID)
            if j.job_type == JOB_TYPE_OBSIDIAN_WRITE
        ]
        self.assertEqual(rows[0].state, JobState.FAILED_RETRYABLE)
        self.assertEqual(
            rows[0].result.get("error"), SKIPPED_APPROVAL_REQUIRED
        )


class HeartbeatsRecordedRegressionTests(_HarnessFixture):
    def test_every_worker_emits_a_heartbeat_during_its_step(self) -> None:
        self._drive_research()
        self._drive_role_takes()
        self._seed_obsidian_approval()
        handle_approval_reply(
            queue=self.queue,
            obsidian_worker=self.obsidian_worker,
            text="승인",
            session_id=self.SESSION_ID,
            approved_by="masterway",
            source_thread_id=self.SOURCE_THREAD_ID,
            source_message_id=self.SOURCE_MESSAGE_ID,
        )
        picked = self.queue.pick(
            worker_id="harness-writer-1",
            job_types=[JOB_TYPE_OBSIDIAN_WRITE],
        )
        assert picked is not None
        _run(self.obsidian_worker.process_job(picked))

        recorded_ids = {
            record.service_id for record in self.heartbeats.list_all()
        }
        # Research + approval + obsidian writer always beat.
        self.assertIn(SERVICE_ID_RESEARCH_WORKER, recorded_ids)
        self.assertIn(SERVICE_ID_APPROVAL_WORKER, recorded_ids)
        self.assertIn(SERVICE_ID_OBSIDIAN_WRITER, recorded_ids)
        # Each role worker recorded under its scoped id.
        for role in self.ROLE_SEQUENCE:
            self.assertIn(service_id_for_role(role), recorded_ids)


class StateTransitionRegressionTests(_HarnessFixture):
    def test_research_collect_walks_queued_to_saved(self) -> None:
        # Drive research and verify the row never returns to QUEUED
        # (no requeue happens on a clean run).
        self._drive_research()
        rows = self.queue.list_for_session(
            self.SESSION_ID, states=[JobState.SAVED]
        )
        research_rows = [
            r for r in rows if r.job_type == JOB_TYPE_RESEARCH_COLLECT
        ]
        self.assertEqual(len(research_rows), 1)
        # No leftover QUEUED rows for the same session.
        leftover = [
            r for r in self.queue.list_for_session(self.SESSION_ID)
            if r.state in {
                JobState.QUEUED,
                JobState.ASSIGNED,
                JobState.IN_PROGRESS,
            }
            and r.job_type == JOB_TYPE_RESEARCH_COLLECT
        ]
        self.assertEqual(leftover, [])


class FailedRetryableResilienceTests(_HarnessFixture):
    def test_research_runner_failure_keeps_session_recoverable(self) -> None:
        # Runner raises once → row in FAILED_RETRYABLE with lease cleared.
        # `requeue_retryable` on the queue moves it back to QUEUED so
        # a fresh run_one (with a working runner) can complete.
        async def flaky_runner(_job):
            raise RuntimeError("LLM provider 5xx")

        with self.assertRaises(RuntimeError):
            _run(
                self.research_worker.run_one(
                    session_id=self.SESSION_ID,
                    runner=flaky_runner,
                )
            )
        rows = [
            r for r in self.queue.list_for_session(self.SESSION_ID)
            if r.job_type == JOB_TYPE_RESEARCH_COLLECT
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].state, JobState.FAILED_RETRYABLE)
        self.assertIsNone(rows[0].picked_by)

        # Operator (or supervisor) requeues with backoff. The row
        # comes back to QUEUED with attempt incremented.
        requeued = self.queue.requeue_retryable(
            rows[0].job_id, backoff_seconds=0.0
        )
        self.assertEqual(requeued.state, JobState.QUEUED)
        self.assertEqual(requeued.attempt, 1)


class ApprovalDedupRegressionTests(_HarnessFixture):
    def test_in_flight_duplicate_enqueue_returns_same_row(self) -> None:
        # Producer-side dedup: two enqueues for the same
        # (session, kind, source_message_id) before the worker drains
        # collapse to one row. Models the gateway seeing the same
        # intake message twice in quick succession.
        request = ApprovalRequest(
            session_id=self.SESSION_ID,
            approval_kind=APPROVAL_KIND_OBSIDIAN_WRITE,
            title="결정 노트",
            summary="",
            requested_action="",
            created_by="tech-lead",
            source_channel_id=self.APPROVAL_CHANNEL_ID,
            source_thread_id=self.SOURCE_THREAD_ID,
            source_message_id=self.SOURCE_MESSAGE_ID,
        )
        first_job, first_created = self.approval_worker.enqueue(request)
        second_job, second_created = self.approval_worker.enqueue(request)
        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first_job.job_id, second_job.job_id)
        # Still exactly one approval_post row.
        rows = [
            j for j in self.queue.list_for_session(self.SESSION_ID)
            if j.job_type == JOB_TYPE_APPROVAL_POST
        ]
        self.assertEqual(len(rows), 1)
        # No card was posted because nobody drained yet.
        self.assertEqual(self.posted_cards, [])


class ObsidianWriteDedupRegressionTests(_HarnessFixture):
    def test_in_flight_duplicate_enqueue_returns_existing_row(self) -> None:
        # Producer-side dedup: two enqueues for the same
        # (session, note_kind, source_thread_id, title) before the
        # writer drains collapse to one row. Models a duplicate
        # APPROVE reply hitting the router twice before the writer
        # picks the queued write up.
        request = ObsidianWriteRequest(
            session_id=self.SESSION_ID,
            note_kind=NOTE_KIND_KNOWLEDGE,
            title="결정 노트",
            source_thread_id=self.SOURCE_THREAD_ID,
            approval_id="apv-1",
            approved_by="masterway",
            approved_at="2026-05-07T13:00:00+00:00",
        )
        first_job, first_created = self.obsidian_worker.enqueue(request)
        second_job, second_created = self.obsidian_worker.enqueue(request)
        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first_job.job_id, second_job.job_id)
        # Exactly one obsidian_write row in the queue.
        rows = [
            j for j in self.queue.list_for_session(self.SESSION_ID)
            if j.job_type == JOB_TYPE_OBSIDIAN_WRITE
        ]
        self.assertEqual(len(rows), 1)
        # write_fn never called — nobody drained the queued row.
        self.assertEqual(self.written_notes, [])


if __name__ == "__main__":
    unittest.main()
