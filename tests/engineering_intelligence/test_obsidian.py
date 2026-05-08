"""Obsidian bridge — quality gate + ObsidianWriteRequest shape."""

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
    NOTE_KIND_ENGINEERING_KNOWLEDGE,
    PracticeVerification,
    SourceKind,
)
from yule_orchestrator.agents.engineering_intelligence.obsidian import (
    build_engineering_knowledge_write_request,
    build_rejected_quality_gate_audit,
    evaluate_quality_gate,
)


def _good_item(**overrides) -> EngineeringKnowledgeItem:
    base = dict(
        item_id="dev-spring-6",
        topic_key="spring-6-2-virtual-thread",
        title="Spring 6.2 Virtual Thread 최신 권장",
        role="backend-engineer",
        stack_tags=("spring", "java"),
        source_name="Spring Engineering Blog",
        source_url="https://spring.io/blog/2026/05/01/spring-6-2-vt",
        source_kind=SourceKind.ENGINEERING_BLOG,
        collected_at="2026-05-08T03:00:00Z",
        importance=Importance.HIGH,
        audience=Audience.JUNIOR,
        summary="Spring 6.2 부터 가상 스레드 사용 패턴이 정리됐다.",
        why_it_matters="Tomcat 11 에서 spike 트래픽 처리에 안전한 기본 설정이 바뀌었다.",
        what_changed="`spring.threads.virtual.enabled` 기본값과 권장 풀 사이즈가 바뀜.",
        practical_impact="기존 RestController 응답 지연이 감소하지만 ThreadLocal 안전성 점검 필요.",
        recommended_action="가상 스레드 의존 코드의 ThreadLocal 사용처를 한번 더 본다.",
        practice_topic="Spring 6.2 가상 스레드 마이그레이션 점검",
        practice_goal="기본 설정으로 켜고 ThreadLocal 의존 endpoint 가 안전한지 본다",
        practice_steps=(
            "spring-boot 3.4 + spring-framework 6.2 로 실험 프로젝트 생성",
            "RestController 한 개를 가상 스레드로 전환",
            "ThreadLocal 사용처를 ScopedValue 로 마이그레이션",
        ),
        practice_checklist=(
            "endpoint 응답이 5xx 가 아니다",
            "ThreadLocal 누수 경고가 로그에 없다",
        ),
        expected_output="vt 기본 endpoint + 마이그레이션 노트",
        common_mistakes=(
            "ThreadLocal 을 그대로 두고 가상 스레드만 켜기",
        ),
        practice_verification=PracticeVerification(
            expected_result="2xx 응답 + 로그 깨끗",
            command_to_run="./gradlew bootRun",
        ),
        rag_tags=("spring", "virtual-thread"),
        cag_context_key="spring-6-2-vt-when-traffic-spikes",
        cag_context=CagContext(
            when_to_use="spring 6.2+ 도입 시 또는 vt 회귀 의심 시",
            decision_hint="ThreadLocal 사용 vs ScopedValue 마이그레이션",
        ),
        retrieval_queries=(
            "Spring 6.2 가상 스레드 기본값?",
            "ThreadLocal 누수 어떻게 잡지?",
        ),
        retrieval_summary="vt 마이그레이션 회귀 신호 빠르게 다시 본다",
        learning_level=LearningLevel.INTERMEDIATE,
        prerequisites=("Spring Boot 기초",),
        next_topics=("ScopedValue",),
        estimated_practice_time="45분",
        review_after_days=90,
        references=("https://spring.io/blog/2026/05/01/spring-6-2-vt",),
        confidence=0.8,
        dedup_key="eng-knowledge:backend-engineer:abc",
    )
    base.update(overrides)
    return EngineeringKnowledgeItem(**base)


