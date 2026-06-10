"""KnowledgeRetriever — role / axis / topic / freshness scoring."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.engineering_intelligence.models import (
    EngineeringKnowledgeItem,
    Importance,
    SourceAxis,
    SourceKind,
)
from yule_engineering.agents.engineering_intelligence.retrieval import (
    KnowledgeRecord,
    KnowledgeRetriever,
    label_for_signal,
    score_knowledge_record,
)
from yule_engineering.agents.engineering_intelligence.models import (
    KnowledgeShareScope,
)


def _now() -> datetime:
    return datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)


def _record(
    *,
    topic_key: str = "spring-auth",
    title: str = "Spring Security 인증 흐름",
    role: str = "backend-engineer",
    summary: str = "OAuth2 + Filter chain 정리",
    axes: tuple = (SourceAxis.API_SCHEMA_AUTH, SourceAxis.OFFICIAL_DOCS),
    rag_tags: tuple = ("spring", "auth", "oauth2"),
    importance: Importance = Importance.HIGH,
    collected_at: str = "2026-05-08T00:00:00Z",
    secondary_roles: tuple = (),
) -> KnowledgeRecord:
    return KnowledgeRecord(
        topic_key=topic_key,
        title=title,
        role=role,
        source_url=f"https://example.com/{topic_key}",
        source_name="Spring Docs",
        summary=summary,
        axes=axes,
        rag_tags=rag_tags,
        importance=importance,
        collected_at=collected_at,
        secondary_roles=secondary_roles,
    )


class ScoreKnowledgeRecordTests(unittest.TestCase):
    def test_role_primary_match_adds_three(self) -> None:
        match = score_knowledge_record(
            _record(),
            query="auth",
            role="engineering-agent/backend-engineer",
            now=_now(),
        )
        self.assertIn("role_primary_match", match.signals)
        # role match (3) + topic overlap on "auth" (1) + axis hint via
        # task_type=None (0) + importance HIGH (1) + freshness 1 day
        # ago (1) = 6.
        self.assertGreaterEqual(match.score, 6.0)

    def test_axis_hint_from_task_type_adds_two_per_overlap(self) -> None:
        match = score_knowledge_record(
            _record(),
            query=None,
            role="backend-engineer",
            task_type="backend-feature",
            now=_now(),
        )
        # axis_hints_for_task_type("backend-feature") returns
        # API_SCHEMA_AUTH, OFFICIAL_DOCS, SECURITY. Record covers
        # API_SCHEMA_AUTH and OFFICIAL_DOCS = 2 overlap × 2 = +4.
        signals_str = ",".join(match.signals)
        self.assertIn("axis_overlap:", signals_str)
        # Score components: role (3) + axis (4) + importance HIGH (1)
        # + freshness 7d (1) = 9. Verify score reflects axis bonus.
        self.assertGreaterEqual(match.score, 9.0)

    def test_topic_overlap_capped_at_three(self) -> None:
        # Stuff the query with many overlapping tokens — score must cap
        # the topic bonus at +3. Tokens must be ≥ 2 chars to count.
        match = score_knowledge_record(
            _record(rag_tags=("aa", "bb", "cc", "dd", "ee", "ff")),
            query="aa bb cc dd ee ff",
            role=None,
            task_type=None,
            now=_now(),
        )
        self.assertIn("topic_overlap:3", match.signals)

    def test_freshness_bonus_decays(self) -> None:
        recent = score_knowledge_record(
            _record(collected_at="2026-05-08T00:00:00Z"),
            query=None,
            role=None,
            task_type=None,
            now=_now(),
        )
        old = score_knowledge_record(
            _record(collected_at="2026-01-01T00:00:00Z"),
            query=None,
            role=None,
            task_type=None,
            now=_now(),
        )
        # Recent record should outscore the old one purely on freshness
        # since every other dimension matches.
        self.assertGreater(recent.score, old.score)
        self.assertIn("fresh_7d", recent.signals)
        self.assertNotIn("fresh_7d", old.signals)
        self.assertNotIn("fresh_30d", old.signals)

    def test_low_importance_penalised(self) -> None:
        match = score_knowledge_record(
            _record(importance=Importance.LOW),
            query=None,
            role=None,
            task_type=None,
            now=_now(),
        )
        self.assertIn("importance_low", match.signals)

    def test_empty_body_penalised(self) -> None:
        match = score_knowledge_record(
            _record(summary="", rag_tags=()),
            query=None,
            role=None,
            task_type=None,
            now=_now(),
        )
        self.assertIn("empty_body_penalty", match.signals)

    def test_secondary_role_counts(self) -> None:
        match = score_knowledge_record(
            _record(role="ai-engineer", secondary_roles=("backend-engineer",)),
            query=None,
            role="backend-engineer",
            task_type=None,
            now=_now(),
        )
        self.assertIn("role_secondary_match", match.signals)


class KnowledgeRetrieverTests(unittest.TestCase):
    def test_filters_below_min_score(self) -> None:
        retriever = KnowledgeRetriever(min_score=2.0, now=_now())
        candidates = [
            _record(role="backend-engineer", title="Spring auth"),  # score>>2
            _record(
                role="qa-engineer",
                title="random test note",
                axes=(),
                rag_tags=(),
                summary="",
                importance=Importance.LOW,
                collected_at="2026-01-01T00:00:00Z",
            ),
        ]
        picked = retriever(
            candidates=candidates,
            query="spring auth",
            role="backend-engineer",
            task_type="backend-feature",
        )
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0].topic_key, "spring-auth")

    def test_orders_by_score_desc_then_freshness(self) -> None:
        # Two records both passing min_score; the one with bigger axis
        # overlap should land first.
        retriever = KnowledgeRetriever(min_score=0.0, now=_now())
        big_axis = _record(
            topic_key="big-axis",
            axes=(SourceAxis.API_SCHEMA_AUTH, SourceAxis.OFFICIAL_DOCS),
        )
        small_axis = _record(
            topic_key="small-axis",
            axes=(SourceAxis.OFFICIAL_DOCS,),
        )
        picked = retriever(
            candidates=[small_axis, big_axis],
            query=None,
            role="backend-engineer",
            task_type="backend-feature",
        )
        self.assertEqual(picked[0].topic_key, "big-axis")

    def test_limit_caps_results(self) -> None:
        retriever = KnowledgeRetriever(min_score=0.0, now=_now())
        candidates = [
            _record(topic_key=f"t{i}", title=f"item {i}") for i in range(8)
        ]
        picked = retriever(
            candidates=candidates,
            query=None,
            role="backend-engineer",
            limit=3,
        )
        self.assertEqual(len(picked), 3)

    def test_with_signals_returns_scored_envelopes(self) -> None:
        retriever = KnowledgeRetriever(min_score=0.0, now=_now())
        matches = retriever.with_signals(
            candidates=[_record()],
            query="auth",
            role="backend-engineer",
            task_type="backend-feature",
        )
        self.assertEqual(len(matches), 1)
        self.assertGreater(matches[0].score, 0)
        self.assertTrue(matches[0].signals)


class RoleFeedProvenanceTests(unittest.TestCase):
    """matched_axes + relevance_reason explain *why* the row came back."""

    def test_matched_axes_carries_axis_overlap(self) -> None:
        match = score_knowledge_record(
            _record(),
            query=None,
            role="backend-engineer",
            task_type="backend-feature",
            now=_now(),
        )
        # backend-feature hint = (API_SCHEMA_AUTH, OFFICIAL_DOCS, SECURITY)
        # Record covers API_SCHEMA_AUTH + OFFICIAL_DOCS — both should
        # appear on matched_axes (sorted by axis value).
        self.assertEqual(
            match.matched_axes,
            (SourceAxis.API_SCHEMA_AUTH, SourceAxis.OFFICIAL_DOCS),
        )

    def test_matched_axes_empty_when_no_hint(self) -> None:
        match = score_knowledge_record(
            _record(),
            query="auth",
            role="backend-engineer",
            task_type=None,
            now=_now(),
        )
        self.assertEqual(match.matched_axes, ())

    def test_relevance_reason_summarises_role_and_axis(self) -> None:
        match = score_knowledge_record(
            _record(),
            query="auth",
            role="backend-engineer",
            task_type="backend-feature",
            now=_now(),
        )
        # The reason is a one-liner; must mention the role and the
        # axes that drove the score so a synthesizer can paste it.
        self.assertIn("role=backend-engineer", match.relevance_reason)
        self.assertIn("axes=", match.relevance_reason)
        self.assertIn("api_schema_auth", match.relevance_reason)

    def test_relevance_reason_empty_signals_falls_back_to_default(self) -> None:
        # An off-role record with no axis hint and no query → no
        # signals fired. The reason should collapse to a default
        # sentence rather than empty string so the dashboard always
        # has something to print.
        match = score_knowledge_record(
            _record(role="ai-engineer", axes=()),
            query=None,
            role=None,
            task_type=None,
            now=_now(),
        )
        # MEDIUM importance + freshness 1d ago = freshness hits → reason
        # carries that signal. Use a record with an old date + no
        # importance bonus to reach the truly empty path.
        bare = score_knowledge_record(
            _record(
                role="ai-engineer",
                axes=(),
                rag_tags=(),
                summary="something",
                importance=Importance.MEDIUM,
                collected_at="2024-01-01T00:00:00Z",
            ),
            query=None,
            role=None,
            task_type=None,
            now=_now(),
        )
        self.assertEqual(bare.relevance_reason, "no signal match")
        self.assertEqual(bare.matched_axes, ())


class CoercionTests(unittest.TestCase):
    def test_engineering_knowledge_item_coerces_to_record(self) -> None:
        item = EngineeringKnowledgeItem(
            item_id="x",
            topic_key="spring-auth",
            title="Spring Auth",
            role="backend-engineer",
            stack_tags=("spring",),
            source_name="Spring Docs",
            source_url="https://example.com/spring-auth",
            source_kind=SourceKind.DOCS,
            collected_at="2026-05-08T00:00:00Z",
            importance=Importance.HIGH,
            summary="OAuth2 정리",
            rag_tags=("spring", "auth"),
        )
        retriever = KnowledgeRetriever(min_score=0.0, now=_now())
        picked = retriever(
            candidates=[item],
            query="spring",
            role="backend-engineer",
            task_type="backend-feature",
        )
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0].topic_key, "spring-auth")

    def test_mapping_candidate_with_required_fields_coerces(self) -> None:
        retriever = KnowledgeRetriever(min_score=0.0, now=_now())
        picked = retriever(
            candidates=[
                {
                    "topic_key": "k",
                    "title": "T",
                    "role": "backend-engineer",
                    "axes": ["api_schema_auth"],
                    "importance": "high",
                    "summary": "x",
                    "rag_tags": ["auth"],
                    "collected_at": "2026-05-08T00:00:00Z",
                }
            ],
            query="auth",
            role="backend-engineer",
        )
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0].title, "T")
        self.assertEqual(picked[0].importance, Importance.HIGH)

    def test_mapping_missing_required_field_dropped(self) -> None:
        retriever = KnowledgeRetriever(min_score=0.0, now=_now())
        picked = retriever(
            candidates=[{"title": "no role"}],
            query=None,
            role=None,
        )
        self.assertEqual(picked, ())


class EvidenceLabelTests(unittest.TestCase):
    def test_known_signals_have_korean_labels(self) -> None:
        self.assertEqual(
            label_for_signal("role_primary_match"), "요청 역할과 정확히 일치"
        )
        self.assertEqual(
            label_for_signal("importance_critical"), "중요도 critical"
        )
        self.assertEqual(label_for_signal("fresh_7d"), "최근 7일 이내 수집")

    def test_axis_overlap_keeps_axis_names(self) -> None:
        label = label_for_signal("axis_overlap:api_schema_auth,official_docs")
        self.assertEqual(
            label, "task_type 축 일치 (api_schema_auth,official_docs)"
        )

    def test_topic_overlap_extracts_count(self) -> None:
        self.assertEqual(label_for_signal("topic_overlap:2"), "질문 토큰 겹침 (+2)")

    def test_unknown_signal_falls_through(self) -> None:
        label = label_for_signal("future_signal_xyz")
        self.assertTrue(label.startswith("기타: "))

    def test_match_evidence_labels_uses_known_labels(self) -> None:
        match = score_knowledge_record(
            _record(),
            query="auth",
            role="backend-engineer",
            task_type="backend-feature",
            now=_now(),
        )
        labels = match.evidence_labels()
        self.assertIn("요청 역할과 정확히 일치", labels)
        # axis 매칭이 발생했으면 사람이 읽을 수 있는 라벨로 풀어진다.
        self.assertTrue(
            any("task_type 축 일치" in lbl for lbl in labels),
            f"axis label missing in {labels}",
        )


class ShareScopePropagationTests(unittest.TestCase):
    def test_share_scope_default_public(self) -> None:
        rec = _record()
        self.assertEqual(rec.share_scope, KnowledgeShareScope.PUBLIC)
        payload = rec.to_payload()
        self.assertEqual(payload["share_scope"], "public")

    def test_mapping_share_scope_team_internal_coerces(self) -> None:
        retriever = KnowledgeRetriever(min_score=0.0, now=_now())
        picked = retriever(
            candidates=[
                {
                    "topic_key": "k",
                    "title": "T",
                    "role": "backend-engineer",
                    "share_scope": "team_internal",
                    "share_scope_reason": "private repo",
                }
            ],
            query=None,
            role="backend-engineer",
        )
        self.assertEqual(len(picked), 1)
        self.assertEqual(
            picked[0].share_scope, KnowledgeShareScope.TEAM_INTERNAL
        )
        self.assertEqual(picked[0].share_scope_reason, "private repo")

    def test_mapping_unknown_share_scope_falls_back_to_public(self) -> None:
        retriever = KnowledgeRetriever(min_score=0.0, now=_now())
        picked = retriever(
            candidates=[
                {
                    "topic_key": "k",
                    "title": "T",
                    "role": "backend-engineer",
                    "share_scope": "future_unknown_value",
                }
            ],
            query=None,
            role="backend-engineer",
        )
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0].share_scope, KnowledgeShareScope.PUBLIC)


if __name__ == "__main__":
    unittest.main()
