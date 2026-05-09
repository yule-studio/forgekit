"""Tests for ``yule_orchestrator.agents.discussion.context_pack``."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_orchestrator.agents.discussion import (
    CodeHint,
    ContextPack,
    ContextPackBuilder,
    GithubIssueRef,
    GithubPRRef,
    ObsidianNoteRef,
    RelevantMemorySelector,
    ThreadMessage,
)


class ContextPackBuilderTestCase(unittest.TestCase):
    def _session(self, **kwargs):
        defaults = dict(
            session_id="abc12345",
            task_type="backend-feature",
            write_requested=False,
            write_blocked_reason=None,
            extra={"research_pack": {"x": 1}},
        )
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_empty_builder_returns_pack_with_only_message(self) -> None:
        builder = ContextPackBuilder()
        pack = builder.build(message_text="hello world")
        self.assertEqual(pack.current_message, "hello world")
        self.assertIsNone(pack.session_id)
        self.assertEqual(pack.recent_thread, ())
        self.assertEqual(pack.related_issues, ())
        self.assertEqual(pack.relevant_notes, ())
        self.assertEqual(pack.blockers, ())

    def test_thread_loader_truncates_long_messages(self) -> None:
        long_msg = "ㄱ" * 500
        builder = ContextPackBuilder(
            thread_loader=lambda sid: [
                ThreadMessage(role="user", content=long_msg, posted_at="2026-05-09T10:00"),
                ThreadMessage(role="tech-lead", content="짧은 답변"),
            ],
            max_thread_message_chars=100,
        )
        pack = builder.build(
            message_text="후속 질문",
            session=self._session(),
        )
        self.assertEqual(len(pack.recent_thread), 2)
        self.assertLessEqual(len(pack.recent_thread[0].content), 100)
        self.assertTrue(pack.recent_thread[0].content.endswith("…"))
        self.assertIn("최근 thread 발화", pack.thread_summary or "")

    def test_thread_loader_accepts_mappings(self) -> None:
        builder = ContextPackBuilder(
            thread_loader=lambda sid: [
                {"role": "user", "content": "안녕"},
                {"role": "tech-lead", "content": "응 들어왔다"},
            ]
        )
        pack = builder.build(
            message_text="x",
            session=self._session(),
        )
        self.assertEqual(len(pack.recent_thread), 2)
        self.assertEqual(pack.recent_thread[0].role, "user")

    def test_seam_failure_is_captured_as_blocker(self) -> None:
        def crashing(query: str):
            raise RuntimeError("github offline")

        builder = ContextPackBuilder(
            issue_loader=crashing,
            pr_loader=crashing,
            note_loader=crashing,
            code_hint_loader=crashing,
        )
        pack = builder.build(message_text="hi")
        self.assertEqual(pack.related_issues, ())
        self.assertEqual(pack.related_prs, ())
        self.assertEqual(pack.relevant_notes, ())
        self.assertEqual(pack.code_hints, ())
        for needle in ("issue_loader", "pr_loader", "note_loader", "code_hint_loader"):
            self.assertTrue(
                any(needle in b for b in pack.blockers),
                f"missing blocker for {needle}: {pack.blockers}",
            )

    def test_memory_selector_filters_notes(self) -> None:
        notes = [
            ObsidianNoteRef(
                title="auth migration retro",
                summary="auth migration #42 had a token leak",
                tags=("backend-feature", "auth", "backend-engineer"),
                kind="retrospective",
            ),
            ObsidianNoteRef(
                title="diary",
                summary="lunch was good",
                tags=("personal",),
                kind="reference",
            ),
        ]
        builder = ContextPackBuilder(
            note_loader=lambda q: notes,
            memory_selector=RelevantMemorySelector(),
        )
        pack = builder.build(
            message_text="auth migration 어떻게 정리했지",
            session=self._session(task_type="backend-feature"),
            role_for_research="engineering-agent/backend-engineer",
        )
        self.assertEqual(len(pack.relevant_notes), 1)
        self.assertEqual(pack.relevant_notes[0].title, "auth migration retro")

    def test_role_profile_loader_summary_used(self) -> None:
        builder = ContextPackBuilder(
            role_profile_loader=lambda role: f"profile for {role}",
            role_research_profile_loader=lambda role: f"research for {role}",
        )
        pack = builder.build(
            message_text="hi",
            role_for_research="engineering-agent/qa-engineer",
        )
        self.assertEqual(pack.role_profile_summary, "profile for engineering-agent/qa-engineer")
        self.assertEqual(
            pack.role_research_profile_summary,
            "research for engineering-agent/qa-engineer",
        )

    def test_session_extra_summary_lists_known_keys(self) -> None:
        builder = ContextPackBuilder()
        pack = builder.build(
            message_text="hi",
            session=self._session(extra={
                "research_pack": {"x": 1},
                "coding_proposal": {"y": 2},
                "secret_token": "should-not-appear",
            }),
        )
        self.assertIsNotNone(pack.session_extra_summary)
        self.assertIn("research_pack", pack.session_extra_summary or "")
        self.assertIn("coding_proposal", pack.session_extra_summary or "")
        self.assertNotIn("secret_token", pack.session_extra_summary or "")
        self.assertNotIn("should-not-appear", pack.session_extra_summary or "")

    def test_as_dict_round_trip_keys(self) -> None:
        pack = ContextPack(
            current_message="hi",
            session_id="sid",
            related_issues=(GithubIssueRef(number=1, title="x"),),
            related_prs=(GithubPRRef(number=2, title="y"),),
            relevant_notes=(ObsidianNoteRef(title="n"),),
            code_hints=(CodeHint(path="src/a.py"),),
        )
        payload = pack.as_dict()
        self.assertEqual(payload["session_id"], "sid")
        self.assertEqual(payload["related_issues"][0]["number"], 1)
        self.assertEqual(payload["related_prs"][0]["number"], 2)
        self.assertEqual(payload["relevant_notes"][0]["title"], "n")
        self.assertEqual(payload["code_hints"][0]["path"], "src/a.py")


if __name__ == "__main__":
    unittest.main()