class GatePassTests(unittest.TestCase):
    def test_full_item_passes_gate(self) -> None:
        gate = evaluate_quality_gate(_good_item())
        self.assertTrue(gate.passed, msg=f"reasons={gate.reasons}")
        self.assertEqual(gate.reasons, ())

    def test_request_built_when_gate_passes(self) -> None:
        request = build_engineering_knowledge_write_request(
            _good_item(),
            session_id="session-1",
        )
        self.assertIsNotNone(request)
        self.assertEqual(request.note_kind, NOTE_KIND_ENGINEERING_KNOWLEDGE)
        self.assertEqual(request.note_kind, "engineering-knowledge")
        self.assertEqual(request.session_id, "session-1")
        self.assertEqual(request.project, "yule-studio-agent")
        self.assertEqual(request.title, "Spring 6.2 Virtual Thread 최신 권장")
        # Body present.
        self.assertIn("body", request.metadata)
        self.assertIn("# Spring 6.2", request.metadata["body"])
        # Engineering intelligence metadata block.
        ei = request.metadata["engineering_intelligence"]
        self.assertEqual(ei["topic_key"], "spring-6-2-virtual-thread")
        self.assertEqual(ei["role"], "backend-engineer")
        self.assertIn("rag_tags", ei)
        # L1 autonomy hint.
        self.assertEqual(
            request.metadata["autonomy_level"], "L1_AUTO_RECORD_REQUIRED"
        )

    def test_request_does_not_carry_approval_fields(self) -> None:
        # engineering-knowledge is L1 — must NOT carry approval_id /
        # approved_by / approved_at (the worker only requires those
        # for the canonical 'knowledge' / 'knowledge-note' / 'decision-record'
        # kinds).
        request = build_engineering_knowledge_write_request(_good_item())
        self.assertIsNone(request.approval_id)
        self.assertIsNone(request.approved_by)
        self.assertIsNone(request.approved_at)
        self.assertFalse(request.requires_approval())


class GateFailTests(unittest.TestCase):
    def test_missing_summary_blocks(self) -> None:
        item = _good_item(summary="")
        gate = evaluate_quality_gate(item)
        self.assertFalse(gate.passed)
        self.assertIn("missing:summary", gate.reasons)
        self.assertIsNone(build_engineering_knowledge_write_request(item))

    def test_missing_source_url_blocks(self) -> None:
        item = _good_item(source_url="")
        gate = evaluate_quality_gate(item)
        self.assertFalse(gate.passed)
        self.assertIn("missing:source_url", gate.reasons)

    def test_practice_steps_under_two_blocks(self) -> None:
        item = _good_item(practice_steps=("only one",))
        gate = evaluate_quality_gate(item)
        self.assertIn("missing:practice_steps_min_2", gate.reasons)
        self.assertIsNone(build_engineering_knowledge_write_request(item))

    def test_missing_references_blocks(self) -> None:
        item = _good_item(references=())
        gate = evaluate_quality_gate(item)
        self.assertIn("missing:references_min_1", gate.reasons)

    def test_missing_rag_tags_blocks(self) -> None:
        item = _good_item(rag_tags=())
        gate = evaluate_quality_gate(item)
        self.assertIn("missing:rag_tags_min_1", gate.reasons)

    def test_missing_cag_context_when_to_use_blocks(self) -> None:
        item = _good_item(cag_context=None)
        gate = evaluate_quality_gate(item)
        self.assertIn("missing:cag_context.when_to_use", gate.reasons)

    def test_retrieval_queries_under_two_blocks(self) -> None:
        item = _good_item(retrieval_queries=("only one query?",))
        gate = evaluate_quality_gate(item)
        self.assertIn("missing:retrieval_queries_min_2", gate.reasons)

    def test_missing_practice_verification_expected_blocks(self) -> None:
        item = _good_item(practice_verification=None)
        gate = evaluate_quality_gate(item)
        self.assertIn(
            "missing:practice_verification.expected_result", gate.reasons
        )

    def test_review_after_days_zero_blocks(self) -> None:
        item = _good_item(review_after_days=0)
        gate = evaluate_quality_gate(item)
        self.assertIn("missing:review_after_days", gate.reasons)


class RejectionAuditTests(unittest.TestCase):
    def test_audit_payload_has_outcome_and_reasons(self) -> None:
        item = _good_item(summary="", recommended_action="")
        gate = evaluate_quality_gate(item)
        audit = build_rejected_quality_gate_audit(item, gate)
        self.assertEqual(audit["action"], "engineering_knowledge_quality_gate")
        self.assertEqual(audit["outcome"], "rejected_quality_gate")
        self.assertEqual(audit["topic_key"], item.topic_key)
        self.assertEqual(audit["role"], item.role)
        self.assertIn("missing:summary", audit["reasons"])
        self.assertIn("missing:recommended_action", audit["reasons"])
        self.assertIn("rejected", audit["summary"])


if __name__ == "__main__":
    unittest.main()
