"""Share boundary — renderer / obsidian / discord_summary contract.

새 ``KnowledgeShareScope`` 가 note/report formatting 전반에 어떻게
반영되는지 한 번에 본다. 한 자료 항목이 share_scope 만 다르게 들어
왔을 때 surface 별로 어떻게 다르게 노출되는지가 핵심 회귀 보호.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.engineering_intelligence.discord_summary import (
    render_daily_role_summary,
)
from yule_engineering.agents.engineering_intelligence.models import (
    Audience,
    CagContext,
    EngineeringKnowledgeItem,
    Importance,
    KnowledgeShareScope,
    LearningLevel,
    PracticeVerification,
    SourceKind,
)
from yule_engineering.agents.engineering_intelligence.obsidian import (
    build_engineering_knowledge_write_request,
    evaluate_quality_gate,
    shareable_external_payload,
    vault_only_metadata,
)
from yule_engineering.agents.engineering_intelligence.renderer import (
    RendererError,
    render_engineering_knowledge_note,
    required_sections,
)


def _item(**overrides) -> EngineeringKnowledgeItem:
    base = dict(
        item_id="incident-2026-04-29-customer-data-leak",
        topic_key="customer-data-leak-2026-04-29",
        title="2026-04-29 고객 데이터 노출 사고 — 내부 분석",
        role="backend-engineer",
        stack_tags=("incident",),
        source_name="Internal Postmortem",
        source_url="https://internal.example.com/incidents/2026-04-29",
        source_kind=SourceKind.SECURITY_ADVISORY,
        collected_at="2026-04-30T01:00:00Z",
        importance=Importance.CRITICAL,
        audience=Audience.SENIOR,
        summary="customer 데이터 1.2k 건이 일시적으로 외부에 노출됨.",
        why_it_matters="동일 패턴 반복 시 사용자 신뢰 손상 + 규제 노출.",
        what_changed="audit log 보존 정책 변경 + access guard 강화.",
        practical_impact="향후 고객 데이터 접근 endpoint 변경 시 추가 검증 필요.",
        recommended_action="incident-2026-04-29 의 root cause 5가지 점검 항목 확인.",
        practice_topic="incident replay (내부 한정)",
        practice_goal="audit log + access guard 수정 흐름을 한 번 따라가 본다",
        practice_steps=(
            "audit log 변경 PR 다시 본다",
            "access guard 새 case 의 unit test 를 본다",
        ),
        practice_checklist=("내부 채널에서 incident 카드 닫음",),
        expected_output="incident retrospective 한 줄 요약",
        common_mistakes=("동일 패턴 후속 endpoint 에 guard 안 거는 케이스",),
        practice_verification=PracticeVerification(
            expected_result="postmortem 마지막 액션 아이템이 close 됨",
            command_to_run=None,
        ),
        rag_tags=("incident", "security"),
        cag_context_key="incident-2026-04-29-when-similar-spike",
        cag_context=CagContext(
            when_to_use="고객 데이터 path 변경 / access guard 회귀 의심 시",
        ),
        retrieval_queries=(
            "incident 2026-04-29 어떤 audit guard 가 새로 들어갔지?",
            "고객 데이터 노출 회귀 어디서 잡지?",
        ),
        retrieval_summary="audit/access guard 회귀 의심 시 incident postmortem 다시 본다",
        learning_level=LearningLevel.ADVANCED,
        prerequisites=("audit log 정책",),
        next_topics=("access-guard-v2",),
        estimated_practice_time="30분",
        review_after_days=30,
        references=(
            "https://internal.example.com/incidents/2026-04-29",
        ),
        confidence=0.9,
        dedup_key="eng-knowledge:backend-engineer:incident-2026-04-29",
    )
    base.update(overrides)
    return EngineeringKnowledgeItem(**base)


class FrontmatterAndSectionTests(unittest.TestCase):
    def test_share_scope_section_listed_in_required_sections(self) -> None:
        sections = required_sections()
        self.assertEqual(len(sections), 14)
        self.assertIn("13. 공유 가능 범위", sections)
        self.assertIn("14. 참고 자료", sections)

    def test_frontmatter_carries_share_scope_default_public(self) -> None:
        body = render_engineering_knowledge_note(_item())
        self.assertIn('share_scope: "public"', body)

    def test_frontmatter_share_scope_team_internal(self) -> None:
        body = render_engineering_knowledge_note(
            _item(share_scope=KnowledgeShareScope.TEAM_INTERNAL)
        )
        self.assertIn('share_scope: "team_internal"', body)
        # 본문(요약/실무 영향)은 정상 렌더되지만 share-scope 섹션이 추가
        # 외부 surface 규칙을 안내한다.
        self.assertIn("팀 내부 한정", body)
        self.assertIn("외부 surface 노출 규칙", body)


class RestrictedScopeBodyTests(unittest.TestCase):
    def test_restricted_requires_share_scope_reason(self) -> None:
        with self.assertRaises(RendererError):
            render_engineering_knowledge_note(
                _item(share_scope=KnowledgeShareScope.RESTRICTED)
            )

    def test_restricted_body_redacts_learning_sections(self) -> None:
        body = render_engineering_knowledge_note(
            _item(
                share_scope=KnowledgeShareScope.RESTRICTED,
                share_scope_reason="customer PII 가 포함된 incident",
            )
        )
        # 핵심 요약 / 권장 대응 / 실습 단계 본문은 placeholder 로 대체된다.
        self.assertIn("공개 제한된 자료", body)
        self.assertNotIn("customer 데이터 1.2k 건", body)
        self.assertNotIn("audit log 변경 PR 다시 본다", body)
        self.assertNotIn("동일 패턴 반복 시", body)
        # 그래도 share_scope 섹션 / 참고자료 / RAG/CAG 메타는 살아있다.
        self.assertIn("share_scope_reason", body)
        self.assertIn("customer PII 가 포함된 incident", body)
        self.assertIn("https://internal.example.com/incidents/2026-04-29", body)
        self.assertIn("rag_tags", body)


class QualityGateShareScopeTests(unittest.TestCase):
    def test_default_public_passes_gate(self) -> None:
        gate = evaluate_quality_gate(_item())
        self.assertTrue(gate.passed, msg=f"reasons={gate.reasons}")

    def test_restricted_without_reason_blocks(self) -> None:
        gate = evaluate_quality_gate(
            _item(share_scope=KnowledgeShareScope.RESTRICTED)
        )
        self.assertFalse(gate.passed)
        self.assertIn(
            "missing:share_scope_reason_for_restricted", gate.reasons
        )

    def test_restricted_with_reason_passes(self) -> None:
        gate = evaluate_quality_gate(
            _item(
                share_scope=KnowledgeShareScope.RESTRICTED,
                share_scope_reason="customer PII 가 포함된 incident",
            )
        )
        self.assertTrue(gate.passed, msg=f"reasons={gate.reasons}")

    def test_write_request_metadata_carries_share_scope(self) -> None:
        request = build_engineering_knowledge_write_request(
            _item(share_scope=KnowledgeShareScope.TEAM_INTERNAL)
        )
        assert request is not None
        ei = request.metadata["engineering_intelligence"]
        self.assertEqual(ei["share_scope"], "team_internal")
        external = request.metadata["shareable_external_payload"]
        self.assertEqual(external["share_scope"], "team_internal")
        # team-internal: title + source 노출, summary 는 surface 차단.
        self.assertIn("title", external)
        self.assertIn("source_url", external)
        self.assertNotIn("summary", external)


class ShareablePayloadTests(unittest.TestCase):
    def test_public_payload_includes_summary(self) -> None:
        payload = shareable_external_payload(_item())
        self.assertEqual(payload["share_scope"], "public")
        self.assertIn("summary", payload)
        self.assertIn("title", payload)

    def test_team_internal_payload_drops_summary(self) -> None:
        payload = shareable_external_payload(
            _item(share_scope=KnowledgeShareScope.TEAM_INTERNAL)
        )
        self.assertEqual(payload["share_scope"], "team_internal")
        self.assertNotIn("summary", payload)
        self.assertTrue(payload.get("internal_only"))

    def test_restricted_payload_only_marker(self) -> None:
        payload = shareable_external_payload(
            _item(
                share_scope=KnowledgeShareScope.RESTRICTED,
                share_scope_reason="customer PII",
            )
        )
        self.assertEqual(payload["share_scope"], "restricted")
        # 외부 surface 가 우연히라도 본문/제목/링크를 가져가지 않게 차단.
        self.assertNotIn("title", payload)
        self.assertNotIn("source_url", payload)
        self.assertNotIn("summary", payload)
        self.assertEqual(payload["restricted_marker"], "🔒 공개 제한된 자료")
        self.assertEqual(payload["share_scope_reason"], "customer PII")


class VaultOnlyMetadataTests(unittest.TestCase):
    def test_vault_only_carries_practice_body(self) -> None:
        meta = vault_only_metadata(
            _item(share_scope=KnowledgeShareScope.TEAM_INTERNAL)
        )
        self.assertEqual(meta["share_scope"], "team_internal")
        self.assertIn("practice_steps", meta)
        self.assertIn("common_mistakes", meta)
        self.assertIn("recommended_action", meta)


class DiscordSummaryShareScopeTests(unittest.TestCase):
    def test_public_line_unchanged(self) -> None:
        text = render_daily_role_summary(
            "backend-engineer",
            [_item()],
            today="2026-04-30",
        )
        self.assertIn("2026-04-29 고객 데이터 노출 사고", text)
        self.assertNotIn("team-internal", text)

    def test_team_internal_keeps_title_with_tag(self) -> None:
        text = render_daily_role_summary(
            "backend-engineer",
            [_item(share_scope=KnowledgeShareScope.TEAM_INTERNAL)],
            today="2026-04-30",
        )
        self.assertIn("2026-04-29 고객 데이터 노출 사고", text)
        self.assertIn("team-internal", text)

    def test_restricted_line_hides_title_and_url(self) -> None:
        text = render_daily_role_summary(
            "backend-engineer",
            [
                _item(
                    share_scope=KnowledgeShareScope.RESTRICTED,
                    share_scope_reason="customer PII",
                )
            ],
            today="2026-04-30",
        )
        # 제목과 URL 은 제거되고 공개 제한 마커 + topic_key 만 남는다.
        self.assertNotIn("2026-04-29 고객 데이터 노출 사고", text)
        self.assertNotIn("https://internal.example.com/", text)
        self.assertIn("공개 제한된 자료", text)
        self.assertIn("customer-data-leak-2026-04-29", text)


if __name__ == "__main__":
    unittest.main()
