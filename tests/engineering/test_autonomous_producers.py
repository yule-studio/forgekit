"""autonomous_producers — A-M10b builder helpers.

Pin the request composers used by future M10c triggers:

  * research-log builds with snapshot + session.extra hydration,
  * agent-ops reads from session.extra by default,
  * simple-body kinds carry the producer-authored markdown body,
  * round-trip through ``default_render_fn`` produces a non-empty
    note (no double-renderer divergence).
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.obsidian_writer_worker import (
    NOTE_KIND_AGENT_OPS,
    NOTE_KIND_FAILURE_POSTMORTEM,
    NOTE_KIND_RESEARCH_LOG,
    default_render_fn,
)
from yule_orchestrator.agents.lifecycle.agent_ops_log import (
    AgentOpsEntry,
    SESSION_EXTRA_KEY,
)
from yule_orchestrator.agents.lifecycle.autonomous_producers import (
    build_agent_ops_request,
    build_research_log_request,
    build_simple_body_request,
)
from yule_orchestrator.agents.lifecycle.thread_snapshot import (
    ThreadMessage,
    ThreadSnapshot,
)


def _session(**overrides):
    base = {
        "session_id": "sess-prod-1",
        "prompt": "DevOps 학습 로드맵",
        "thread_id": None,
        "extra": {
            "research_forum_thread_id": 5001,
            "research_pack": {
                "title": "DevOps 자료",
                "summary": "k8s + CI/CD 자료 모음",
                "urls": ["https://kubernetes.io/docs/"],
            },
            "research_synthesis_text": "rolling update + canary 정책으로 합의",
        },
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# research-log
# ---------------------------------------------------------------------------


class ResearchLogProducerTests(unittest.TestCase):
    def test_request_carries_session_metadata(self) -> None:
        snapshot = ThreadSnapshot(
            messages=(ThreadMessage(author="masterway", content="합의"),),
            extracted_links=("https://k8s.io/docs/",),
        )
        request = build_research_log_request(
            session=_session(),
            snapshot=snapshot,
            canonical_title="DevOps 로드맵",
            topic_key="devops-roadmap-5001",
            source_thread_url="https://discord.com/channels/1/2/3",
            selected_roles=("tech-lead", "devops-engineer"),
        )
        self.assertEqual(request.note_kind, NOTE_KIND_RESEARCH_LOG)
        self.assertEqual(request.title, "DevOps 로드맵")
        self.assertEqual(request.source_thread_id, 5001)
        self.assertEqual(
            request.metadata["topic_key"], "devops-roadmap-5001"
        )
        self.assertEqual(
            request.metadata["selected_roles"], ["tech-lead", "devops-engineer"]
        )
        self.assertIn("messages", request.metadata["thread_snapshot"])
        self.assertEqual(
            request.metadata["synthesis_text"],
            "rolling update + canary 정책으로 합의",
        )
        # Round-trip through the renderer must succeed (produces
        # non-empty body since snapshot + synthesis + pack are all
        # present).
        note = default_render_fn(request)
        self.assertIn("DevOps", note.content)
        self.assertIn("합의", note.content)

    def test_falls_back_to_synthesis_when_no_snapshot(self) -> None:
        request = build_research_log_request(session=_session())
        note = default_render_fn(request)
        self.assertIn("rolling update", note.content)


# ---------------------------------------------------------------------------
# agent-ops
# ---------------------------------------------------------------------------


class AgentOpsProducerTests(unittest.TestCase):
    def _entry(self, *, idx: int = 1) -> AgentOpsEntry:
        return AgentOpsEntry(
            entry_id=f"evt-{idx}",
            session_id="sess-prod-1",
            action="forum_handoff_decision",
            autonomy_level="L1_AUTO_RECORD_REQUIRED",
            summary=f"audit {idx}",
            reasoning="L1 — forum handoff 결정 audit",
            outcome="skipped:topic_already_saved",
            recorded_at="2026-05-08T10:00:00+00:00",
        )

    def test_reads_audit_entries_from_session_extra(self) -> None:
        session = _session(
            extra={SESSION_EXTRA_KEY: [self._entry(idx=1).to_payload()]}
        )
        request = build_agent_ops_request(session=session)
        self.assertEqual(request.note_kind, NOTE_KIND_AGENT_OPS)
        self.assertEqual(len(request.metadata["audit_entries"]), 1)
        # Round-trip through renderer.
        note = default_render_fn(request)
        self.assertIn("L1_AUTO_RECORD_REQUIRED", note.content)

    def test_explicit_audit_entries_override_session(self) -> None:
        session = _session(extra={SESSION_EXTRA_KEY: []})
        request = build_agent_ops_request(
            session=session,
            audit_entries=[self._entry(idx=2)],
        )
        self.assertEqual(len(request.metadata["audit_entries"]), 1)


# ---------------------------------------------------------------------------
# simple-body kinds
# ---------------------------------------------------------------------------


class SimpleBodyProducerTests(unittest.TestCase):
    def test_postmortem_request_renders(self) -> None:
        request = build_simple_body_request(
            session=_session(),
            note_kind=NOTE_KIND_FAILURE_POSTMORTEM,
            title="ApprovalWorker 예외 postmortem",
            body="원인: post_fn 이 raise 함\n\n조치: 재시도 정책 추가.",
        )
        self.assertEqual(request.metadata["body"].startswith("원인"), True)
        note = default_render_fn(request)
        self.assertIn("postmortems", note.path.folder)
        self.assertIn("원인", note.content)


if __name__ == "__main__":
    unittest.main()
