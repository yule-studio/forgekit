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


class ContextPackEvidenceSurfaceTestCase(unittest.TestCase):
    """``relevant_knowledge`` → 사람이 읽을 수 있는 evidence 블록."""

    def _public_ref(self, **overrides) -> EngineeringKnowledgeRef:
        base = dict(
            title="Spring Security 인증 흐름",
            role="backend-engineer",
            topic_key="spring-auth",
            source_url="https://example.com/spring-auth",
            source_name="Spring Docs",
            summary="OAuth2 + Filter chain 정리",
            score=8.0,
            signals=("role_primary_match", "axis_overlap:api_schema_auth"),
            evidence_labels=(
                "요청 역할과 정확히 일치",
                "task_type 축 일치 (api_schema_auth)",
            ),
            share_scope="public",
        )
        base.update(overrides)
        return EngineeringKnowledgeRef(**base)

    def test_empty_relevant_knowledge_returns_empty_string(self) -> None:
        pack = ContextPack(current_message="hi")
        self.assertEqual(pack.format_knowledge_evidence_block(), "")

    def test_public_evidence_includes_summary_and_signals(self) -> None:
        pack = ContextPack(
            current_message="auth",
            relevant_knowledge=(self._public_ref(),),
        )
        block = pack.format_knowledge_evidence_block()
        self.assertIn("근거 자료", block)
        self.assertIn("Spring Security 인증 흐름", block)
        self.assertIn("OAuth2 + Filter chain 정리", block)
        # 사람이 읽는 evidence 라벨이 그대로 노출된다.
        self.assertIn("요청 역할과 정확히 일치", block)
        self.assertIn("task_type 축 일치", block)
        self.assertIn("score=8.0", block)

    def test_team_internal_evidence_drops_summary(self) -> None:
        pack = ContextPack(
            current_message="auth",
            relevant_knowledge=(
                self._public_ref(
                    share_scope="team_internal",
                    summary="이 요약은 외부 surface 에 노출되면 안 된다",
                ),
            ),
        )
        block = pack.format_knowledge_evidence_block()
        self.assertIn("Spring Security 인증 흐름", block)
        self.assertIn("team-internal", block)
        self.assertNotIn("외부 surface 에 노출되면 안 된다", block)

    def test_restricted_evidence_redacts_title_and_url(self) -> None:
        pack = ContextPack(
            current_message="incident",
            relevant_knowledge=(
                self._public_ref(
                    share_scope="restricted",
                    share_scope_reason="customer PII",
                    title="2026-04-29 customer data leak",
                    source_url="https://internal.example.com/incidents/2026-04-29",
                    summary="민감 본문",
                ),
            ),
        )
        block = pack.format_knowledge_evidence_block()
        # 제목/URL/요약 어떤 것도 외부에 옮겨지지 않는다.
        self.assertNotIn("customer data leak", block)
        self.assertNotIn("internal.example.com/incidents", block)
        self.assertNotIn("민감 본문", block)
        self.assertIn("공개 제한된 자료", block)
        self.assertIn("customer PII", block)

    def test_max_items_caps_block_length(self) -> None:
        refs = tuple(
            self._public_ref(topic_key=f"k{i}", title=f"item {i}")
            for i in range(8)
        )
        pack = ContextPack(current_message="x", relevant_knowledge=refs)
        block = pack.format_knowledge_evidence_block(max_items=3)
        for visible in ("item 0", "item 1", "item 2"):
            self.assertIn(visible, block)
        self.assertNotIn("item 5", block)

    def test_as_dict_carries_share_scope_and_evidence_labels(self) -> None:
        pack = ContextPack(
            current_message="auth",
            relevant_knowledge=(self._public_ref(share_scope="team_internal"),),
        )
        payload = pack.as_dict()
        knowledge = payload["relevant_knowledge"][0]
        self.assertEqual(knowledge["share_scope"], "team_internal")
        self.assertEqual(knowledge["score"], 8.0)
        self.assertIn("요청 역할과 정확히 일치", knowledge["evidence_labels"])


