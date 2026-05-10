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
    EngineeringKnowledgeRef,
    GithubIssueRef,
    GithubPRRef,
    ObsidianNoteRef,
    RelevantMemorySelector,
    ThreadMessage,
)
from yule_orchestrator.agents.engineering_intelligence import (
    KnowledgeRecord,
    KnowledgeRetriever,
    SourceAxis,
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
            relevant_knowledge=(
                EngineeringKnowledgeRef(
                    title="Spring auth",
                    role="backend-engineer",
                    topic_key="spring-auth",
                    axes=("api_schema_auth",),
                    rag_tags=("auth",),
                ),
            ),
            code_hints=(CodeHint(path="src/a.py"),),
        )
        payload = pack.as_dict()
        self.assertEqual(payload["session_id"], "sid")
        self.assertEqual(payload["related_issues"][0]["number"], 1)
        self.assertEqual(payload["related_prs"][0]["number"], 2)
        self.assertEqual(payload["relevant_notes"][0]["title"], "n")
        self.assertEqual(payload["code_hints"][0]["path"], "src/a.py")
        self.assertEqual(payload["relevant_knowledge"][0]["topic_key"], "spring-auth")
        self.assertEqual(payload["relevant_knowledge"][0]["axes"], ["api_schema_auth"])


class ContextPackKnowledgeIntegrationTestCase(unittest.TestCase):
    """`engineering_intelligence` retrieval ↔ ContextPackBuilder wiring."""

    def _record(
        self,
        topic_key: str,
        title: str,
        role: str = "backend-engineer",
        axes: tuple = (SourceAxis.API_SCHEMA_AUTH,),
    ) -> KnowledgeRecord:
        return KnowledgeRecord(
            topic_key=topic_key,
            title=title,
            role=role,
            source_url=f"https://example.com/{topic_key}",
            source_name="Spring Docs",
            summary=f"summary for {topic_key}",
            axes=axes,
            rag_tags=("spring", "auth"),
            collected_at="2026-05-08T00:00:00Z",
        )

    def test_knowledge_loader_results_appear_in_pack(self) -> None:
        candidates = [
            self._record("spring-auth", "Spring 인증"),
            self._record(
                "qa-cypress",
                "Cypress 회귀",
                role="qa-engineer",
                axes=(SourceAxis.REGRESSION_TEST_PLAN,),
            ),
        ]
        builder = ContextPackBuilder(
            knowledge_loader=lambda q: candidates,
            knowledge_retriever=KnowledgeRetriever(min_score=0.5),
            max_knowledge=5,
        )
        pack = builder.build(
            message_text="spring auth 흐름이 어떻게 되지",
            session=SimpleNamespace(
                session_id="abc",
                task_type="backend-feature",
                write_requested=False,
                write_blocked_reason=None,
                extra={},
            ),
            role_for_research="engineering-agent/backend-engineer",
        )
        self.assertGreater(len(pack.relevant_knowledge), 0)
        # Backend-feature task + backend-engineer role → spring-auth
        # should outscore the qa-engineer record.
        self.assertEqual(pack.relevant_knowledge[0].topic_key, "spring-auth")

    def test_knowledge_retriever_omitted_uses_role_first_fallback(self) -> None:
        candidates = [
            self._record("qa-cypress", "Cypress", role="qa-engineer"),
            self._record("spring-auth", "Spring 인증"),
        ]
        builder = ContextPackBuilder(
            knowledge_loader=lambda q: candidates,
            max_knowledge=5,
        )
        pack = builder.build(
            message_text="auth",
            role_for_research="backend-engineer",
        )
        # Same-role record (spring-auth) should land before the
        # qa-engineer one even without a retriever.
        self.assertEqual(pack.relevant_knowledge[0].topic_key, "spring-auth")
        self.assertEqual(pack.relevant_knowledge[1].topic_key, "qa-cypress")

    def test_knowledge_loader_failure_records_blocker(self) -> None:
        def boom(query: str):
            raise RuntimeError("vault offline")

        builder = ContextPackBuilder(knowledge_loader=boom)
        pack = builder.build(message_text="hi")
        self.assertEqual(pack.relevant_knowledge, ())
        self.assertTrue(
            any("knowledge_loader" in b for b in pack.blockers),
            f"expected knowledge_loader blocker; got {pack.blockers}",
        )

    def test_knowledge_loader_capped_by_max_knowledge(self) -> None:
        candidates = [
            self._record(f"k{i}", f"item {i}") for i in range(10)
        ]
        builder = ContextPackBuilder(
            knowledge_loader=lambda q: candidates,
            knowledge_retriever=KnowledgeRetriever(min_score=0.0),
            max_knowledge=3,
        )
        pack = builder.build(
            message_text="hi",
            role_for_research="backend-engineer",
        )
        self.assertEqual(len(pack.relevant_knowledge), 3)

    def test_role_feed_provenance_lands_on_ref(self) -> None:
        # The retriever should hand back KnowledgeMatch envelopes via
        # with_signals; the builder unpacks them so the role-feed
        # provenance (matched_axes, relevance_reason, signals, score)
        # surfaces on the EngineeringKnowledgeRef and survives as_dict.
        candidates = [
            self._record(
                "spring-auth",
                "Spring 인증",
                axes=(SourceAxis.API_SCHEMA_AUTH, SourceAxis.OFFICIAL_DOCS),
            ),
        ]
        builder = ContextPackBuilder(
            knowledge_loader=lambda q: candidates,
            knowledge_retriever=KnowledgeRetriever(min_score=0.0),
            max_knowledge=5,
        )
        pack = builder.build(
            message_text="auth flow",
            session=SimpleNamespace(
                session_id="abc",
                task_type="backend-feature",
                write_requested=False,
                write_blocked_reason=None,
                extra={},
            ),
            role_for_research="engineering-agent/backend-engineer",
        )
        self.assertEqual(len(pack.relevant_knowledge), 1)
        ref = pack.relevant_knowledge[0]
        self.assertIn("api_schema_auth", ref.matched_axes)
        self.assertIn("official_docs", ref.matched_axes)
        self.assertTrue(ref.relevance_reason)
        self.assertIn("role=backend-engineer", ref.relevance_reason or "")
        self.assertGreater(ref.score or 0, 0)
        self.assertTrue(ref.signals)
        # as_dict surface
        payload = pack.as_dict()
        first = payload["relevant_knowledge"][0]
        self.assertEqual(
            first["matched_axes"], ["api_schema_auth", "official_docs"]
        )
        self.assertIn("role=backend-engineer", first["relevance_reason"])

    def test_provenance_passthrough_in_dict_candidates(self) -> None:
        # Vault rows already carrying the provenance fields (because
        # they were scored upstream and persisted) should round-trip
        # through the builder without the retriever wiping them.
        candidates = [
            {
                "topic_key": "spring-auth",
                "title": "Spring 인증",
                "role": "backend-engineer",
                "summary": "OAuth2 정리",
                "axes": ["api_schema_auth"],
                "rag_tags": ["auth"],
                "collected_at": "2026-05-08T00:00:00Z",
                "matched_axes": ["api_schema_auth"],
                "relevance_reason": "role=backend-engineer; axes=api_schema_auth",
                "signals": ["role_primary_match", "axis_overlap:api_schema_auth"],
                "score": 5.5,
            }
        ]
        builder = ContextPackBuilder(
            knowledge_loader=lambda q: candidates,
        )
        pack = builder.build(
            message_text="auth",
            role_for_research="backend-engineer",
        )
        self.assertEqual(len(pack.relevant_knowledge), 1)
        ref = pack.relevant_knowledge[0]
        self.assertEqual(ref.matched_axes, ("api_schema_auth",))
        self.assertEqual(
            ref.relevance_reason,
            "role=backend-engineer; axes=api_schema_auth",
        )
        self.assertEqual(ref.score, 5.5)
        self.assertEqual(
            ref.signals,
            ("role_primary_match", "axis_overlap:api_schema_auth"),
        )

    def test_dict_candidates_coerce(self) -> None:
        candidates = [
            {
                "topic_key": "spring-auth",
                "title": "Spring 인증",
                "role": "backend-engineer",
                "summary": "OAuth2 정리",
                "axes": ["api_schema_auth"],
                "rag_tags": ["auth"],
                "collected_at": "2026-05-08T00:00:00Z",
                "importance": "high",
            }
        ]
        builder = ContextPackBuilder(
            knowledge_loader=lambda q: candidates,
            knowledge_retriever=KnowledgeRetriever(min_score=0.0),
        )
        pack = builder.build(
            message_text="spring",
            role_for_research="backend-engineer",
        )
        self.assertEqual(len(pack.relevant_knowledge), 1)
        self.assertEqual(pack.relevant_knowledge[0].topic_key, "spring-auth")
        self.assertEqual(
            pack.relevant_knowledge[0].axes,
            ("api_schema_auth",),
        )


if __name__ == "__main__":
    unittest.main()
