from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents import (
    Dispatcher,
    WorkflowError,
    WorkflowOrchestrator,
    WorkflowState,
    build_participants_pool,
)
from yule_orchestrator.agents.review_loop import (
    ReviewFeedback,
    ReviewSeverity,
    ReviewSource,
    format_review_intake_message,
    format_review_reply_message,
    from_payload,
    route_review_feedback,
    to_payload,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _feedback(
    *,
    summary: str,
    body: str = "",
    categories: tuple[str, ...] = (),
    file_paths: tuple[str, ...] = (),
    severity: ReviewSeverity = ReviewSeverity.MEDIUM,
    source: ReviewSource = ReviewSource.GITHUB_PR_REVIEW,
    target_session_id: str | None = None,
    target_thread_id: int | None = None,
) -> ReviewFeedback:
    return ReviewFeedback(
        feedback_id="fb-1",
        source=source,
        submitted_at=datetime(2026, 4, 29, 10, 0, 0),
        summary=summary,
        body=body,
        target_session_id=target_session_id,
        target_thread_id=target_thread_id,
        file_paths=file_paths,
        severity=severity,
        categories=categories,
        author="reviewer-1",
    )


class RouteReviewFeedbackTestCase(unittest.TestCase):
    def test_design_feedback_routes_to_product_designer_with_reference_needed(self) -> None:
        feedback = _feedback(
            summary="hero copy too generic; layout feels flat",
            categories=("ui", "copy"),
        )
        routing = route_review_feedback(feedback)
        self.assertEqual(routing.primary_role, "product-designer")
        self.assertIn("frontend-engineer", routing.supporting_roles)
        self.assertTrue(routing.reference_needed)
        self.assertIn("Really Good Emails", routing.reference_sources + ("",) * 0)

    def test_qa_feedback_routes_to_qa_engineer(self) -> None:
        feedback = _feedback(
            summary="missing regression test for login redirect",
            categories=("test",),
        )
        routing = route_review_feedback(feedback)
        self.assertEqual(routing.primary_role, "qa-engineer")
        self.assertFalse(routing.reference_needed)

    def test_backend_path_routes_to_backend_engineer(self) -> None:
        feedback = _feedback(
            summary="auth token validation off",
            file_paths=("src/api/auth_service.py",),
        )
        routing = route_review_feedback(feedback)
        self.assertEqual(routing.primary_role, "backend-engineer")

    def test_frontend_path_routes_to_frontend_engineer(self) -> None:
        feedback = _feedback(
            summary="button alignment broken on mobile",
            file_paths=("web/components/Button.tsx",),
        )
        routing = route_review_feedback(feedback)
        self.assertEqual(routing.primary_role, "frontend-engineer")

    def test_blocking_architecture_feedback_routes_to_tech_lead(self) -> None:
        feedback = _feedback(
            summary="this introduces a circular dependency in the domain layer",
            categories=("architecture",),
            severity=ReviewSeverity.BLOCKING,
        )
        routing = route_review_feedback(feedback)
        self.assertEqual(routing.primary_role, "tech-lead")
        self.assertIn("backend-engineer", routing.supporting_roles)

    def test_ambiguous_feedback_falls_back_to_tech_lead(self) -> None:
        feedback = _feedback(summary="not sure if this is the right approach")
        routing = route_review_feedback(feedback)
        self.assertEqual(routing.primary_role, "tech-lead")

    def test_reference_gap_keywords_surface_correct_sources(self) -> None:
        feedback = _feedback(
            summary="UX 플로우 단계가 너무 깊고 카피 훅이 약합니다",
            categories=("ui",),
        )
        routing = route_review_feedback(feedback)
        self.assertTrue(routing.reference_needed)
        self.assertIn("UX 플로우", routing.reference_gaps)
        self.assertIn("카피 훅", routing.reference_gaps)
        self.assertIn("Mobbin", routing.reference_sources)


class ReviewFeedbackPayloadTestCase(unittest.TestCase):
    def test_round_trip_serialization(self) -> None:
        original = _feedback(
            summary="add a11y label to button",
            body="aria-label missing",
            categories=("ui", "accessibility"),
            file_paths=("web/Button.tsx",),
            severity=ReviewSeverity.HIGH,
            source=ReviewSource.GITHUB_COPILOT,
            target_session_id="abcd1234",
            target_thread_id=999,
        )
        roundtrip = from_payload(to_payload(original))
        self.assertEqual(roundtrip.feedback_id, original.feedback_id)
        self.assertEqual(roundtrip.source, original.source)
        self.assertEqual(roundtrip.severity, original.severity)
        self.assertEqual(roundtrip.categories, original.categories)
        self.assertEqual(roundtrip.target_thread_id, 999)


class FormatReviewMessagesTestCase(unittest.TestCase):
    def test_intake_message_lists_role_and_reference_sources(self) -> None:
        feedback = _feedback(
            summary="onboarding flow drops user at step 3",
            categories=("ux",),
        )
        routing = route_review_feedback(feedback)
        message = format_review_intake_message(
            feedback,
            routing,
            session_id="sess-1",
            review_cycle=1,
        )
        self.assertIn("리뷰 피드백 수신", message)
        self.assertIn("`product-designer`", message)
        self.assertIn("Mobbin", message)

    def test_reply_message_lists_applied_proposed_remaining(self) -> None:
        feedback = _feedback(summary="hero CTA copy weak", categories=("copy",))
        routing = route_review_feedback(feedback)
        message = format_review_reply_message(
            feedback,
            routing,
            session_id="sess-1",
            review_cycle=2,
            applied=("CTA copy를 'Start free' 로 교체",),
            proposed=("Hero subheadline에 social proof 추가",),
            remaining=("A/B 테스트 셋업",),
        )
        self.assertIn("적용한 수정", message)
        self.assertIn("Start free", message)
        self.assertIn("추가 제안", message)
        self.assertIn("남은 이슈", message)


class _IsolatedCacheTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._prev_root = os.environ.get("YULE_REPO_ROOT")
        os.environ["YULE_REPO_ROOT"] = self._tmp.name

    def tearDown(self) -> None:
        if self._prev_root is None:
            os.environ.pop("YULE_REPO_ROOT", None)
        else:
            os.environ["YULE_REPO_ROOT"] = self._prev_root


def _build_orchestrator() -> WorkflowOrchestrator:
    pool = build_participants_pool(REPO_ROOT, "engineering-agent")
    return WorkflowOrchestrator(Dispatcher(pool), now_fn=lambda: datetime(2026, 4, 29, 10, 0, 0))


class ReviewWorkflowIntegrationTestCase(_IsolatedCacheTestCase):
    def test_record_review_increments_cycle_and_appends_record(self) -> None:
        orch = _build_orchestrator()
        intake = orch.intake(prompt="기존 랜딩 hero 섹션 보강", write_requested=True)
        orch.approve(intake.session.session_id)

        feedback = _feedback(
            summary="hero copy too long; layout dense",
            categories=("ui", "copy"),
            target_session_id=intake.session.session_id,
            target_thread_id=intake.session.thread_id,
        )
        result = orch.record_review_feedback(intake.session.session_id, feedback)

        self.assertEqual(result.session.review_cycle, 1)
        self.assertEqual(len(result.session.review_feedbacks), 1)
        self.assertEqual(result.routing.primary_role, "product-designer")
        self.assertIn("리뷰 피드백 수신", result.message)

    def test_record_review_reopens_completed_session(self) -> None:
        orch = _build_orchestrator()
        intake = orch.intake(prompt="새 결제 API 스켈레톤")
        orch.approve(intake.session.session_id)
        orch.complete(intake.session.session_id, summary="merged PR #42")

        feedback = _feedback(
            summary="missing regression test for refund path",
            categories=("test",),
        )
        result = orch.record_review_feedback(intake.session.session_id, feedback)

        self.assertEqual(result.session.state, WorkflowState.IN_PROGRESS)
        self.assertEqual(result.routing.primary_role, "qa-engineer")

    def test_record_review_rejected_session_raises(self) -> None:
        orch = _build_orchestrator()
        intake = orch.intake(prompt="잘못된 작업")
        orch.reject(intake.session.session_id, reason="duplicate")

        with self.assertRaises(WorkflowError):
            orch.record_review_feedback(
                intake.session.session_id,
                _feedback(summary="trivial nit"),
            )

    def test_respond_to_review_writes_progress_note(self) -> None:
        orch = _build_orchestrator()
        intake = orch.intake(prompt="이메일 캠페인 카피 정리")
        orch.approve(intake.session.session_id)
        feedback = _feedback(
            summary="후크가 약하고 비주얼이 평이합니다",
            categories=("copy", "visual"),
        )
        orch.record_review_feedback(intake.session.session_id, feedback)

        reply = orch.respond_to_review(
            intake.session.session_id,
            feedback_id=feedback.feedback_id,
            applied=("후크 카피 교체",),
            proposed=("CTA 버튼 색상 시안 변경",),
            remaining=("최종 디자이너 컨펌",),
        )

        self.assertIn("리뷰 회신", reply.message)
        self.assertIn("후크 카피 교체", reply.message)
        self.assertEqual(len(reply.session.progress_notes), 1)
        self.assertIn("review cycle 1 회신", reply.session.progress_notes[0])


if __name__ == "__main__":
    unittest.main()
