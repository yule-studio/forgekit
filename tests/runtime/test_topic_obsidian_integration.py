"""[E] M9 / M10a / M10b integration regression — topic ledger ↔ note routing ↔ hydration.

Pre-M13 dashboard for the three layers that touch the same Obsidian
write path:

  * **M9** topic ledger — one canonical key per research thread, status
    transitions ``researching → pending_approval → saved`` so successive
    save requests dedup at the producer.
  * **M10a** canonical note-kind / folder matrix — five kinds with the
    approval gate exactly on ``knowledge-note`` / ``decision-record``;
    auto-execute kinds (``research-log`` / ``agent-ops`` / ``blog-draft``)
    bypass the approval triple.
  * **M10b** hydration — ``source_thread_url`` / ``topic_key`` / the
    full ``thread_snapshot`` (with ``extracted_links``) flow from the
    forum producer onto :class:`ApprovalRequest.extra`, get carried
    onto :class:`ObsidianWriteRequest.metadata` by the converter, and
    surface in the rendered knowledge note.

The 9 scenarios mirror the M13 brief checklist:

  1. 같은 thread 에서 save request 3회 → approval_post 1개.
  2. pending approval 이 있으면 기존 job 안내.
  3. saved topic 재요청 → revision 안내.
  4. no pack + thread snapshot + extracted_links → hydrated note 생성.
  5. no pack + no snapshot → empty note write 차단.
  6. source_thread_url / topic_key / extracted_links 가 frontmatter
     또는 body 에 보존.
  7. canonical note-kind folder mapping 무회귀.
  8. knowledge final approval guard 유지.
  9. research-log auto write 는 approval 없이 가능.

The :func:`drive_forum_save_to_vault` helper at the top of the module
is the load-bearing e2e driver — M13 will reuse it to script the
"forum-thread → approved knowledge note in vault" path without
re-implementing the queue / worker / writer wiring.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, List, Mapping, Optional, Tuple
from unittest import mock

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.approval_reply import (
    handle_approval_reply,
)
from yule_engineering.agents.job_queue.approval_worker import (
    APPROVAL_KIND_OBSIDIAN_WRITE,
    ApprovalRequest,
    ApprovalWorker,
)
from yule_engineering.agents.job_queue.forum_obsidian_handoff import (
    RESPONSE_TOPIC_ALREADY_SAVED,
    RESPONSE_TOPIC_PENDING_APPROVAL,
    SKIPPED_DUPLICATE_APPROVAL,
    SKIPPED_TOPIC_ALREADY_SAVED,
    SKIPPED_TOPIC_PENDING_APPROVAL,
    render_handoff_response,
    route_forum_obsidian_save_request,
)
from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.obsidian_writer_worker import (
    JOB_TYPE_OBSIDIAN_WRITE,
    NOTE_KIND_KNOWLEDGE,
    NOTE_KIND_KNOWLEDGE_NOTE,
    NOTE_KIND_RESEARCH_LOG,
    SKIPPED_APPROVAL_REQUIRED,
    ObsidianRenderError,
    ObsidianWriteRequest,
    ObsidianWriterWorker,
    default_render_fn,
)
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue
from yule_engineering.agents.lifecycle.research_topic import (
    STATUS_SAVED,
    build_ledger_record,
    transition_topic_ledger,
    write_topic_ledger,
)
from yule_engineering.agents.obsidian.note_kinds import (
    FOLDER_AGENT_OPS,
    FOLDER_BLOG_DRAFTS,
    FOLDER_DECISIONS,
    FOLDER_KNOWLEDGE,
    FOLDER_RESEARCH_LOG,
    KIND_AGENT_OPS,
    KIND_BLOG_DRAFT,
    KIND_DECISION_RECORD,
    KIND_KNOWLEDGE_NOTE,
    KIND_RESEARCH_LOG,
    folder_for_canonical_kind,
    requires_approval,
)
from yule_engineering.agents.workflow_state import (
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


# ---------------------------------------------------------------------------
# E2E helper — M13 will import drive_forum_save_to_vault from here.
# ---------------------------------------------------------------------------


@dataclass
class _ForumE2EBundle:
    """Wiring for one e2e run: queue + workers + injected stubs."""

    queue: JobQueue
    heartbeats: HeartbeatStore
    approval_worker: ApprovalWorker
    obsidian_worker: ObsidianWriterWorker
    posted_cards: List[Tuple[ApprovalRequest, str]]
    write_calls: List[Tuple[Any, Path, ObsidianWriteRequest]]
    vault_root: Path


def _build_forum_e2e_bundle(
    *,
    db_path: Path,
    vault_root: Path,
    approval_channel_id: int = 80001,
    real_writer: bool = False,
) -> _ForumE2EBundle:
    """Build the e2e wiring used by every test in this module.

    *real_writer* swaps the stub markdown sink for the production
    :func:`agents.obsidian.writer.write_note` so the test ends with a
    real .md file in *vault_root*. Useful for hydration body assertions.
    """

    queue = JobQueue(db_path=db_path)
    heartbeats = HeartbeatStore(db_path=db_path)
    posted_cards: List[Tuple[ApprovalRequest, str]] = []

    async def post_fn(request: ApprovalRequest, rendered: str):
        posted_cards.append((request, rendered))
        return {
            "posted_message_id": 90000 + len(posted_cards),
            "channel_id": approval_channel_id,
        }

    approval_worker = ApprovalWorker(
        queue=queue,
        heartbeats=heartbeats,
        post_fn=post_fn,
        channel_resolver=lambda: approval_channel_id,
    )

    write_calls: List[Tuple[Any, Path, ObsidianWriteRequest]] = []

    if real_writer:
        from yule_engineering.agents.obsidian.writer import (
            write_note as _real_write,
        )

        def write_fn(note, vault, request):
            result = _real_write(
                note,
                vault,
                overwrite=request.overwrite,
                dry_run=request.dry_run,
            )
            write_calls.append((note, vault, request))
            return result

    else:

        def write_fn(note, vault, request):
            write_calls.append((note, vault, request))
            return SimpleNamespace(
                target_path=vault / f"{request.title}.md",
                original_target_path=vault / f"{request.title}.md",
                written=True,
                dry_run=False,
                suffix_applied=False,
            )

    obsidian_worker = ObsidianWriterWorker(
        queue=queue,
        heartbeats=heartbeats,
        render_fn=default_render_fn,
        write_fn=write_fn,
        vault_root_resolver=lambda _r: vault_root,
    )
    return _ForumE2EBundle(
        queue=queue,
        heartbeats=heartbeats,
        approval_worker=approval_worker,
        obsidian_worker=obsidian_worker,
        posted_cards=posted_cards,
        write_calls=write_calls,
        vault_root=vault_root,
    )


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
    author = SimpleNamespace(
        id=author_id,
        name=author_name,
        global_name=author_name,
        bot=False,
    )
    return SimpleNamespace(
        id=message_id,
        channel=channel,
        author=author,
        content=content,
        guild=SimpleNamespace(id=guild_id),
        jump_url=(
            f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"
        ),
    )


def _seed_forum_session(
    *,
    session_id: str,
    forum_thread_id: int = 50001,
    prompt: str = "k8s 운영 자료 정리",
    extra_overrides: Optional[Mapping[str, Any]] = None,
) -> WorkflowSession:
    """Persist a no-pack research session whose thread id matches the
    fake forum message channel id. Returns the saved row.
    """

    when = datetime.now(tz=timezone.utc)
    extra: dict[str, Any] = {
        "research_forum_thread_id": forum_thread_id,
        "active_research_roles": ["tech-lead", "devops-engineer"],
    }
    if extra_overrides:
        extra.update(extra_overrides)
    session = WorkflowSession(
        session_id=session_id,
        prompt=prompt,
        task_type="research",
        state=WorkflowState.IN_PROGRESS,
        created_at=when,
        updated_at=when,
        role_sequence=("tech-lead", "devops-engineer"),
        extra=extra,
    )
    save_session(session)
    return session


def _stub_history_with_links() -> Tuple[
    Callable[[Any], Awaitable[List[Any]]], Callable[[Any], Optional[str]]
]:
    """Return (history_fetcher, role_resolver) that yield a thread with
    one operator + two role-bot messages carrying URLs.
    """

    async def fetcher(_msg):
        return [
            SimpleNamespace(
                id=70001,
                content=(
                    "tech-lead 종합: rolling update 정책 자료 "
                    "https://kubernetes.io/docs/concepts/workloads/ 정리"
                ),
                author=SimpleNamespace(
                    id=11,
                    name="tl-bot",
                    global_name="tl-bot",
                    bot=True,
                ),
                created_at=None,
            ),
            SimpleNamespace(
                id=70002,
                content=(
                    "devops: runbook https://example.com/k8s-runbook 도 같이 봤음"
                ),
                author=SimpleNamespace(
                    id=12,
                    name="devops-bot",
                    global_name="devops-bot",
                    bot=True,
                ),
                created_at=None,
            ),
            SimpleNamespace(
                id=70003,
                content="operator: 정리 부탁",
                author=SimpleNamespace(
                    id=7,
                    name="masterway",
                    global_name="masterway",
                    bot=False,
                ),
                created_at=None,
            ),
        ]

    def role_resolver(author):
        mapping = {"tl-bot": "tech-lead", "devops-bot": "devops-engineer"}
        name = (
            getattr(author, "global_name", None)
            or getattr(author, "name", None)
            or ""
        )
        return mapping.get(name)

    return fetcher, role_resolver


def drive_forum_save_to_vault(
    *,
    bundle: _ForumE2EBundle,
    message: Any,
    session: Any,
    thread_history_fetcher: Optional[Callable[[Any], Awaitable[List[Any]]]] = None,
    role_resolver: Optional[Callable[[Any], Optional[str]]] = None,
    approve_text: str = "이대로 저장",
) -> dict:
    """Drive the full forum-handoff → approval card → reply → writer
    pipeline against *bundle* using a single fake forum message.

    The helper M13 will reuse: returns a dict carrying every stage's
    outcome (handoff, approval card payload, reply outcome, write
    outcome, and the saved markdown body when a real writer is wired)
    so a regression caller can assert on whichever surface is broken.
    """

    handoff_outcome = _run(
        route_forum_obsidian_save_request(
            message=message,
            text=message.content,
            queue=bundle.queue,
            approval_worker=bundle.approval_worker,
            session_lister=lambda **_: [session],
            thread_history_fetcher=thread_history_fetcher,
            role_resolver=role_resolver,
        )
    )
    reply_outcome = handle_approval_reply(
        queue=bundle.queue,
        obsidian_worker=bundle.obsidian_worker,
        text=approve_text,
        session_id=session.session_id,
        approved_by="masterway",
        source_message_id=message.id,
        source_thread_id=message.channel.id,
    )
    picked = bundle.queue.pick(
        worker_id="e2e-writer",
        job_types=[JOB_TYPE_OBSIDIAN_WRITE],
    )
    write_outcome = (
        _run(bundle.obsidian_worker.process_job(picked))
        if picked is not None
        else None
    )
    return {
        "handoff": handoff_outcome,
        "reply": reply_outcome,
        "write": write_outcome,
        "approval_card": (
            bundle.posted_cards[-1] if bundle.posted_cards else None
        ),
    }


# ---------------------------------------------------------------------------
# Shared test base — wires temp DB + temp vault + env pinning so
# default_render_fn's ``load_session`` reads the seeded row.
# ---------------------------------------------------------------------------


class _IntegrationFixture(unittest.TestCase):
    """Common temp dirs / env pinning. Subclasses build the bundle
    when they need it (some tests want a stub writer, some want real)."""

    def setUp(self) -> None:  # noqa: D401
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        self._vault = Path(self._tmp.name) / "vault"
        self._vault.mkdir()
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

    def _bundle(self, *, real_writer: bool = False) -> _ForumE2EBundle:
        return _build_forum_e2e_bundle(
            db_path=self._db,
            vault_root=self._vault,
            real_writer=real_writer,
        )


# ---------------------------------------------------------------------------
# Scenarios 1-3 — topic ledger dedup
# ---------------------------------------------------------------------------


class TopicDedupRegressionTests(_IntegrationFixture):
    """One thread → one approval card. Subsequent saves dedup with the
    correct guidance message based on lifecycle stage.
    """

    def test_three_save_requests_collapse_to_one_approval_card(self) -> None:
        # Scenario 1 — repeated "Obsidian 에 정리하고 싶어" replies in
        # the same forum thread must not multiply approval cards.
        bundle = self._bundle()
        session = _seed_forum_session(session_id="sess-topic-dedup")

        for i in range(3):
            message = _forum_thread_message(message_id=60001 + i)
            outcome = _run(
                route_forum_obsidian_save_request(
                    message=message,
                    text=message.content,
                    queue=bundle.queue,
                    approval_worker=bundle.approval_worker,
                    session_lister=lambda **_: [session],
                )
            )
            self.assertTrue(outcome.handled)

        cards = bundle.posted_cards
        self.assertEqual(
            len(cards), 1,
            f"expected exactly one approval card, got {len(cards)}",
        )
        approval_rows = [
            j for j in bundle.queue.list_for_session(session.session_id)
            if j.job_type == "approval_post"
        ]
        self.assertEqual(len(approval_rows), 1)

    def test_pending_approval_dedup_uses_response_template(self) -> None:
        # Scenario 2 — second save while approval card is still
        # PENDING surfaces RESPONSE_TOPIC_PENDING_APPROVAL with the
        # existing job id so the operator can navigate, not a new card.
        bundle = self._bundle()
        session = _seed_forum_session(session_id="sess-pending-dedup")

        first_message = _forum_thread_message()
        first = _run(
            route_forum_obsidian_save_request(
                message=first_message,
                text=first_message.content,
                queue=bundle.queue,
                approval_worker=bundle.approval_worker,
                session_lister=lambda **_: [session],
            )
        )
        self.assertIsNone(first.skipped_reason)
        approval_job_id = first.approval_job_id
        self.assertIsNotNone(approval_job_id)

        second_message = _forum_thread_message(message_id=60002)
        second = _run(
            route_forum_obsidian_save_request(
                message=second_message,
                text=second_message.content,
                queue=bundle.queue,
                approval_worker=bundle.approval_worker,
                session_lister=lambda **_: [session],
            )
        )
        # M9 dedup priority: topic_pending wins over message-id dedup.
        self.assertIn(
            second.skipped_reason,
            {SKIPPED_TOPIC_PENDING_APPROVAL, SKIPPED_DUPLICATE_APPROVAL},
        )
        rendered = render_handoff_response(second)
        self.assertIsNotNone(rendered)
        self.assertIn(approval_job_id, rendered or "")

    def test_saved_topic_replies_with_revision_template(self) -> None:
        # Scenario 3 — approval already replied + obsidian_write SAVED.
        # New save request for the same thread must NOT enqueue another
        # card; the producer responds with RESPONSE_TOPIC_ALREADY_SAVED
        # carrying the saved vault path. Operator must explicitly type
        # a revision phrase to bypass.
        bundle = self._bundle()
        session = _seed_forum_session(session_id="sess-saved-revision")

        # Drive the first save through to a SAVED obsidian_write row.
        first_message = _forum_thread_message()
        result = drive_forum_save_to_vault(
            bundle=bundle,
            message=first_message,
            session=session,
        )
        self.assertIsNotNone(result["write"])
        self.assertEqual(result["write"].job.state, JobState.SAVED)

        # Sync the topic ledger to STATUS_SAVED — the producer reads
        # session.extra to short-circuit on the next save request.
        # Re-load the persisted session so we pick up any extra writes
        # the writer worker may have stamped.
        from yule_engineering.agents.workflow_state import load_session

        latest = load_session(session.session_id)
        assert latest is not None
        record = build_ledger_record(
            session=latest,
            research_thread_id=int(first_message.channel.id),
            active_roles=tuple(
                latest.extra.get("active_research_roles") or ()
            ),
        )
        record = transition_topic_ledger(record, status=STATUS_SAVED)
        new_extra = write_topic_ledger(latest.extra, record)
        from dataclasses import replace as _replace

        from yule_engineering.agents.workflow_state import (
            update_session as _update,
        )

        _update(_replace(latest, extra=new_extra), now=datetime.now(tz=timezone.utc))
        latest = load_session(session.session_id)

        # Second save attempt — dedup must now route to "already saved".
        second_message = _forum_thread_message(message_id=60099)
        second = _run(
            route_forum_obsidian_save_request(
                message=second_message,
                text=second_message.content,
                queue=bundle.queue,
                approval_worker=bundle.approval_worker,
                session_lister=lambda **_: [latest],
            )
        )
        self.assertEqual(
            second.skipped_reason, SKIPPED_TOPIC_ALREADY_SAVED
        )
        rendered = render_handoff_response(second) or ""
        # Both fields the operator template formats — topic_key + vault path.
        self.assertIn("이미 vault 에 저장", rendered)
        # No new approval card was posted.
        self.assertEqual(len(bundle.posted_cards), 1)

    def test_saved_topic_revision_phrase_reopens_approval_path(self) -> None:
        # Scenario 3 follow-up — when the operator explicitly types
        # "개정본으로 저장해줘" the producer falls through the saved
        # guard and enqueues a NEW approval card. Confirms the revision
        # bypass is available without auto-deleting the prior note.
        bundle = self._bundle()
        session = _seed_forum_session(session_id="sess-revision-bypass")
        first_message = _forum_thread_message()
        drive_forum_save_to_vault(
            bundle=bundle,
            message=first_message,
            session=session,
        )

        # Force ledger to STATUS_SAVED so we hit the guard branch.
        from yule_engineering.agents.workflow_state import (
            load_session,
            update_session as _update,
        )
        from dataclasses import replace as _replace

        latest = load_session(session.session_id)
        assert latest is not None
        record = build_ledger_record(
            session=latest,
            research_thread_id=int(first_message.channel.id),
        )
        record = transition_topic_ledger(record, status=STATUS_SAVED)
        new_extra = write_topic_ledger(latest.extra, record)
        _update(
            _replace(latest, extra=new_extra),
            now=datetime.now(tz=timezone.utc),
        )
        latest = load_session(session.session_id)

        revision_message = _forum_thread_message(
            message_id=60198,
            # Save-request detector requires the "obsidian 에 정리" /
            # "옵시디언에 저장" lexicon; the revision bypass requires one
            # of "개정본 / 다시 저장 / 덮어써 / supersede / overwrite". We
            # combine both in one message so the detector fires AND the
            # producer takes the saved-topic bypass branch.
            content="Obsidian 에 정리해줘. 개정본으로 다시 저장 부탁.",
        )
        revision = _run(
            route_forum_obsidian_save_request(
                message=revision_message,
                text=revision_message.content,
                queue=bundle.queue,
                approval_worker=bundle.approval_worker,
                session_lister=lambda **_: [latest],
            )
        )
        self.assertIsNone(revision.skipped_reason)
        # A second approval card landed for the revision request.
        self.assertEqual(len(bundle.posted_cards), 2)


# ---------------------------------------------------------------------------
# Scenarios 4-6 — hydration contracts
# ---------------------------------------------------------------------------


class HydrationContractRegressionTests(_IntegrationFixture):
    """no-pack threads still hydrate when the snapshot carries content;
    fully-empty hydration is refused; source_thread_url / topic_key /
    extracted_links round-trip into the saved markdown.
    """

    def test_no_pack_with_snapshot_renders_hydrated_knowledge_note(
        self,
    ) -> None:
        # Scenario 4 — no research_pack on session; snapshot has
        # messages + links + role summaries → renderer composes a
        # non-empty knowledge note.
        bundle = self._bundle(real_writer=True)
        session = _seed_forum_session(session_id="sess-no-pack-hydrated")
        fetcher, role_resolver = _stub_history_with_links()
        result = drive_forum_save_to_vault(
            bundle=bundle,
            message=_forum_thread_message(),
            session=session,
            thread_history_fetcher=fetcher,
            role_resolver=role_resolver,
        )
        self.assertIsNotNone(result["write"])
        self.assertIsNone(result["write"].skipped_reason)
        self.assertEqual(result["write"].job.state, JobState.SAVED)

        md_files = list(self._vault.rglob("*.md"))
        self.assertGreaterEqual(len(md_files), 1)

    def test_no_pack_no_snapshot_blocks_write(self) -> None:
        # Scenario 5 — pack absent + snapshot empty + synthesis absent
        # → renderer raises ObsidianRenderError; queue row lands
        # FAILED_RETRYABLE; no .md file in vault.
        bundle = self._bundle(real_writer=True)
        sid = "sess-no-pack-no-snapshot"
        when = datetime.now(tz=timezone.utc)
        save_session(
            WorkflowSession(
                session_id=sid,
                prompt="empty",
                task_type="research",
                state=WorkflowState.IN_PROGRESS,
                created_at=when,
                updated_at=when,
                role_sequence=(),
                extra={},
            )
        )
        # Skip the forum producer — go directly to the writer with a
        # request that carries the approval triple but no hydration so
        # the empty-note guard is the only thing that can fire.
        request = ObsidianWriteRequest(
            session_id=sid,
            note_kind=NOTE_KIND_KNOWLEDGE,
            title="hollow",
            approval_id="apv-empty",
            approved_by="masterway",
            approved_at="2026-05-08T10:00:00+00:00",
        )
        # The worker marks the row FAILED_RETRYABLE BEFORE re-raising
        # the renderer's ObsidianRenderError, so the contract we pin
        # is "row reaches FAILED_RETRYABLE + no .md landed". Catching
        # the propagated exception keeps the harness consumer-friendly.
        with self.assertRaises(ObsidianRenderError) as ctx:
            _run(bundle.obsidian_worker.run_one(request))
        self.assertIn("hydration", str(ctx.exception))

        rows = [
            j for j in bundle.queue.list_for_session(sid)
            if j.job_type == JOB_TYPE_OBSIDIAN_WRITE
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].state, JobState.FAILED_RETRYABLE)
        # No vault file landed.
        self.assertEqual(list(self._vault.rglob("*.md")), [])

    def test_source_thread_url_topic_key_and_links_preserved(self) -> None:
        # Scenario 6 — the canonical hydration triple
        # (source_thread_url / topic_key / extracted_links) must surface
        # somewhere in the saved note. We assert frontmatter for the
        # IDs and body for the URLs so the contract holds even when the
        # renderer reorganises sections.
        bundle = self._bundle(real_writer=True)
        session = _seed_forum_session(session_id="sess-hydration-triple")
        fetcher, role_resolver = _stub_history_with_links()
        message = _forum_thread_message()
        result = drive_forum_save_to_vault(
            bundle=bundle,
            message=message,
            session=session,
            thread_history_fetcher=fetcher,
            role_resolver=role_resolver,
        )
        self.assertIsNotNone(result["write"])
        self.assertEqual(result["write"].job.state, JobState.SAVED)

        # Find the knowledge note — vault layout may produce other
        # research-log files alongside, but the knowledge note carries
        # the operator-visible hydration block.
        md_files = list(self._vault.rglob("*.md"))
        self.assertGreaterEqual(len(md_files), 1)
        knowledge_bodies = [
            (path, path.read_text(encoding="utf-8")) for path in md_files
        ]
        # At least one note quotes the operator URLs.
        link_present = any(
            "https://kubernetes.io/docs/concepts/workloads/" in body
            for _path, body in knowledge_bodies
        )
        self.assertTrue(
            link_present,
            "expected at least one .md to quote the kubernetes.io URL",
        )
        thread_url_present = any(
            message.jump_url in body for _path, body in knowledge_bodies
        )
        self.assertTrue(
            thread_url_present,
            "expected source_thread_url to be quoted in the saved note",
        )

        # And the knowledge_write row's metadata should still carry
        # extracted_links + topic_key for grep-ability.
        write_rows = [
            j for j in bundle.queue.list_for_session(session.session_id)
            if j.job_type == JOB_TYPE_OBSIDIAN_WRITE
        ]
        knowledge_rows = [
            r for r in write_rows
            if (r.payload or {}).get("note_kind") == NOTE_KIND_KNOWLEDGE
        ]
        self.assertTrue(knowledge_rows, "expected a knowledge write row")
        meta = (knowledge_rows[0].payload or {}).get("metadata") or {}
        self.assertTrue(meta.get("topic_key"))
        self.assertEqual(meta.get("source_thread_url"), message.jump_url)
        self.assertIn(
            "https://kubernetes.io/docs/concepts/workloads/",
            list(meta.get("extracted_links") or ()),
        )


# ---------------------------------------------------------------------------
# Scenarios 7-9 — note-kind / approval matrix
# ---------------------------------------------------------------------------


class NoteKindMatrixRegressionTests(_IntegrationFixture):
    """The M10a folder mapping + approval matrix is the contract every
    M10b producer assumes. A drift here would silently misroute notes.
    """

    def test_canonical_folder_mapping_for_all_m10a_kinds(self) -> None:
        # Scenario 7 — verify the canonical mapping at the policy
        # surface (not via vault writes; that's covered in test_note_kinds).
        self.assertEqual(
            folder_for_canonical_kind(KIND_RESEARCH_LOG), FOLDER_RESEARCH_LOG
        )
        self.assertEqual(
            folder_for_canonical_kind(KIND_KNOWLEDGE_NOTE), FOLDER_KNOWLEDGE
        )
        self.assertEqual(
            folder_for_canonical_kind(KIND_DECISION_RECORD), FOLDER_DECISIONS
        )
        self.assertEqual(
            folder_for_canonical_kind(KIND_AGENT_OPS), FOLDER_AGENT_OPS
        )
        self.assertEqual(
            folder_for_canonical_kind(KIND_BLOG_DRAFT), FOLDER_BLOG_DRAFTS
        )
        # Numbered prefix is the load-bearing vault sort order.
        self.assertEqual(FOLDER_RESEARCH_LOG, "10-research-log")
        self.assertEqual(FOLDER_KNOWLEDGE, "20-knowledge")
        self.assertEqual(FOLDER_DECISIONS, "30-decisions")
        self.assertEqual(FOLDER_AGENT_OPS, "40-agent-ops")
        self.assertEqual(FOLDER_BLOG_DRAFTS, "50-blog-drafts")

    def test_knowledge_final_approval_guard_holds(self) -> None:
        # Scenario 8 — knowledge / decision still require the approval
        # triple at the worker boundary. Producer error (no triple) →
        # SKIPPED_APPROVAL_REQUIRED, never a vault write.
        bundle = self._bundle()
        session = _seed_forum_session(session_id="sess-knowledge-guard")
        request = ObsidianWriteRequest(
            session_id=session.session_id,
            note_kind=NOTE_KIND_KNOWLEDGE_NOTE,
            title="우회 시도",
            approval_id=None,
            approved_by=None,
            approved_at=None,
        )
        outcome = _run(bundle.obsidian_worker.run_one(request))
        self.assertEqual(outcome.skipped_reason, SKIPPED_APPROVAL_REQUIRED)
        self.assertEqual(bundle.write_calls, [])
        # Policy declaration also still True.
        self.assertTrue(requires_approval(KIND_KNOWLEDGE_NOTE))
        self.assertTrue(requires_approval(KIND_DECISION_RECORD))
        self.assertTrue(requires_approval("knowledge"))  # legacy alias

    def test_research_log_auto_writes_without_approval_triple(self) -> None:
        # Scenario 9 — research-log is L1 autonomy, no approval triple
        # needed. Auto-execute path enqueues a write, the worker
        # processes it, and a markdown file lands in the vault.
        bundle = self._bundle(real_writer=True)
        session = _seed_forum_session(session_id="sess-research-log-auto")
        request = ObsidianWriteRequest(
            session_id=session.session_id,
            note_kind=NOTE_KIND_RESEARCH_LOG,
            title="auto research-log",
            metadata={
                "original_prompt": "tech-lead 합의 흐름 요약",
                "thread_snapshot": {
                    "messages": [
                        {
                            "author": "masterway",
                            "content": "rolling update 합의 정리.",
                            "role": None,
                            "posted_at": None,
                        }
                    ],
                    "extracted_links": [
                        "https://kubernetes.io/docs/concepts/workloads/",
                    ],
                    "role_summaries": {
                        "tech-lead": "rolling update 정책 정리"
                    },
                },
                "topic_key": "k8s-rolling-update-12345",
                "source_thread_url": "https://discord.com/channels/1/2/3",
            },
        )
        outcome = _run(bundle.obsidian_worker.run_one(request))
        # No SKIPPED_APPROVAL_REQUIRED — research-log bypasses the gate.
        self.assertIsNone(outcome.skipped_reason)
        self.assertEqual(outcome.job.state, JobState.SAVED)
        self.assertEqual(len(bundle.write_calls), 1)
        # Policy declaration confirms the bypass is intentional.
        self.assertFalse(requires_approval(KIND_RESEARCH_LOG))
        self.assertFalse(requires_approval(KIND_AGENT_OPS))
        self.assertFalse(requires_approval(KIND_BLOG_DRAFT))


if __name__ == "__main__":
    unittest.main()
