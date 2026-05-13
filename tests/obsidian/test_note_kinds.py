"""M10a Knowledge Ops Core — note_kinds policy tests.

Pins the canonical kind names, the top-level folder mapping, the
approval matrix, the title normalization helper, the empty-content
guard on :class:`NoteRenderContext`, and end-to-end routing through
``default_render_fn`` for the new canonical names.

These tests cover the four explicit checks from M10a's brief:

  * research-log auto write (no approval required).
  * knowledge final approval guard (knowledge-note + decision-record
    both blocked when the approval triple is missing).
  * no content → write blocked (NoteRenderContext.has_content guard).
  * title normalization ([Research] strip + long-text truncation).

Plus the routing integration:

  * recommend_path("knowledge-note") → ``20-knowledge/``.
  * recommend_path("decision-record") → ``30-decisions/``.
  * Legacy short forms (``knowledge`` / ``decision``) keep their
    project-nested routing — guarantees no regression for the
    existing knowledge writer / M10b producers.
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

from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.obsidian_writer_worker import (
    NOTE_KIND_AGENT_OPS,
    NOTE_KIND_BLOG_DRAFT,
    NOTE_KIND_DECISION,
    NOTE_KIND_DECISION_RECORD,
    NOTE_KIND_KNOWLEDGE,
    NOTE_KIND_KNOWLEDGE_NOTE,
    NOTE_KIND_RESEARCH,
    NOTE_KIND_RESEARCH_LOG,
    SKIPPED_APPROVAL_REQUIRED,
    ObsidianRenderError,
    ObsidianWriteRequest,
    ObsidianWriterWorker,
    default_render_fn,
)
from yule_orchestrator.agents.job_queue.state_machine import JobState
from yule_orchestrator.agents.job_queue.store import JobQueue
from yule_orchestrator.agents.obsidian.export import recommend_path
from yule_orchestrator.agents.obsidian.note_kinds import (
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
    M10A_KINDS,
    NoteRenderContext,
    canonical_kind,
    folder_for_canonical_kind,
    is_canonical_kind,
    normalize_title,
    render_links_block,
    render_role_notes_block,
    render_source_thread_block,
    requires_approval,
)
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


# ---------------------------------------------------------------------------
# Canonical kinds + folder mapping
# ---------------------------------------------------------------------------


class CanonicalKindsTests(unittest.TestCase):
    def test_m10a_kinds_complete(self) -> None:
        self.assertEqual(
            set(M10A_KINDS),
            {
                KIND_RESEARCH_LOG,
                KIND_KNOWLEDGE_NOTE,
                KIND_DECISION_RECORD,
                KIND_AGENT_OPS,
                KIND_BLOG_DRAFT,
            },
        )
        # Order matches the vault tree numbering (10/20/30/40/50).
        self.assertEqual(
            tuple(M10A_KINDS),
            (
                KIND_RESEARCH_LOG,
                KIND_KNOWLEDGE_NOTE,
                KIND_DECISION_RECORD,
                KIND_AGENT_OPS,
                KIND_BLOG_DRAFT,
            ),
        )

    def test_canonical_kind_resolves_aliases(self) -> None:
        # Snake_case + legacy short forms collapse to canonical names.
        self.assertEqual(canonical_kind("knowledge"), KIND_KNOWLEDGE_NOTE)
        self.assertEqual(canonical_kind("knowledge_note"), KIND_KNOWLEDGE_NOTE)
        self.assertEqual(canonical_kind("knowledge-note"), KIND_KNOWLEDGE_NOTE)
        self.assertEqual(canonical_kind("decision"), KIND_DECISION_RECORD)
        self.assertEqual(canonical_kind("decisions"), KIND_DECISION_RECORD)
        self.assertEqual(canonical_kind("decision-record"), KIND_DECISION_RECORD)
        self.assertEqual(canonical_kind("research-log"), KIND_RESEARCH_LOG)
        self.assertEqual(canonical_kind("research_log"), KIND_RESEARCH_LOG)
        self.assertEqual(canonical_kind("agent-ops"), KIND_AGENT_OPS)
        self.assertEqual(canonical_kind("blog-draft"), KIND_BLOG_DRAFT)
        # Unknown kinds (e.g. legacy ``research`` / ``meeting``) return None.
        self.assertIsNone(canonical_kind("research"))
        self.assertIsNone(canonical_kind("meeting"))
        self.assertIsNone(canonical_kind(""))
        self.assertIsNone(canonical_kind(None))

    def test_is_canonical_kind(self) -> None:
        self.assertTrue(is_canonical_kind("knowledge-note"))
        self.assertTrue(is_canonical_kind("decision"))  # legacy alias
        self.assertFalse(is_canonical_kind("meeting"))
        self.assertFalse(is_canonical_kind("research"))


class FolderMappingTests(unittest.TestCase):
    def test_top_level_folder_per_canonical_kind(self) -> None:
        self.assertEqual(
            folder_for_canonical_kind(KIND_RESEARCH_LOG), FOLDER_RESEARCH_LOG
        )
        self.assertEqual(folder_for_canonical_kind(KIND_RESEARCH_LOG), "10-research-log")
        self.assertEqual(
            folder_for_canonical_kind(KIND_KNOWLEDGE_NOTE), FOLDER_KNOWLEDGE
        )
        self.assertEqual(folder_for_canonical_kind(KIND_KNOWLEDGE_NOTE), "20-knowledge")
        self.assertEqual(
            folder_for_canonical_kind(KIND_DECISION_RECORD), FOLDER_DECISIONS
        )
        self.assertEqual(folder_for_canonical_kind(KIND_DECISION_RECORD), "30-decisions")
        self.assertEqual(
            folder_for_canonical_kind(KIND_AGENT_OPS), FOLDER_AGENT_OPS
        )
        self.assertEqual(folder_for_canonical_kind(KIND_AGENT_OPS), "40-agent-ops")
        self.assertEqual(
            folder_for_canonical_kind(KIND_BLOG_DRAFT), FOLDER_BLOG_DRAFTS
        )
        self.assertEqual(folder_for_canonical_kind(KIND_BLOG_DRAFT), "50-blog-drafts")

    def test_unknown_kind_has_no_folder(self) -> None:
        self.assertIsNone(folder_for_canonical_kind("meeting"))
        self.assertIsNone(folder_for_canonical_kind(""))


class ExportRecommendPathTests(unittest.TestCase):
    """recommend_path uses M10a top-level folders for the new canonical
    names, while legacy short forms keep their project-nested layout."""

    def test_knowledge_note_routes_to_top_level_20_knowledge(self) -> None:
        path = recommend_path(
            title="결제 인프라 가이드",
            kind="knowledge-note",
            created_at=datetime(2026, 5, 8),
            env={},
        )
        self.assertEqual(path.folder, "20-knowledge")
        self.assertTrue(path.filename.startswith("knowledge-"))
        self.assertTrue(path.filename.endswith(".md"))

    def test_decision_record_routes_to_top_level_30_decisions(self) -> None:
        path = recommend_path(
            title="k8s 노드 풀 분리 결정",
            kind="decision-record",
            created_at=datetime(2026, 5, 8),
            env={},
        )
        self.assertEqual(path.folder, "30-decisions")
        self.assertTrue(path.filename.startswith("decision-"))

    def test_legacy_knowledge_keeps_project_nested_routing(self) -> None:
        path = recommend_path(
            title="결제 인프라 가이드",
            kind="knowledge",
            created_at=datetime(2026, 5, 8),
            env={},
        )
        self.assertTrue(
            path.folder.startswith("10-projects/"),
            f"legacy knowledge must stay project-nested; got {path.folder!r}",
        )
        self.assertTrue(path.folder.endswith("/knowledge"))

    def test_legacy_decision_keeps_project_nested_routing(self) -> None:
        path = recommend_path(
            title="결정",
            kind="decision",
            created_at=datetime(2026, 5, 8),
            env={},
        )
        self.assertTrue(path.folder.startswith("10-projects/"))
        self.assertTrue(path.folder.endswith("/decisions"))

    def test_legacy_research_log_keeps_project_nested_routing(self) -> None:
        # M10b regression — research-log producers pin the project-
        # nested layout in tests outside the M10a ownership scope. The
        # M10a folder mapping is documented in note_kinds but not yet
        # applied to this kind name; documented as a follow-up.
        path = recommend_path(
            title="DevOps 로드맵",
            kind="research-log",
            created_at=datetime(2026, 5, 8),
            env={},
        )
        self.assertTrue(path.folder.startswith("10-projects/"))
        self.assertTrue(path.folder.endswith("/research-log"))


# ---------------------------------------------------------------------------
# Approval matrix
# ---------------------------------------------------------------------------


class ApprovalMatrixTests(unittest.TestCase):
    def test_research_log_does_not_require_approval(self) -> None:
        self.assertFalse(requires_approval("research-log"))
        self.assertFalse(requires_approval("research_log"))

    def test_agent_ops_does_not_require_approval(self) -> None:
        self.assertFalse(requires_approval("agent-ops"))
        self.assertFalse(requires_approval("agent_ops"))

    def test_blog_draft_does_not_require_approval(self) -> None:
        self.assertFalse(requires_approval("blog-draft"))

    def test_knowledge_note_requires_approval(self) -> None:
        self.assertTrue(requires_approval("knowledge-note"))
        # Legacy short form still hits the gate.
        self.assertTrue(requires_approval("knowledge"))

    def test_decision_record_requires_approval(self) -> None:
        self.assertTrue(requires_approval("decision-record"))
        self.assertTrue(requires_approval("decision"))
        self.assertTrue(requires_approval("decisions"))

    def test_unknown_kind_does_not_require_approval(self) -> None:
        # Defer to the worker's existing fallback (legacy ``research``
        # / ``meeting`` etc. keep their existing behaviour).
        self.assertFalse(requires_approval("research"))
        self.assertFalse(requires_approval("meeting"))
        self.assertFalse(requires_approval(""))


class WorkerApprovalGuardTests(unittest.TestCase):
    """The queue worker enforces the M10a approval matrix at the
    boundary so a missing approval triple never results in a write."""

    def setUp(self) -> None:  # noqa: D401
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _request(
        self,
        *,
        note_kind: str,
        approval_id: str | None = None,
        approved_by: str | None = None,
        approved_at: str | None = None,
    ) -> ObsidianWriteRequest:
        return ObsidianWriteRequest(
            session_id="sess-test",
            note_kind=note_kind,
            title="t",
            approval_id=approval_id,
            approved_by=approved_by,
            approved_at=approved_at,
        )

    def test_request_requires_approval_recognises_canonical_kinds(self) -> None:
        for kind in (NOTE_KIND_KNOWLEDGE_NOTE, NOTE_KIND_DECISION_RECORD):
            with self.subTest(kind=kind):
                req = self._request(note_kind=kind)
                self.assertTrue(req.requires_approval())

    def test_request_does_not_require_approval_for_research_log(self) -> None:
        req = self._request(note_kind=NOTE_KIND_RESEARCH_LOG)
        self.assertFalse(req.requires_approval())

    def test_request_has_full_approval(self) -> None:
        triple = self._request(
            note_kind=NOTE_KIND_KNOWLEDGE_NOTE,
            approval_id="apv-1",
            approved_by="masterway",
            approved_at="2026-05-08T10:00:00+00:00",
        )
        self.assertTrue(triple.has_full_approval())
        no_triple = self._request(note_kind=NOTE_KIND_KNOWLEDGE_NOTE)
        self.assertFalse(no_triple.has_full_approval())


# ---------------------------------------------------------------------------
# Title normalization
# ---------------------------------------------------------------------------


class TitleNormalizationTests(unittest.TestCase):
    def test_strips_research_prefix(self) -> None:
        self.assertEqual(
            normalize_title("[Research] Stripe pricing 검토"),
            "Stripe pricing 검토",
        )
        self.assertEqual(
            normalize_title("[research] hero copy"), "hero copy"
        )
        self.assertEqual(
            normalize_title("[Decision] 노드 풀 분리"), "노드 풀 분리"
        )
        self.assertEqual(
            normalize_title("[Knowledge] k8s 운영"), "k8s 운영"
        )
        self.assertEqual(
            normalize_title("[Knowledge-Note] x"), "x"
        )

    def test_strips_bold_and_urls(self) -> None:
        self.assertEqual(
            normalize_title("**Stripe** pricing https://example.com 정리"),
            "Stripe pricing 정리",
        )

    def test_truncates_long_text(self) -> None:
        long_text = (
            "결제 모듈 멱등성 검증 흐름을 백엔드와 프론트엔드 양쪽에 "
            "적용하고 retry / dead-letter 큐 정책을 정리하는 매우 긴 제목"
        )
        result = normalize_title(long_text, max_chars=40)
        self.assertLessEqual(len(result), 41)  # max + "…"

    def test_truncates_at_sentence_boundary_when_possible(self) -> None:
        text = "결제 모듈 정리. 그리고 추가 후속 작업이 있다."
        # First sentence "결제 모듈 정리" is ≤ max_chars → returned cleanly.
        result = normalize_title(text, max_chars=20)
        self.assertEqual(result, "결제 모듈 정리")

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(normalize_title(""), "")
        self.assertEqual(normalize_title("[Research]"), "")
        self.assertEqual(normalize_title("   "), "")

    def test_collapses_whitespace_and_newlines(self) -> None:
        self.assertEqual(
            normalize_title("hero\n  copy   정리"),
            "hero copy 정리",
        )


# ---------------------------------------------------------------------------
# NoteRenderContext + helper renderers
# ---------------------------------------------------------------------------


class NoteRenderContextTests(unittest.TestCase):
    def test_has_content_blocks_empty_payload(self) -> None:
        ctx = NoteRenderContext(title="t", note_kind="knowledge-note")
        self.assertFalse(ctx.has_content())

    def test_has_content_when_body_block_set(self) -> None:
        ctx = NoteRenderContext(
            title="t",
            note_kind="knowledge-note",
            body_blocks=("## 결론\n\nrolling update 정책",),
        )
        self.assertTrue(ctx.has_content())

    def test_has_content_when_only_role_notes(self) -> None:
        ctx = NoteRenderContext(
            title="t",
            note_kind="knowledge-note",
            role_notes={"tech-lead": "rolling update 정리"},
        )
        self.assertTrue(ctx.has_content())

    def test_has_content_when_only_links(self) -> None:
        ctx = NoteRenderContext(
            title="t",
            note_kind="knowledge-note",
            links=("https://kubernetes.io/docs/",),
        )
        self.assertTrue(ctx.has_content())

    def test_whitespace_only_payload_does_not_count(self) -> None:
        ctx = NoteRenderContext(
            title="t",
            note_kind="knowledge-note",
            body_blocks=("   \n\n   ",),
            role_notes={"tech-lead": "   "},
            links=("   ",),
        )
        self.assertFalse(ctx.has_content())

    def test_canonical_and_folder_properties(self) -> None:
        ctx = NoteRenderContext(title="t", note_kind="knowledge-note")
        self.assertEqual(ctx.canonical_kind, KIND_KNOWLEDGE_NOTE)
        self.assertEqual(ctx.folder, FOLDER_KNOWLEDGE)
        self.assertTrue(ctx.requires_approval)

    def test_research_log_context_does_not_require_approval(self) -> None:
        ctx = NoteRenderContext(title="t", note_kind="research-log")
        self.assertFalse(ctx.requires_approval)
        self.assertEqual(ctx.folder, FOLDER_RESEARCH_LOG)


class RenderHelperBlockTests(unittest.TestCase):
    def test_render_links_block_dedups_and_strips(self) -> None:
        block = render_links_block([
            "https://a.example",
            "  https://a.example  ",
            "https://b.example",
            "",
            "  ",
        ])
        self.assertIn("- https://a.example", block)
        self.assertIn("- https://b.example", block)
        # Dedup — only one occurrence.
        self.assertEqual(block.count("https://a.example"), 1)

    def test_render_links_block_empty(self) -> None:
        self.assertEqual(render_links_block([]), "")
        self.assertEqual(render_links_block([""]), "")

    def test_render_role_notes_sorted(self) -> None:
        block = render_role_notes_block(
            {
                "tech-lead": "rolling update 합의",
                "devops-engineer": "노드 풀 분리",
                "qa-engineer": "",
            }
        )
        # Sorted ordering: devops-engineer < qa-engineer < tech-lead.
        idx_devops = block.find("### devops-engineer")
        idx_techlead = block.find("### tech-lead")
        self.assertGreater(idx_devops, -1)
        self.assertGreater(idx_techlead, idx_devops)
        # qa-engineer was dropped because its summary was empty.
        self.assertNotIn("### qa-engineer", block)

    def test_render_role_notes_block_empty(self) -> None:
        self.assertEqual(render_role_notes_block({}), "")
        self.assertEqual(render_role_notes_block({"x": ""}), "")

    def test_render_source_thread_block(self) -> None:
        block = render_source_thread_block(
            "https://discord.com/channels/1/2/3",
            title="k8s 운영 합의",
        )
        self.assertIn("URL: https://discord.com/channels/1/2/3", block)
        self.assertIn("제목: k8s 운영 합의", block)

    def test_render_source_thread_block_no_url(self) -> None:
        self.assertEqual(render_source_thread_block(None), "")
        self.assertEqual(render_source_thread_block(""), "")
        self.assertEqual(render_source_thread_block("   "), "")


# ---------------------------------------------------------------------------
# End-to-end through default_render_fn
# ---------------------------------------------------------------------------


class _RenderFixture(unittest.TestCase):
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


def _seed_session_with_pack(*, session_id: str) -> WorkflowSession:
    when = datetime.now(tz=timezone.utc)
    pack = {
        "title": "결제 인프라 자료",
        "session_id": session_id,
        "summary": "PG 응답 idempotency-key 패턴 정리",
        "sources": [
            {
                "title": "Stripe Idempotency",
                "url": "https://stripe.com/docs/idempotency",
                "kind": "doc",
            }
        ],
        "urls": ["https://stripe.com/docs/idempotency"],
        "findings": [],
    }
    session = WorkflowSession(
        session_id=session_id,
        prompt="결제 모듈 멱등성 흐름 정리",
        task_type="research",
        state=WorkflowState.IN_PROGRESS,
        created_at=when,
        updated_at=when,
        role_sequence=("tech-lead", "backend-engineer"),
        extra={
            "research_pack": pack,
            "active_research_roles": ["tech-lead", "backend-engineer"],
        },
    )
    save_session(session)
    return session


class M10aDefaultRenderRoutingTests(_RenderFixture):
    def test_knowledge_note_renders_with_approval_to_top_level(self) -> None:
        sid = "sess-knowledge-note-toplevel"
        _seed_session_with_pack(session_id=sid)
        request = ObsidianWriteRequest(
            session_id=sid,
            note_kind=NOTE_KIND_KNOWLEDGE_NOTE,
            title="결제 인프라 가이드",
            approval_id="apv-1",
            approved_by="masterway",
            approved_at="2026-05-08T10:00:00+00:00",
        )
        note = default_render_fn(request)
        # Routes to ``20-knowledge/`` not ``10-projects/<project>/knowledge/``.
        self.assertEqual(note.path.folder, "20-knowledge")
        self.assertGreater(len(note.content), 100)

    def test_decision_record_renders_with_approval_to_top_level(self) -> None:
        sid = "sess-decision-record-toplevel"
        _seed_session_with_pack(session_id=sid)
        request = ObsidianWriteRequest(
            session_id=sid,
            note_kind=NOTE_KIND_DECISION_RECORD,
            title="멱등성 키 적용 결정",
            approval_id="apv-2",
            approved_by="masterway",
            approved_at="2026-05-08T10:30:00+00:00",
        )
        note = default_render_fn(request)
        self.assertEqual(note.path.folder, "30-decisions")
        self.assertGreater(len(note.content), 50)

    def test_legacy_knowledge_kind_unchanged(self) -> None:
        # No regression — the legacy kind name still routes to the
        # project-nested folder so existing notes / tests stay stable.
        sid = "sess-knowledge-legacy"
        _seed_session_with_pack(session_id=sid)
        request = ObsidianWriteRequest(
            session_id=sid,
            note_kind=NOTE_KIND_KNOWLEDGE,
            title="legacy knowledge",
            approval_id="apv-3",
            approved_by="masterway",
            approved_at="2026-05-08T11:00:00+00:00",
        )
        note = default_render_fn(request)
        self.assertTrue(note.path.folder.startswith("10-projects/"))
        self.assertTrue(note.path.folder.endswith("/knowledge"))


class M10aWorkerApprovalGuardEndToEndTests(_RenderFixture):
    """Worker-level approval guard test: ``knowledge-note`` /
    ``decision-record`` writes that arrive without the approval triple
    must never render — process_job emits FAILED_RETRYABLE."""

    def _build_worker(self, *, write_calls: list):
        queue = JobQueue(db_path=self._db)
        heartbeats = HeartbeatStore(db_path=self._db)

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
        return queue, worker

    def test_knowledge_note_without_approval_blocked(self) -> None:
        sid = "sess-knowledge-note-guard"
        _seed_session_with_pack(session_id=sid)
        write_calls: List = []
        queue, worker = self._build_worker(write_calls=write_calls)
        request = ObsidianWriteRequest(
            session_id=sid,
            note_kind=NOTE_KIND_KNOWLEDGE_NOTE,
            title="가이드",
            # No approval triple.
        )
        outcome = _run(worker.run_one(request))
        self.assertEqual(outcome.skipped_reason, SKIPPED_APPROVAL_REQUIRED)
        self.assertEqual(write_calls, [])

    def test_decision_record_without_approval_blocked(self) -> None:
        sid = "sess-decision-record-guard"
        _seed_session_with_pack(session_id=sid)
        write_calls: List = []
        queue, worker = self._build_worker(write_calls=write_calls)
        request = ObsidianWriteRequest(
            session_id=sid,
            note_kind=NOTE_KIND_DECISION_RECORD,
            title="결정",
        )
        outcome = _run(worker.run_one(request))
        self.assertEqual(outcome.skipped_reason, SKIPPED_APPROVAL_REQUIRED)
        self.assertEqual(write_calls, [])

    def test_research_log_writes_without_approval(self) -> None:
        # research-log is L1/L2 — the M10a approval matrix lets it
        # write without an approval triple. The renderer needs at
        # least one content piece (snapshot / synthesis / pack /
        # prompt) so it doesn't write a hollow file.
        sid = "sess-research-log-auto"
        _seed_session_with_pack(session_id=sid)
        write_calls: List = []
        queue, worker = self._build_worker(write_calls=write_calls)
        request = ObsidianWriteRequest(
            session_id=sid,
            note_kind=NOTE_KIND_RESEARCH_LOG,
            title="auto research-log",
            metadata={
                "original_prompt": "auto-recorded prompt",
                "synthesis_text": "rolling update 합의",
                "links": ["https://stripe.com/docs/idempotency"],
                "source_thread_url": "https://discord.com/channels/1/2/3",
            },
        )
        outcome = _run(worker.run_one(request))
        self.assertIsNone(outcome.skipped_reason)
        self.assertEqual(outcome.job.state, JobState.SAVED)
        self.assertEqual(len(write_calls), 1)


class M10aResearchLogEmptyContentBlockedTests(_RenderFixture):
    """The renderer's empty-content guard refuses a research-log write
    when ``metadata`` carries no usable payload."""

    def test_empty_research_log_request_raises(self) -> None:
        sid = "sess-research-log-empty"
        _seed_session_with_pack(session_id=sid)
        request = ObsidianWriteRequest(
            session_id=sid,
            note_kind=NOTE_KIND_RESEARCH_LOG,
            title="empty",
            # No metadata fields → renderer must refuse.
        )
        with self.assertRaises(ObsidianRenderError) as ctx:
            default_render_fn(request)
        self.assertIn("research-log", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