class ContextPackShortSummaryTestCase(unittest.TestCase):
    """`knowledge_short_summary` + `share_boundary_breakdown` 회귀 가드."""

    def _ref(self, **overrides) -> EngineeringKnowledgeRef:
        base = dict(
            title="Spring Security 인증 흐름",
            role="backend-engineer",
            topic_key="spring-auth",
            source_url="https://example.com/spring-auth",
            source_name="Spring Docs",
            summary="OAuth2 정리",
            share_scope="public",
        )
        base.update(overrides)
        return EngineeringKnowledgeRef(**base)

    def test_empty_pack_returns_empty_summary_and_zero_breakdown(self) -> None:
        pack = ContextPack(current_message="hi")
        self.assertEqual(pack.knowledge_short_summary(), "")
        breakdown = pack.share_boundary_breakdown()
        self.assertEqual(breakdown["public"], 0)
        self.assertEqual(breakdown["team_internal"], 0)
        self.assertEqual(breakdown["restricted"], 0)
        self.assertEqual(breakdown["total"], 0)

    def test_summary_lists_top_titles_and_scope_counts(self) -> None:
        pack = ContextPack(
            current_message="auth",
            relevant_knowledge=(
                self._ref(),
                self._ref(
                    title="사내 OAuth playbook",
                    topic_key="company-oauth",
                    share_scope="team_internal",
                ),
                self._ref(
                    title="incident-2026-04-29",
                    topic_key="incident-2026-04-29",
                    share_scope="restricted",
                    share_scope_reason="PII",
                ),
            ),
        )
        summary = pack.knowledge_short_summary()
        self.assertIn("근거 자료 3건", summary)
        self.assertIn("public 1", summary)
        self.assertIn("team_internal 1", summary)
        self.assertIn("restricted 1", summary)
        # 상위 2 건의 제목이 들어가지만 restricted 의 제목/url 은 절대 새지 않는다.
        self.assertIn("Spring Security 인증 흐름", summary)
        self.assertIn("사내 OAuth playbook", summary)
        self.assertIn("team-internal", summary)
        self.assertNotIn("incident-2026-04-29", summary)

    def test_summary_max_topics_caps_preview(self) -> None:
        refs = tuple(
            self._ref(topic_key=f"k{i}", title=f"item {i}") for i in range(4)
        )
        pack = ContextPack(current_message="x", relevant_knowledge=refs)
        summary = pack.knowledge_short_summary(max_topics=1)
        self.assertIn("외 3건", summary)
        self.assertIn("item 0", summary)
        self.assertNotIn("item 1", summary)


class ContextPackKnowledgeMatchUnwrapTestCase(unittest.TestCase):
    """``KnowledgeRetriever.with_signals`` 가 ContextPack 까지 흘러들어와야 한다."""

    def test_with_signals_path_carries_score_and_labels(self) -> None:
        candidates = [
            KnowledgeRecord(
                topic_key="spring-auth",
                title="Spring 인증",
                role="backend-engineer",
                source_url="https://example.com/spring-auth",
                source_name="Spring Docs",
                summary="OAuth2 흐름",
                axes=(SourceAxis.API_SCHEMA_AUTH,),
                rag_tags=("auth",),
                collected_at="2026-05-08T00:00:00Z",
            ),
        ]
        builder = ContextPackBuilder(
            knowledge_loader=lambda q: candidates,
            knowledge_retriever=KnowledgeRetriever(min_score=0.0),
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
        self.assertEqual(len(pack.relevant_knowledge), 1)
        ref = pack.relevant_knowledge[0]
        # score 와 signals 가 ContextPack 으로 전달되어 surface 가 가능
        self.assertIsNotNone(ref.score)
        self.assertGreater(ref.score, 0.0)
        self.assertTrue(ref.signals, msg="signals were not propagated")
        # evidence_labels 도 ContextPack 까지 살아 들어온다 (한국어 라벨).
        self.assertTrue(ref.evidence_labels)


if __name__ == "__main__":
    unittest.main()
