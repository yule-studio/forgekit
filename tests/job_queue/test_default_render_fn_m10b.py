"""default_render_fn — A-M10b autonomous-execution kind tests.

Pin the new note kinds:

  * research-log / agent-ops / failure-postmortem /
    self-improvement-proposal / blog-draft can be rendered through
    ``default_render_fn`` without a research_pack on the session
    (the payload lives in ``request.metadata`` instead),
  * the approval guard (``_APPROVAL_REQUIRED_KINDS``) does NOT
    require an approval triple for these kinds — they are L1/L2
    autonomous actions per autonomy_policy,
  * empty-body guards reject hollow notes before vault write.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.obsidian_writer_worker import (
    NOTE_KIND_AGENT_OPS,
    NOTE_KIND_BLOG_DRAFT,
    NOTE_KIND_FAILURE_POSTMORTEM,
    NOTE_KIND_RESEARCH_LOG,
    NOTE_KIND_SELF_IMPROVEMENT_PROPOSAL,
    ObsidianRenderError,
    ObsidianWriteRequest,
    default_render_fn,
)
from yule_engineering.agents.obsidian.research_log_writer import (
    render_research_log_note,
)


# ---------------------------------------------------------------------------
# research-log
# ---------------------------------------------------------------------------


class ResearchLogRenderTests(unittest.TestCase):
    def test_renders_when_thread_snapshot_present(self) -> None:
        request = ObsidianWriteRequest(
            session_id="sess-1",
            note_kind=NOTE_KIND_RESEARCH_LOG,
            title="DevOps 학습 로드맵",
            project="yule-studio-agent",
            metadata={
                "original_prompt": "DevOps 엔지니어가 되려면 어떤 걸 공부해야 돼?",
                "thread_snapshot": {
                    "messages": [
                        {
                            "author": "masterway",
                            "content": "k8s 자료 같이 보자",
                        }
                    ],
                    "extracted_links": ["https://kubernetes.io/docs/"],
                    "role_summaries": {
                        "devops-engineer": "rolling update 정책 정리",
                    },
                },
                "selected_roles": ["tech-lead", "devops-engineer"],
                "topic_key": "devops-roadmap-12345",
                "source_thread_url": "https://discord.com/channels/1/2/3",
            },
        )
        note = default_render_fn(request)
        self.assertEqual(note.path.folder.endswith("/research-log"), True)
        self.assertIn("DevOps", note.content)
        self.assertIn("원문 요청", note.content)
        self.assertIn("DevOps 엔지니어가 되려면", note.content)
        self.assertIn("kubernetes.io", note.content)
        self.assertIn("자동 기록 안내", note.content)
        self.assertEqual(note.frontmatter["kind"], "research-log")
        self.assertEqual(
            note.frontmatter["topic_key"], "devops-roadmap-12345"
        )

    def test_renders_when_only_synthesis_present(self) -> None:
        request = ObsidianWriteRequest(
            session_id="sess-1",
            note_kind=NOTE_KIND_RESEARCH_LOG,
            title="간단 합의",
            metadata={
                "synthesis_text": "rolling update + canary 정책으로 합의",
            },
        )
        note = default_render_fn(request)
        self.assertIn("tech-lead 합의", note.content)
        self.assertIn("rolling update", note.content)

    def test_renders_when_only_research_pack_present(self) -> None:
        request = ObsidianWriteRequest(
            session_id="sess-1",
            note_kind=NOTE_KIND_RESEARCH_LOG,
            title="간단 pack",
            metadata={
                "research_pack": {
                    "title": "k8s 자료",
                    "summary": "k8s rolling update 자료 모음",
                    "urls": ["https://kubernetes.io/docs/"],
                },
            },
        )
        note = default_render_fn(request)
        self.assertIn("리서치 요약", note.content)
        self.assertIn("rolling update", note.content)
        self.assertIn("https://kubernetes.io/docs/", note.content)

    def test_empty_metadata_raises(self) -> None:
        request = ObsidianWriteRequest(
            session_id="sess-1",
            note_kind=NOTE_KIND_RESEARCH_LOG,
            title="빈 노트",
            metadata={},
        )
        with self.assertRaises(ObsidianRenderError) as ctx:
            default_render_fn(request)
        self.assertIn("hydration", str(ctx.exception))

    def test_writes_without_session_lookup(self) -> None:
        # Producer for research-log must not depend on a still-open
        # workflow_state row — the rendering path must work with
        # only metadata in hand.
        request = ObsidianWriteRequest(
            session_id="sess-does-not-exist",
            note_kind=NOTE_KIND_RESEARCH_LOG,
            title="standalone log",
            metadata={
                "original_prompt": "일정 정리해줘",
            },
        )
        note = default_render_fn(request)
        self.assertEqual(note.frontmatter["kind"], "research-log")


# ---------------------------------------------------------------------------
# agent-ops
# ---------------------------------------------------------------------------


class AgentOpsRenderTests(unittest.TestCase):
    def test_renders_audit_entries_into_log(self) -> None:
        from yule_engineering.agents.lifecycle.agent_ops_log import (
            AgentOpsEntry,
        )

        entry = AgentOpsEntry(
            entry_id="evt-1",
            session_id="sess-1",
            action="forum_handoff_decision",
            autonomy_level="L1_AUTO_RECORD_REQUIRED",
            summary="중복 카드 차단",
            reasoning="L1 — forum handoff 결정 audit",
            outcome="skipped:topic_already_saved",
            recorded_at="2026-05-08T10:30:00+00:00",
        )
        request = ObsidianWriteRequest(
            session_id="sess-1",
            note_kind=NOTE_KIND_AGENT_OPS,
            title="agent-ops 2026-05-08",
            project="yule-studio-agent",
            metadata={
                "audit_entries": [entry.to_payload()],
            },
        )
        note = default_render_fn(request)
        self.assertTrue(note.path.folder.endswith("/agent-ops"))
        self.assertIn("L1_AUTO_RECORD_REQUIRED", note.content)
        self.assertIn("forum_handoff_decision", note.content)
        self.assertIn("skipped:topic_already_saved", note.content)
        self.assertEqual(note.frontmatter["entry_count"], 1)

    def test_empty_audit_list_raises(self) -> None:
        request = ObsidianWriteRequest(
            session_id="s",
            note_kind=NOTE_KIND_AGENT_OPS,
            title="empty",
            metadata={"audit_entries": []},
        )
        with self.assertRaises(ObsidianRenderError):
            default_render_fn(request)


# ---------------------------------------------------------------------------
# postmortem / proposal / blog-draft (simple-body kinds)
# ---------------------------------------------------------------------------


class SimpleBodyKindTests(unittest.TestCase):
    def _request(self, kind: str, *, body: str) -> ObsidianWriteRequest:
        return ObsidianWriteRequest(
            session_id="s",
            note_kind=kind,
            title="root cause",
            project="yule-studio-agent",
            metadata={"body": body},
        )

    def test_postmortem_lands_in_postmortems_subdir(self) -> None:
        note = default_render_fn(
            self._request(
                NOTE_KIND_FAILURE_POSTMORTEM,
                body="원인: ApprovalWorker 가 raise 함",
            )
        )
        self.assertIn("postmortems", note.path.folder)
        self.assertIn("원인", note.content)

    def test_proposal_lands_in_proposals_subdir(self) -> None:
        note = default_render_fn(
            self._request(
                NOTE_KIND_SELF_IMPROVEMENT_PROPOSAL,
                body="제안: dedup 로그를 vault 에 자동 export",
            )
        )
        self.assertIn("proposals", note.path.folder)
        self.assertIn("제안", note.content)

    def test_blog_draft_lands_in_blog_drafts_subdir(self) -> None:
        note = default_render_fn(
            self._request(
                NOTE_KIND_BLOG_DRAFT,
                body="# 직원형 에이전트 만들기\n\n초안",
            )
        )
        self.assertIn("blog-drafts", note.path.folder)
        self.assertIn("초안", note.content)

    def test_empty_body_raises(self) -> None:
        request = self._request(NOTE_KIND_FAILURE_POSTMORTEM, body="")
        with self.assertRaises(ObsidianRenderError):
            default_render_fn(request)


# ---------------------------------------------------------------------------
# Approval bypass — none of the M10b kinds require an approval triple
# ---------------------------------------------------------------------------


class ApprovalBypassTests(unittest.TestCase):
    """The worker's approval guard is keyed off
    ``ObsidianWriteRequest.requires_approval``. M10b kinds must
    return False so they auto-write without an approval row.
    """

    def test_research_log_does_not_require_approval(self) -> None:
        request = ObsidianWriteRequest(
            session_id="s",
            note_kind=NOTE_KIND_RESEARCH_LOG,
            title="t",
        )
        self.assertFalse(request.requires_approval())

    def test_agent_ops_does_not_require_approval(self) -> None:
        request = ObsidianWriteRequest(
            session_id="s",
            note_kind=NOTE_KIND_AGENT_OPS,
            title="t",
        )
        self.assertFalse(request.requires_approval())

    def test_failure_postmortem_does_not_require_approval(self) -> None:
        request = ObsidianWriteRequest(
            session_id="s",
            note_kind=NOTE_KIND_FAILURE_POSTMORTEM,
            title="t",
        )
        self.assertFalse(request.requires_approval())

    def test_self_improvement_proposal_does_not_require_approval(self) -> None:
        request = ObsidianWriteRequest(
            session_id="s",
            note_kind=NOTE_KIND_SELF_IMPROVEMENT_PROPOSAL,
            title="t",
        )
        self.assertFalse(request.requires_approval())

    def test_blog_draft_does_not_require_approval(self) -> None:
        request = ObsidianWriteRequest(
            session_id="s",
            note_kind=NOTE_KIND_BLOG_DRAFT,
            title="t",
        )
        self.assertFalse(request.requires_approval())


if __name__ == "__main__":
    unittest.main()
