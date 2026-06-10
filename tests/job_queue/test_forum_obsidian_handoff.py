"""forum_obsidian_handoff — A-M7.5 producer tests.

Pin the missing producer wiring: a forum-thread message saying
"Obsidian 에 정리하고 싶어" must enqueue a #승인-대기 card via
:class:`ApprovalWorker`. Without this producer the user complaint
"thread 에서 저장 요청해도 카드 안 생김" stayed open until A-M7.5.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import List

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.approval_worker import (
    APPROVAL_KIND_OBSIDIAN_WRITE,
    ApprovalWorker,
)
from yule_engineering.agents.job_queue.forum_obsidian_handoff import (
    RESPONSE_APPROVAL_CHANNEL_UNSET,
    RESPONSE_APPROVAL_DUPLICATE,
    RESPONSE_APPROVAL_QUEUED,
    RESPONSE_NO_SESSION_FOR_THREAD,
    SKIPPED_APPROVAL_CHANNEL_UNSET,
    SKIPPED_DUPLICATE_APPROVAL,
    SKIPPED_NO_SESSION_FOR_THREAD,
    SKIPPED_NOT_SAVE_REQUEST,
    render_handoff_response,
    route_forum_obsidian_save_request,
)
from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.store import JobQueue


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _forum_thread_message(
    *,
    channel_id: int = 50001,
    parent_channel_id: int = 50000,
    channel_name: str = "k8s 운영 자료",
    content: str = "Obsidian에 정리하고 싶어",
    author_name: str = "masterway",
    author_id: int = 7,
    message_id: int = 60001,
    guild_id: int = 40000,
):
    channel = SimpleNamespace(
        id=channel_id,
        parent_id=parent_channel_id,
        parent=SimpleNamespace(id=parent_channel_id, name="운영-리서치"),
        name=channel_name,
        guild=SimpleNamespace(id=guild_id),
    )
    author = SimpleNamespace(id=author_id, name=author_name, global_name=author_name)
    return SimpleNamespace(
        id=message_id,
        channel=channel,
        author=author,
        content=content,
        guild=SimpleNamespace(id=guild_id),
        jump_url=f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}",
    )


def _open_session(
    *,
    session_id: str = "sess-forum-handoff-1",
    forum_thread_id: int = 50001,
    prompt: str = "k8s 운영 자료 정리",
    extra_overrides=None,
):
    extra = {"research_forum_thread_id": forum_thread_id}
    if extra_overrides:
        extra.update(extra_overrides)
    when = datetime.now(tz=timezone.utc)
    return SimpleNamespace(
        session_id=session_id,
        prompt=prompt,
        thread_id=None,
        extra=extra,
        updated_at=when.isoformat(),
        role_sequence=("tech-lead", "devops-engineer"),
    )


class _HandoffFixture(unittest.TestCase):
    APPROVAL_CHANNEL_ID: int = 80001

    def setUp(self) -> None:  # noqa: D401
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=self._db)
        self.heartbeats = HeartbeatStore(db_path=self._db)

        self.posted_cards: List = []

        async def post_fn(request, rendered_text):
            self.posted_cards.append((request, rendered_text))
            return {
                "posted_message_id": 90000 + len(self.posted_cards),
                "channel_id": self.APPROVAL_CHANNEL_ID,
            }

        self.approval_worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=post_fn,
            channel_resolver=lambda: self.APPROVAL_CHANNEL_ID,
        )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class HappyPathTests(_HandoffFixture):
    def test_forum_save_request_enqueues_approval_card(self) -> None:
        session = _open_session()
        message = _forum_thread_message()
        outcome = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [session],
            )
        )
        self.assertTrue(outcome.handled)
        self.assertIsNotNone(outcome.approval_job_id)
        self.assertIsNone(outcome.skipped_reason)
        # ApprovalWorker actually posted the card via the stub.
        self.assertEqual(len(self.posted_cards), 1)
        request, rendered = self.posted_cards[0]
        self.assertEqual(request.session_id, session.session_id)
        self.assertEqual(request.approval_kind, APPROVAL_KIND_OBSIDIAN_WRITE)
        # Source thread metadata flows through.
        self.assertEqual(request.source_thread_id, message.channel.id)
        self.assertEqual(request.source_message_id, message.id)
        self.assertEqual(request.extra["origin"], "research_forum_save_request")
        self.assertIn("source_thread_url", request.extra)
        self.assertEqual(request.extra["source_thread_title"], message.channel.name)

    def test_response_template_contains_job_id(self) -> None:
        session = _open_session()
        message = _forum_thread_message()
        outcome = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [session],
            )
        )
        text = render_handoff_response(outcome)
        self.assertIsNotNone(text)
        self.assertIn(outcome.approval_job_id, text)
        self.assertIn("Obsidian 저장 요청", text)


# ---------------------------------------------------------------------------
# Idempotency — same forum-thread message must NOT enqueue twice
# ---------------------------------------------------------------------------


class IdempotencyTests(_HandoffFixture):
    def test_same_message_twice_yields_duplicate_outcome(self) -> None:
        # A-M7.6 — topic-level dedup fires before message-level
        # dedup. Same forum message hitting again finds the topic
        # already pending approval and returns the new
        # SKIPPED_TOPIC_PENDING_APPROVAL outcome (which carries the
        # existing approval job id for navigation). M7.5 message-id
        # dedup remains as a final safety net for sessions where
        # ledger persistence didn't take.
        from yule_engineering.agents.job_queue.forum_obsidian_handoff import (
            SKIPPED_TOPIC_PENDING_APPROVAL,
        )

        session = _open_session()
        message = _forum_thread_message()
        first = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [session],
            )
        )
        second = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [session],
            )
        )
        self.assertIsNone(first.skipped_reason)
        # Either dedup signal acceptable — both prove no double-post.
        self.assertIn(
            second.skipped_reason,
            {SKIPPED_TOPIC_PENDING_APPROVAL, SKIPPED_DUPLICATE_APPROVAL},
        )
        # Card was posted exactly once regardless of which dedup fired.
        self.assertEqual(len(self.posted_cards), 1)


# ---------------------------------------------------------------------------
# Negative paths — not a save request, not a forum thread, no session
# ---------------------------------------------------------------------------


class NotSaveRequestTests(_HandoffFixture):
    def test_unrelated_message_passes_through(self) -> None:
        message = _forum_thread_message(content="이 자료 어떻게 봐?")
        outcome = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [_open_session()],
            )
        )
        self.assertFalse(outcome.handled)
        self.assertEqual(outcome.skipped_reason, SKIPPED_NOT_SAVE_REQUEST)
        self.assertEqual(self.posted_cards, [])

    def test_save_request_in_regular_channel_passes_through(self) -> None:
        # parent_id None → not a forum thread; producer must not run.
        channel = SimpleNamespace(id=42, parent_id=None, parent=None, name="general")
        author = SimpleNamespace(id=1, name="m", global_name="m")
        message = SimpleNamespace(
            id=99, channel=channel, author=author, content="Obsidian에 정리해줘"
        )
        outcome = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [_open_session()],
            )
        )
        self.assertFalse(outcome.handled)
        self.assertEqual(self.posted_cards, [])


class NoSessionForThreadTests(_HandoffFixture):
    def test_friendly_no_op_when_session_lookup_fails(self) -> None:
        # Forum thread msg lands but no session has matching
        # research_forum_thread_id → friendly "couldn't find session".
        message = _forum_thread_message(channel_id=99999)
        outcome = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [_open_session(forum_thread_id=11)],
            )
        )
        self.assertTrue(outcome.handled)
        self.assertEqual(
            outcome.skipped_reason, SKIPPED_NO_SESSION_FOR_THREAD
        )
        self.assertEqual(
            render_handoff_response(outcome), RESPONSE_NO_SESSION_FOR_THREAD
        )
        self.assertEqual(self.posted_cards, [])


# ---------------------------------------------------------------------------
# Approval channel unset — graceful surface
# ---------------------------------------------------------------------------


class ApprovalChannelUnsetTests(_HandoffFixture):
    def test_channel_unset_routes_to_friendly_warning(self) -> None:
        # Override the approval worker so its channel resolver returns None.
        async def _post_fn(_request, _rendered):
            return {"posted_message_id": 1}

        worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=_post_fn,
            channel_resolver=lambda: None,
        )
        session = _open_session()
        message = _forum_thread_message()
        outcome = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=worker,
                session_lister=lambda **_: [session],
            )
        )
        self.assertTrue(outcome.handled)
        self.assertEqual(
            outcome.skipped_reason, SKIPPED_APPROVAL_CHANNEL_UNSET
        )
        self.assertEqual(
            render_handoff_response(outcome),
            RESPONSE_APPROVAL_CHANNEL_UNSET,
        )


# ---------------------------------------------------------------------------
# Token-leak guard — error rendering must never echo the token
# ---------------------------------------------------------------------------


class TokenLeakGuardTests(_HandoffFixture):
    def test_worker_exception_message_does_not_leak_secrets(self) -> None:
        async def boom_post(_request, _rendered):
            # Intentional sentinel error string that includes a fake
            # token-shaped payload — the producer must NOT propagate
            # that into the response template.
            raise RuntimeError("status=401 token=secret-do-not-leak")

        worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=boom_post,
            channel_resolver=lambda: 9999,
        )
        session = _open_session()
        message = _forum_thread_message()
        outcome = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=worker,
                session_lister=lambda **_: [session],
            )
        )
        # The handoff outcome carries the short error string but the
        # rendered user-facing response uses a generic template.
        self.assertTrue(outcome.handled)
        text = render_handoff_response(outcome) or ""
        self.assertNotIn("secret-do-not-leak", text)


# ---------------------------------------------------------------------------
# End-to-end: forum save → approval row → reply → obsidian write enqueue
# ---------------------------------------------------------------------------


class EndToEndApprovalToObsidianWriteTests(_HandoffFixture):
    def test_approve_reply_after_forum_handoff_enqueues_obsidian_write(
        self,
    ) -> None:
        from yule_engineering.agents.job_queue.approval_reply import (
            handle_approval_reply,
        )
        from yule_engineering.agents.job_queue.obsidian_writer_worker import (
            JOB_TYPE_OBSIDIAN_WRITE,
            ObsidianWriterWorker,
        )

        session = _open_session()
        message = _forum_thread_message()
        outcome = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [session],
            )
        )
        self.assertIsNone(outcome.skipped_reason)
        approval_job_id = outcome.approval_job_id
        self.assertIsNotNone(approval_job_id)

        # Now simulate the user replying "이대로 저장" in #승인-대기.
        # The existing M5a-2 handle_approval_reply must convert the
        # SAVED approval row into an obsidian_write row — proving the
        # producer integrates with the existing closed pipeline.
        obsidian_worker = ObsidianWriterWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            render_fn=lambda _r: SimpleNamespace(title="t"),
            write_fn=lambda _n, _v, _r: None,
            vault_root_resolver=lambda _r: Path(self._tmp.name),
        )
        reply_outcome = handle_approval_reply(
            queue=self.queue,
            obsidian_worker=obsidian_worker,
            text="이대로 저장",
            session_id=session.session_id,
            approved_by="masterway",
            source_message_id=message.id,
            source_thread_id=message.channel.id,
        )
        self.assertEqual(reply_outcome.approval_job_id, approval_job_id)
        self.assertIsNotNone(reply_outcome.write_job_id)
        # An obsidian_write row landed in the queue → ready for the
        # ObsidianWriterWorker. Vault write happens AFTER user
        # approval — never before.
        write_rows = [
            j for j in self.queue.list_for_session(session.session_id)
            if j.job_type == JOB_TYPE_OBSIDIAN_WRITE
        ]
        self.assertEqual(len(write_rows), 1)
        payload = write_rows[0].payload or {}
        self.assertEqual(payload["approval_id"], approval_job_id)
        self.assertEqual(payload["approved_by"], "masterway")


# ---------------------------------------------------------------------------
# A-M10a — agent-ops audit recording
# ---------------------------------------------------------------------------


class AgentOpsAuditTests(_HandoffFixture):
    """Every handoff decision (queued / dedup / failure) records an
    :mod:`agents.lifecycle.agent_ops_log` entry on session.extra so
    the operator can reconstruct "왜 이 thread 에서 카드가 새로 안
    뜨고 dedup 됐지?" without scraping Discord.
    """

    def _read_audit(self, session) -> list:
        from yule_engineering.agents.lifecycle.agent_ops_log import (
            read_agent_ops_audit,
        )

        return list(read_agent_ops_audit(session))

    def test_approval_card_queued_records_l3_audit_entry(self) -> None:
        session = _open_session()
        message = _forum_thread_message()
        _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [session],
            )
        )
        rows = self._read_audit(session)
        self.assertGreaterEqual(len(rows), 1)
        # The L3 (knowledge_note_finalize) entry is the human-handoff
        # marker. Producer also stamps the topic_key from the ledger.
        levels = {row.autonomy_level for row in rows}
        self.assertIn("L3_HUMAN_APPROVAL", levels)
        finalize_rows = [
            r for r in rows if r.action == "knowledge_note_finalize"
        ]
        self.assertEqual(len(finalize_rows), 1)
        self.assertEqual(finalize_rows[0].outcome, "approval_card_queued")
        self.assertTrue(finalize_rows[0].topic_key)

    def test_topic_pending_dedup_records_l1_audit_entry(self) -> None:
        from yule_engineering.agents.job_queue.forum_obsidian_handoff import (
            SKIPPED_TOPIC_PENDING_APPROVAL,
        )

        session = _open_session()
        message = _forum_thread_message()
        _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [session],
            )
        )
        # Second pass — same thread, fresh message id; should hit
        # the topic-level dedup branch.
        message2 = _forum_thread_message(message_id=60002)
        second = _run(
            route_forum_obsidian_save_request(
                message=message2,
                text=message2.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [session],
            )
        )
        self.assertIn(
            second.skipped_reason,
            {SKIPPED_TOPIC_PENDING_APPROVAL, SKIPPED_DUPLICATE_APPROVAL},
        )
        rows = self._read_audit(session)
        # At least one L1 forum_handoff_decision entry alongside the
        # original L3 approval entry.
        l1_rows = [
            r
            for r in rows
            if r.autonomy_level == "L1_AUTO_RECORD_REQUIRED"
            and r.action == "forum_handoff_decision"
        ]
        self.assertGreaterEqual(len(l1_rows), 1)
        # Outcome string carries the dedup reason for grep-ability.
        outcomes = " | ".join(r.outcome for r in l1_rows)
        self.assertTrue(
            "topic_pending" in outcomes
            or "topic_obsidian_in_flight" in outcomes
            or "duplicate_approval" in outcomes
        )

    def test_approval_channel_unset_records_failure_entry(self) -> None:
        from yule_engineering.agents.job_queue.approval_worker import (
            ApprovalWorker as Worker,
        )

        async def post_fn(_request, _rendered):
            return {"posted_message_id": 1, "channel_id": 0}

        # channel_resolver returning None forces approval_channel_unset.
        worker = Worker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=post_fn,
            channel_resolver=lambda: None,
        )
        session = _open_session()
        message = _forum_thread_message()
        outcome = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=worker,
                session_lister=lambda **_: [session],
            )
        )
        self.assertEqual(
            outcome.skipped_reason, SKIPPED_APPROVAL_CHANNEL_UNSET
        )
        rows = self._read_audit(session)
        l1_rows = [
            r
            for r in rows
            if r.autonomy_level == "L1_AUTO_RECORD_REQUIRED"
        ]
        self.assertGreaterEqual(len(l1_rows), 1)
        # Failure outcome carries the diagnostic string.
        self.assertTrue(
            any("approval_channel_unset" in r.outcome for r in l1_rows)
        )


# ---------------------------------------------------------------------------
# A-M10c — research-log auto-save alongside approval card
# ---------------------------------------------------------------------------


class ResearchLogAutoSaveTests(_HandoffFixture):
    """The forum-handoff producer enqueues an L1 research-log
    obsidian_write alongside the L3 approval card so the user sees
    the research synthesis state captured in the vault even before
    they finish reviewing the canonical knowledge note.
    """

    def _writer(self):
        from yule_engineering.agents.job_queue.obsidian_writer_worker import (
            ObsidianWriterWorker,
        )

        # Stub render / write / vault — research-log enqueue path
        # only exercises ``enqueue`` here, not the consumer path.
        return ObsidianWriterWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            render_fn=lambda req: ("dummy", "dummy"),
            write_fn=lambda *a, **k: None,
            vault_root_resolver=lambda req: None,
        )

    def test_approval_card_triggers_research_log_enqueue(self) -> None:
        from yule_engineering.agents.job_queue.obsidian_writer_worker import (
            JOB_TYPE_OBSIDIAN_WRITE,
            NOTE_KIND_RESEARCH_LOG,
        )

        session = _open_session()
        message = _forum_thread_message()
        outcome = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [session],
                obsidian_writer_worker=self._writer(),
            )
        )
        self.assertTrue(outcome.handled)
        self.assertIsNotNone(outcome.approval_job_id)
        # Approval card was queued AND a research-log write row landed.
        write_rows = [
            j
            for j in self.queue.list_for_session(session.session_id)
            if j.job_type == JOB_TYPE_OBSIDIAN_WRITE
        ]
        self.assertEqual(len(write_rows), 1)
        payload = write_rows[0].payload or {}
        self.assertEqual(payload.get("note_kind"), NOTE_KIND_RESEARCH_LOG)
        # research-log payload carries hydration so the writer can
        # render without a session lookup.
        metadata = payload.get("metadata") or {}
        self.assertIn("thread_snapshot", metadata)
        self.assertEqual(metadata.get("autonomy_level"), "L1_AUTO_RECORD_REQUIRED")
        # Audit row records the L1 research-log decision.
        from yule_engineering.agents.lifecycle.agent_ops_log import (
            read_agent_ops_audit,
        )

        rows = read_agent_ops_audit(session)
        actions = {r.action for r in rows}
        self.assertIn("research_log_save", actions)
        self.assertTrue(
            any(
                r.action == "research_log_save"
                and r.outcome.startswith("research_log_enqueued")
                for r in rows
            )
        )

    def test_no_writer_means_no_research_log_enqueue(self) -> None:
        # Production-shaped tests that don't pass the writer must
        # not silently swallow the approval card. The L3 approval
        # path is unaffected.
        from yule_engineering.agents.job_queue.obsidian_writer_worker import (
            JOB_TYPE_OBSIDIAN_WRITE,
        )

        session = _open_session()
        message = _forum_thread_message()
        outcome = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [session],
            )
        )
        self.assertIsNotNone(outcome.approval_job_id)
        write_rows = [
            j
            for j in self.queue.list_for_session(session.session_id)
            if j.job_type == JOB_TYPE_OBSIDIAN_WRITE
        ]
        self.assertEqual(write_rows, [])


# ---------------------------------------------------------------------------
# A-M10b — extracted_links + supersedes_revision hydration markers
# ---------------------------------------------------------------------------


class HydrationMarkerTests(_HandoffFixture):
    """Pin two M10b hydration contracts the source-side edits introduce:

      * The forum producer lifts ``extracted_links`` to the top level
        of ``ApprovalRequest.extra`` so dedup / search / converters
        can grep without unpacking ``thread_snapshot``. The
        approval→write converter then carries the same key onto
        ``ObsidianWriteRequest.metadata``.
      * When the topic ledger has been bumped to a revision > 1
        (operator opted into "다시 저장"), the rendered knowledge note
        stamps ``ledger_revision`` + ``supersedes_revision`` onto its
        frontmatter so a vault scan can identify earlier hollow
        copies as superseded candidates without auto-deleting them.
    """

    def _stub_history(self):
        async def fetcher(_msg):
            return [
                SimpleNamespace(
                    id=70001,
                    content=(
                        "k8s rolling update 자료: "
                        "https://kubernetes.io/docs/concepts/workloads/"
                    ),
                    author=SimpleNamespace(
                        id=7,
                        name="masterway",
                        global_name="masterway",
                        bot=False,
                    ),
                    created_at=None,
                ),
            ]

        return fetcher

    def test_extracted_links_lifted_onto_approval_extra(self) -> None:
        # Producer must surface extracted_links AT the top of extra,
        # not only inside thread_snapshot. Downstream readers that
        # don't know the snapshot shape can grep the URL list directly.
        session = _open_session()
        message = _forum_thread_message()
        outcome = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [session],
                thread_history_fetcher=self._stub_history(),
            )
        )
        self.assertIsNone(outcome.skipped_reason)
        request, _rendered = self.posted_cards[0]
        self.assertIn(
            "https://kubernetes.io/docs/concepts/workloads/",
            list(request.extra.get("extracted_links") or ()),
        )
        snapshot = request.extra.get("thread_snapshot") or {}
        self.assertIn(
            "https://kubernetes.io/docs/concepts/workloads/",
            list(snapshot.get("extracted_links") or ()),
        )

    def test_extracted_links_round_trip_into_write_request_metadata(
        self,
    ) -> None:
        # extra → ObsidianWriteRequest.metadata via
        # approval_to_obsidian_write_request. M10b regression guard
        # for the converter's key-preservation loop.
        from yule_engineering.agents.job_queue.approval_reply import (
            approval_to_obsidian_write_request,
        )
        from yule_engineering.agents.job_queue.approval_worker import (
            ApprovalRequest,
        )

        approval = ApprovalRequest(
            session_id="sess-extracted",
            approval_kind=APPROVAL_KIND_OBSIDIAN_WRITE,
            title="links",
            summary="-",
            requested_action="vault 저장",
            created_by="masterway",
            extra={
                "extracted_links": [
                    "https://kubernetes.io/docs/concepts/workloads/",
                ],
                "thread_snapshot": {
                    "messages": [],
                    "extracted_links": [
                        "https://kubernetes.io/docs/concepts/workloads/",
                    ],
                    "role_summaries": {},
                },
            },
        )
        write_request = approval_to_obsidian_write_request(
            approval_request=approval,
            approval_id="apv-1",
            approved_by="masterway",
        )
        self.assertIn(
            "https://kubernetes.io/docs/concepts/workloads/",
            list(write_request.metadata.get("extracted_links") or ()),
        )

    def test_supersedes_revision_frontmatter_on_revision_bump(self) -> None:
        # When ledger_revision > 1, the rendered knowledge note
        # carries both the current revision and the prior one it
        # supersedes — anchors the "don't auto-delete; mark superseded
        # candidate" contract from the M10b spec.
        import os
        import tempfile
        from unittest import mock

        from yule_engineering.agents.job_queue.approval_reply import (
            approval_to_obsidian_write_request,
        )
        from yule_engineering.agents.job_queue.approval_worker import (
            ApprovalRequest,
        )
        from yule_engineering.agents.job_queue.obsidian_writer_worker import (
            default_render_fn,
        )
        from yule_engineering.agents.workflow_state import (
            WorkflowSession,
            WorkflowState,
            save_session,
        )

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(
            os.environ,
            {
                "YULE_CACHE_DB_PATH": str(Path(tmp.name) / "cache.sqlite3"),
                "YULE_REPO_ROOT": tmp.name,
                "OBSIDIAN_VAULT_PATH": str(Path(tmp.name) / "vault"),
            },
        )
        env.start()
        self.addCleanup(env.stop)

        sid = "sess-revision-bump"
        when = datetime.now(tz=timezone.utc)
        save_session(
            WorkflowSession(
                session_id=sid,
                prompt="개정본 정리",
                task_type="research",
                state=WorkflowState.IN_PROGRESS,
                created_at=when,
                updated_at=when,
                role_sequence=("tech-lead",),
                extra={"research_forum_thread_id": 50001},
            )
        )

        approval = ApprovalRequest(
            session_id=sid,
            approval_kind=APPROVAL_KIND_OBSIDIAN_WRITE,
            title="개정본",
            summary="-",
            requested_action="vault 저장",
            created_by="masterway",
            source_thread_id=50001,
            extra={
                "ledger_revision": 2,
                "topic_key": "k8s-operations-12345",
                "thread_snapshot": {
                    "messages": [
                        {
                            "author": "masterway",
                            "content": "개정본 메모.",
                            "role": None,
                            "posted_at": None,
                        }
                    ],
                    "extracted_links": [],
                    "role_summaries": {},
                },
            },
        )
        write_request = approval_to_obsidian_write_request(
            approval_request=approval,
            approval_id="apv-rev-2",
            approved_by="masterway",
            approved_at=when.replace(microsecond=0).isoformat(),
        )
        note = default_render_fn(write_request)
        self.assertEqual(note.frontmatter.get("ledger_revision"), 2)
        self.assertEqual(note.frontmatter.get("supersedes_revision"), 1)

    def test_writer_summary_records_superseded_candidate_path(self) -> None:
        # When the writer auto-suffixed (a previous note already sat
        # at the recommended target path), the saved row's result
        # summary surfaces ``superseded_candidate_path`` so the
        # operator can decide whether to retire the prior file. The
        # agent never deletes; it only marks the candidate.
        from yule_engineering.agents.job_queue.obsidian_writer_worker import (
            NOTE_KIND_KNOWLEDGE,
            ObsidianWriteRequest,
            ObsidianWriterWorker,
        )

        worker = ObsidianWriterWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            render_fn=lambda _r: SimpleNamespace(title="t"),
            write_fn=lambda *a, **k: None,
            vault_root_resolver=lambda _r: Path(self._tmp.name),
        )
        request = ObsidianWriteRequest(
            session_id="sess-marker",
            note_kind=NOTE_KIND_KNOWLEDGE,
            title="결정 노트",
            approval_id="apv-1",
            approved_by="masterway",
            approved_at="2026-05-08T10:00:00+00:00",
        )
        original_path = Path(self._tmp.name) / "knowledge" / "결정 노트.md"
        new_path = Path(self._tmp.name) / "knowledge" / "결정 노트_2.md"
        write_result = SimpleNamespace(
            target_path=new_path,
            original_target_path=original_path,
            written=True,
            dry_run=False,
            suffix_applied=True,
        )
        summary = worker._summarize_write_result(  # noqa: SLF001 - test surface
            request=request,
            write_result=write_result,
            vault_root=Path(self._tmp.name),
        )
        self.assertTrue(summary["suffix_applied"])
        self.assertEqual(
            summary["superseded_candidate_path"], str(original_path)
        )
        self.assertEqual(summary["target_path"], str(new_path))


if __name__ == "__main__":
    unittest.main()
