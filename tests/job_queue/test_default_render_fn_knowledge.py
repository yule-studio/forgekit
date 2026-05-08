"""default_render_fn knowledge support — A-M7.5e tests.

Pin the contract M7.5e closes:

  * note_kind=knowledge **without** the approval triple stays
    blocked at the worker's approval guard (M5b regression — must
    NOT be relaxed).
  * note_kind=knowledge **with** the approval triple renders via
    ``default_render_fn`` → ``render_research_note(kind="knowledge")``
    → ``render_knowledge_note``.
  * research / decision rendering is unchanged (kwarg-name fix
    didn't break the existing happy path).
  * end-to-end: forum handoff → approval reply → obsidian_write
    queued → ObsidianWriterWorker.process_job writes a knowledge
    file to vault.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import List
from unittest import mock

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.approval_worker import (
    APPROVAL_KIND_OBSIDIAN_WRITE,
    ApprovalWorker,
)
from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.obsidian_writer_worker import (
    JOB_TYPE_OBSIDIAN_WRITE,
    NOTE_KIND_DECISION,
    NOTE_KIND_KNOWLEDGE,
    NOTE_KIND_RESEARCH,
    SKIPPED_APPROVAL_REQUIRED,
    ObsidianRenderError,
    ObsidianWriteRequest,
    ObsidianWriterWorker,
    default_render_fn,
)
from yule_orchestrator.agents.job_queue.state_machine import JobState
from yule_orchestrator.agents.job_queue.store import JobQueue
from yule_orchestrator.agents.workflow_state import (
    WorkflowSession,
    WorkflowState,
    save_session,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _seed_session_without_pack(
    *,
    session_id: str,
    forum_thread_id: int = 50001,
    prompt: str = "k8s 운영 합의 정리",
):
    """A-M7.5f: real-world handoff session has no research_pack on
    extra. Mirror that shape so the no-pack fallback is exercised
    end-to-end against ``load_session`` (not a stub).
    """

    when = datetime.now(tz=timezone.utc)
    session = WorkflowSession(
        session_id=session_id,
        prompt=prompt,
        task_type="research",
        state=WorkflowState.IN_PROGRESS,
        created_at=when,
        updated_at=when,
        role_sequence=("tech-lead", "devops-engineer"),
        extra={
            "research_forum_thread_id": forum_thread_id,
            "active_research_roles": ["tech-lead", "devops-engineer"],
            # NOTE: no 'research_pack' key — the very gap A-M7.5f closes.
        },
    )
    save_session(session)
    return session


def _seed_session_with_pack(
    *,
    session_id: str,
    forum_thread_id: int = 50001,
    title: str = "k8s 운영 자료",
):
    """Create a workflow session that has a real ResearchPack on
    ``session.extra['research_pack']`` so ``default_render_fn`` has
    enough content to compose a markdown note. Reads minimum-viable
    schema fields the renderer touches (title / sources / urls).
    """

    when = datetime.now(tz=timezone.utc)
    research_pack_payload = {
        "title": title,
        "session_id": session_id,
        "summary": "k8s 운영 핵심 자료 정리",
        "sources": [
            {
                "title": "Official k8s docs",
                "url": "https://kubernetes.io/docs/",
                "kind": "doc",
            }
        ],
        "urls": ["https://kubernetes.io/docs/"],
        "findings": [],
    }
    session = WorkflowSession(
        session_id=session_id,
        prompt="k8s 운영 자료 정리",
        task_type="research",
        state=WorkflowState.IN_PROGRESS,
        created_at=when,
        updated_at=when,
        role_sequence=("tech-lead", "devops-engineer"),
        extra={
            "research_forum_thread_id": forum_thread_id,
            "active_research_roles": ["tech-lead", "devops-engineer"],
            "research_pack": research_pack_payload,
        },
    )
    save_session(session)
    return session


class _RenderFixture(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        self._vault = Path(self._tmp.name) / "vault"
        self._vault.mkdir()
        # Pin all SQLite + workflow_state caches at the temp dir so
        # default_render_fn's ``load_session`` reads the same row we
        # just saved.
        self._env = mock.patch.dict(
            os.environ,
            {
                "YULE_CACHE_DB_PATH": str(self._db),
                "YULE_REPO_ROOT": str(self._tmp.name),
                "OBSIDIAN_VAULT_PATH": str(self._vault),
            },
        )
        self._env.start()
        self.addCleanup(self._env.stop)


# ---------------------------------------------------------------------------
# Approval guard regression — knowledge without triple stays blocked
# ---------------------------------------------------------------------------


class ApprovalGuardRegressionTests(_RenderFixture):
    def test_knowledge_without_approval_triple_remains_blocked(self) -> None:
        # Most important M5b regression — A-M7.5e relaxed the kind
        # set in default_render_fn but MUST NOT relax the approval
        # guard inside ObsidianWriterWorker.process_job.
        sid = "sess-knowledge-guard"
        _seed_session_with_pack(session_id=sid)
        queue = JobQueue(db_path=self._db)
        heartbeats = HeartbeatStore(db_path=self._db)
        write_calls: List = []

        def fake_write(_note, vault, _req):
            write_calls.append(vault)
            return SimpleNamespace(
                target_path=vault / "x.md",
                written=True,
                dry_run=False,
                suffix_applied=False,
            )

        worker = ObsidianWriterWorker(
            queue=queue,
            heartbeats=heartbeats,
            render_fn=default_render_fn,
            write_fn=fake_write,
            vault_root_resolver=lambda _r: self._vault,
        )
        request = ObsidianWriteRequest(
            session_id=sid,
            note_kind=NOTE_KIND_KNOWLEDGE,
            title="결정 노트",
            approval_id=None,
            approved_by=None,
            approved_at=None,
        )
        outcome = _run(worker.run_one(request))
        # Approval guard fires at process_job before render_fn runs.
        self.assertEqual(outcome.skipped_reason, SKIPPED_APPROVAL_REQUIRED)
        self.assertEqual(write_calls, [])
        # Queue row landed FAILED_RETRYABLE so requeue_retryable
        # can pick it up after the operator fixes approval state.
        rows = [
            r for r in queue.list_for_session(sid)
            if r.job_type == JOB_TYPE_OBSIDIAN_WRITE
        ]
        self.assertEqual(rows[0].state, JobState.FAILED_RETRYABLE)


# ---------------------------------------------------------------------------
# Knowledge happy path — default_render_fn produces a note
# ---------------------------------------------------------------------------


class KnowledgeRenderHappyPathTests(_RenderFixture):
    def test_knowledge_with_approval_triple_and_pack_renders_note(
        self,
    ) -> None:
        sid = "sess-knowledge-pack"
        _seed_session_with_pack(session_id=sid)
        request = ObsidianWriteRequest(
            session_id=sid,
            note_kind=NOTE_KIND_KNOWLEDGE,
            title="k8s 운영 결정 노트",
            approval_id="apv-1",
            approved_by="masterway",
            approved_at="2026-05-08T10:00:00+00:00",
        )
        note = default_render_fn(request)
        # render_research_note(kind="knowledge") wraps the build into
        # an ``ObsidianNote`` (path + content + frontmatter), the same
        # shape ``research`` / ``decision`` already produce — the
        # writer's signature is unchanged.
        self.assertIsNotNone(note)
        self.assertTrue(hasattr(note, "path"))
        self.assertTrue(hasattr(note, "content"))
        self.assertTrue(hasattr(note, "frontmatter"))
        self.assertGreater(len(note.content), 100)

    def test_knowledge_without_research_pack_still_renders(self) -> None:
        # A-M7.5f core fix — operator can save a thread's consensus
        # to vault BEFORE any research_pack was collected. The
        # handoff path doesn't require a pack; default_render_fn
        # must follow the same contract for knowledge kind.
        sid = "sess-knowledge-nopack"
        _seed_session_without_pack(session_id=sid)
        request = ObsidianWriteRequest(
            session_id=sid,
            note_kind=NOTE_KIND_KNOWLEDGE,
            title="forum 토의 합의",
            approval_id="apv-2",
            approved_by="masterway",
            approved_at="2026-05-08T10:30:00+00:00",
        )
        note = default_render_fn(request)
        # Note is non-empty even without a pack — the renderer
        # falls back to session.prompt + request.title.
        self.assertIsNotNone(note)
        self.assertGreater(len(note.content), 50)
        # Title flows through to the vault filename / frontmatter.
        self.assertTrue(getattr(note, "frontmatter", None))


class ResearchAndDecisionUnchangedTests(_RenderFixture):
    def test_research_kind_renders_with_default_fn(self) -> None:
        sid = "sess-research-default"
        _seed_session_with_pack(session_id=sid)
        request = ObsidianWriteRequest(
            session_id=sid,
            note_kind=NOTE_KIND_RESEARCH,
            title="k8s 자료 collection",
        )
        # Research kind doesn't require approval triple → renders
        # straight away. A-M7.5e's kwarg-name fix (project_override
        # → project) is exercised here too.
        note = default_render_fn(request)
        self.assertIsNotNone(note)

    def test_decision_kind_renders_with_default_fn(self) -> None:
        sid = "sess-decision-default"
        _seed_session_with_pack(session_id=sid)
        request = ObsidianWriteRequest(
            session_id=sid,
            note_kind=NOTE_KIND_DECISION,
            title="k8s 결정 정리",
        )
        note = default_render_fn(request)
        self.assertIsNotNone(note)

    def test_unsupported_kind_still_raises(self) -> None:
        sid = "sess-meeting"
        _seed_session_with_pack(session_id=sid)
        request = ObsidianWriteRequest(
            session_id=sid,
            note_kind="meeting",
            title="회의록",
        )
        with self.assertRaises(ObsidianRenderError) as ctx:
            default_render_fn(request)
        self.assertIn("meeting", str(ctx.exception))

    def test_research_without_pack_still_fails(self) -> None:
        # A-M7.5f's relaxation is knowledge-only — research / decision
        # still need session.extra['research_pack']. The renderer for
        # those kinds quotes sources / findings and a missing pack
        # would produce a hollow note; better to fail loudly.
        sid = "sess-research-nopack"
        _seed_session_without_pack(session_id=sid)
        request = ObsidianWriteRequest(
            session_id=sid,
            note_kind=NOTE_KIND_RESEARCH,
            title="research without pack",
        )
        with self.assertRaises(ObsidianRenderError) as ctx:
            default_render_fn(request)
        self.assertIn("research_pack", str(ctx.exception))

    def test_decision_without_pack_still_fails(self) -> None:
        sid = "sess-decision-nopack"
        _seed_session_without_pack(session_id=sid)
        request = ObsidianWriteRequest(
            session_id=sid,
            note_kind=NOTE_KIND_DECISION,
            title="decision without pack",
        )
        with self.assertRaises(ObsidianRenderError) as ctx:
            default_render_fn(request)
        self.assertIn("research_pack", str(ctx.exception))


# ---------------------------------------------------------------------------
# End-to-end — forum handoff → approval reply → knowledge write
# ---------------------------------------------------------------------------


class ForumHandoffToKnowledgeWriteTests(_RenderFixture):
    """The user-facing failure mode A-M7.5e closes:

    1. forum thread "Obsidian에 정리하고 싶어"
    2. handoff → ApprovalWorker.run_one → SAVED row in #승인-대기
    3. user replies "이대로 저장" in approval channel
    4. handle_approval_reply → ObsidianWriterWorker.enqueue with
       note_kind=knowledge + approval triple
    5. ObsidianWriterWorker.process_job → default_render_fn →
       knowledge note written to vault.
    """

    APPROVAL_CHANNEL_ID: int = 80001

    def _build_workers(self):
        queue = JobQueue(db_path=self._db)
        heartbeats = HeartbeatStore(db_path=self._db)
        posted: List = []

        async def post_fn(req, rendered):
            posted.append((req, rendered))
            return {"posted_message_id": 90000 + len(posted)}

        approval_worker = ApprovalWorker(
            queue=queue,
            heartbeats=heartbeats,
            post_fn=post_fn,
            channel_resolver=lambda: self.APPROVAL_CHANNEL_ID,
        )
        write_calls: List = []

        def real_write(note, vault, request):
            # Use the shipped writer for the integration — it
            # actually persists a markdown file on disk we can grep.
            from yule_orchestrator.agents.obsidian.writer import write_note

            result = write_note(
                note,
                vault,
                overwrite=request.overwrite,
                dry_run=request.dry_run,
            )
            write_calls.append((note, vault, request, result))
            return result

        obsidian_worker = ObsidianWriterWorker(
            queue=queue,
            heartbeats=heartbeats,
            render_fn=default_render_fn,
            write_fn=real_write,
            vault_root_resolver=lambda _r: self._vault,
        )
        return queue, approval_worker, obsidian_worker, posted, write_calls

    def _forum_msg(self, content: str, *, message_id: int = 60001):
        channel = SimpleNamespace(
            id=50001,
            parent_id=50000,
            parent=SimpleNamespace(id=50000, name="운영-리서치"),
            name="k8s 운영 자료",
            guild=SimpleNamespace(id=40000),
        )
        author = SimpleNamespace(
            id=7, name="masterway", global_name="masterway", bot=False
        )
        return SimpleNamespace(
            id=message_id,
            channel=channel,
            author=author,
            content=content,
            jump_url=f"https://discord.com/channels/40000/50001/{message_id}",
        )

    def _drive_full_pipeline(self, *, session, msg):
        from yule_orchestrator.agents.job_queue.approval_reply import (
            handle_approval_reply,
        )
        from yule_orchestrator.agents.job_queue.forum_obsidian_handoff import (
            route_forum_obsidian_save_request,
        )

        queue, approval_worker, obsidian_worker, posted, write_calls = (
            self._build_workers()
        )

        handoff = _run(
            route_forum_obsidian_save_request(
                message=msg,
                text=msg.content,
                queue=queue,
                approval_worker=approval_worker,
                session_lister=lambda **_: [session],
            )
        )
        reply_outcome = handle_approval_reply(
            queue=queue,
            obsidian_worker=obsidian_worker,
            text="이대로 저장",
            session_id=session.session_id,
            approved_by="masterway",
            source_message_id=msg.id,
            source_thread_id=msg.channel.id,
        )
        picked = queue.pick(
            worker_id="e2e-writer",
            job_types=[JOB_TYPE_OBSIDIAN_WRITE],
        )
        write_outcome = (
            _run(obsidian_worker.process_job(picked))
            if picked is not None
            else None
        )
        return {
            "handoff": handoff,
            "reply": reply_outcome,
            "write": write_outcome,
            "posted": posted,
            "write_calls": write_calls,
            "queue": queue,
        }

    def test_forum_handoff_to_knowledge_write_lands_in_vault(self) -> None:
        sid = "sess-e2e-knowledge"
        session = _seed_session_with_pack(session_id=sid)
        msg = self._forum_msg("Obsidian에 정리하고 싶어")
        result = self._drive_full_pipeline(session=session, msg=msg)
        self.assertTrue(result["handoff"].handled)
        self.assertIsNotNone(result["reply"].write_job_id)
        self.assertIsNone(result["write"].skipped_reason)
        self.assertEqual(result["write"].job.state, JobState.SAVED)
        # Real markdown file landed in vault.
        md_files = list(self._vault.rglob("*.md"))
        self.assertGreaterEqual(len(md_files), 1)

    def test_forum_handoff_with_no_research_pack_still_writes(self) -> None:
        # A-M7.5f core e2e — operator approves a save before any
        # research_pack collection. Pre-fix: failed_retryable with
        # "default render needs session.extra['research_pack']".
        # Post-fix: knowledge note lands in vault.
        sid = "sess-e2e-knowledge-nopack"
        session = _seed_session_without_pack(session_id=sid)
        msg = self._forum_msg("Obsidian에 정리해줘", message_id=60099)
        result = self._drive_full_pipeline(session=session, msg=msg)
        self.assertIsNotNone(result["reply"].write_job_id)
        self.assertIsNone(
            result["write"].skipped_reason,
            f"writer skipped: {result['write'].skipped_reason}",
        )
        self.assertEqual(result["write"].job.state, JobState.SAVED)
        md_files = list(self._vault.rglob("*.md"))
        self.assertGreaterEqual(
            len(md_files),
            1,
            f"expected a vault .md file, found {md_files}",
        )

if __name__ == "__main__":
    unittest.main()
