"""Renderer — frontmatter, 13 mandatory sections, redaction, hard contracts."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.engineering_intelligence.models import (
    Audience,
    CagContext,
    EngineeringKnowledgeItem,
    Importance,
    LearningLevel,
    PracticeVerification,
    ProjectApplicability,
    SourceKind,
)
from yule_orchestrator.agents.engineering_intelligence.renderer import (
    RendererError,
    render_engineering_knowledge_note,
    render_frontmatter,
    required_sections,
)


def _full_item(**overrides) -> EngineeringKnowledgeItem:
    base = dict(
        item_id="ai-engineer-rag-eval",
        topic_key="rag-eval-faithfulness",
        title="RAG 평가 지표: faithfulness 와 context-recall",
        role="ai-engineer",
        stack_tags=("rag", "evaluation"),
        source_name="Ragas Evaluation Docs",
        source_url="https://docs.ragas.io/en/latest/concepts/metrics/faithfulness.html",
        source_kind=SourceKind.DOCS,
        collected_at="2026-05-08T03:00:00Z",
        importance=Importance.HIGH,
        audience=Audience.JUNIOR,
        summary="RAG 응답이 retrieved context 와 얼마나 일치하는지 측정하는 지표.",
        why_it_matters="hallucination 을 사후 검증 가능하게 만든다.",
        what_changed="Ragas 0.2 에서 faithfulness 알고리즘이 LLM-as-judge 로 정리됐다.",
        practical_impact="RAG 파이프라인 변경 시 회귀 지표로 쓸 수 있다.",
        recommended_action="기존 retrieval 평가에 faithfulness + context-recall 추가.",
        practice_topic="자체 RAG 응답에 faithfulness 점수 측정",
        practice_goal="응답 5 개에 대해 faithfulness 점수를 계산해 본다",
        practice_steps=(
            "Ragas 라이브러리 설치 + 샘플 데이터셋 준비",
            "응답 5 개에 대해 faithfulness 평가 실행",
            "점수가 낮은 응답 1 개를 골라 원인 분석",
        ),
        practice_checklist=(
            "샘플 데이터셋이 5 건 이상이다",
            "faithfulness 평균 점수가 출력된다",
            "낮은 점수 케이스의 원인이 한 줄로 정리됐다",
        ),
        expected_output="평균 faithfulness 점수 + 가장 낮은 응답의 원인 분석 한 줄",
        common_mistakes=(
            "context window 길이를 무시하고 평가",
            "ground-truth 가 없는 응답에 faithfulness 만 사용",
        ),
        practice_verification=PracticeVerification(
            expected_result="평균 faithfulness >= 0.7 인 데이터셋이 만들어진다",
            command_to_run="python -m ragas evaluate --metrics faithfulness",
            failure_symptoms=("ImportError ragas", "OPENAI_API_KEY missing"),
            troubleshooting_hint="ragas 0.2 + python>=3.10 으로 다시 시도",
        ),
        rag_tags=("rag", "evaluation", "faithfulness"),
        cag_context_key="rag-eval-when-rag-changes",
        cag_context=CagContext(
            when_to_use="RAG 검색 단계나 프롬프트 변경 후 회귀 지표가 필요할 때",
            constraints=("LLM-as-judge 비용 발생",),
            decision_hint="비용 vs 회귀 검출 정확도",
            avoid_if=("ground-truth 가 전혀 없음",),
        ),
        retrieval_queries=(
            "RAG 평가 지표는 무엇이 있나?",
            "faithfulness 점수가 떨어지는 원인은?",
        ),
        retrieval_summary="RAG 응답 회귀 검출 시 faithfulness 지표를 빠르게 다시 본다",
        learning_level=LearningLevel.INTERMEDIATE,
        prerequisites=("RAG 기본", "Python 3.10",),
        next_topics=("answer-correctness", "context-precision",),
        estimated_practice_time="60분",
        review_after_days=60,
        project_applicability=ProjectApplicability(
            related_repo="yule-studio-agent",
            related_module="src/yule_orchestrator/memory",
            possible_issue_title="RAG faithfulness 회귀 지표 추가",
            implementation_risk="low",
        ),
        references=(
            "https://docs.ragas.io/en/latest/concepts/metrics/faithfulness.html",
            "https://arxiv.org/abs/2309.15217",
        ),
        confidence=0.85,
        dedup_key="eng-knowledge:ai-engineer:abc123",
    )
    base.update(overrides)
    return EngineeringKnowledgeItem(**base)


class FrontmatterTests(unittest.TestCase):
    def test_contract_id_present(self) -> None:
        fm = render_frontmatter(_full_item())
        self.assertIn("contract: engineering-knowledge/v0", fm)

    def test_kind_is_engineering_knowledge(self) -> None:
        fm = render_frontmatter(_full_item())
        self.assertIn("kind: engineering-knowledge", fm)

    def test_role_audience_importance_present(self) -> None:
        fm = render_frontmatter(_full_item())
        for line in (
            'role: "ai-engineer"',
            'audience: "junior"',
            'importance: "high"',
            'learning_level: "intermediate"',
            'topic_key: "rag-eval-faithfulness"',
            'cag_context_key: "rag-eval-when-rag-changes"',
        ):
            self.assertIn(line, fm)

    def test_rag_tags_serialised(self) -> None:
        fm = render_frontmatter(_full_item())
        self.assertIn('rag_tags: ["rag", "evaluation", "faithfulness"]', fm)

    def test_review_after_days_serialised(self) -> None:
        fm = render_frontmatter(_full_item(review_after_days=42))
        self.assertIn("review_after_days: 42", fm)


class SectionPresenceTests(unittest.TestCase):
    def test_all_required_sections_rendered(self) -> None:
        body = render_engineering_knowledge_note(_full_item())
        for title in required_sections():
            self.assertIn(f"## {title}", body, msg=f"missing section: {title}")

    def test_toc_lists_all_required_sections(self) -> None:
        body = render_engineering_knowledge_note(_full_item())
        self.assertIn("## 목차", body)
        # TOC line numbers 1..N each appear on their own line, where N
        # is the current required-section count (14 — share_scope was
        # added between RAG/CAG and References).
        for index in range(1, len(required_sections()) + 1):
            self.assertRegex(body, rf"\n{index}\. ")

    def test_practice_section_includes_steps_checklist_verification(self) -> None:
        body = render_engineering_knowledge_note(_full_item())
        self.assertIn("Ragas 라이브러리 설치", body)
        self.assertIn("[ ] 샘플 데이터셋이 5 건 이상이다", body)
        self.assertIn("expected_result", body)
        self.assertIn("command_to_run", body)

    def test_rag_cag_section_includes_metadata_and_context(self) -> None:
        body = render_engineering_knowledge_note(_full_item())
        self.assertIn("rag_tags", body)
        self.assertIn("cag_context_key", body)
        self.assertIn("CAG 의사결정 컨텍스트", body)
        self.assertIn("when_to_use", body)
        self.assertIn("학습 난이도와 선수 지식", body)
        self.assertIn("프로젝트 적용 후보", body)
        self.assertIn("재검토 시점", body)

    def test_references_lists_source_url(self) -> None:
        body = render_engineering_knowledge_note(_full_item())
        self.assertIn(
            "https://docs.ragas.io/en/latest/concepts/metrics/faithfulness.html",
            body,
        )
        self.assertIn("https://arxiv.org/abs/2309.15217", body)


class HardContractTests(unittest.TestCase):
    def test_empty_summary_raises(self) -> None:
        with self.assertRaises(RendererError):
            render_engineering_knowledge_note(_full_item(summary=""))

    def test_missing_source_url_raises(self) -> None:
        with self.assertRaises(RendererError):
            render_engineering_knowledge_note(_full_item(source_url=""))

    def test_missing_practice_topic_raises(self) -> None:
        with self.assertRaises(RendererError):
            render_engineering_knowledge_note(_full_item(practice_topic=""))

    def test_practice_steps_under_two_raises(self) -> None:
        with self.assertRaises(RendererError):
            render_engineering_knowledge_note(
                _full_item(practice_steps=("only one step",))
            )

    def test_no_common_mistakes_raises(self) -> None:
        with self.assertRaises(RendererError):
            render_engineering_knowledge_note(_full_item(common_mistakes=()))

    def test_empty_title_raises(self) -> None:
        with self.assertRaises(RendererError):
            render_engineering_knowledge_note(_full_item(title=""))


class RedactionTests(unittest.TestCase):
    def test_secret_in_summary_is_redacted(self) -> None:
        body = render_engineering_knowledge_note(
            _full_item(
                summary=(
                    "이 문서는 OpenAI 호출에 sk-supersecretvaluethatlooksreal12 "
                    "토큰을 쓴다."
                )
            )
        )
        self.assertNotIn("sk-supersecretvaluethatlooksreal12", body)
        self.assertIn("[redacted-api-key]", body)

    def test_github_token_in_practice_step_is_redacted(self) -> None:
        body = render_engineering_knowledge_note(
            _full_item(
                practice_steps=(
                    "GH_TOKEN=ghp_aaaaaaaaaaaaaaaaaaaaaa 로 export",
                    "step 2",
                )
            )
        )
        self.assertNotIn("ghp_aaaaaaaaaaaaaaaaaaaaaa", body)
        self.assertIn("[redacted-github-token]", body)


if __name__ == "__main__":
    unittest.main()
