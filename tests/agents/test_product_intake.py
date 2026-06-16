"""Product-intake gate — shaping, feature-family gaps, question policy (pure)."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.product_intake import shape_product_intent
from yule_engineering.agents.product_intake import question_policy
from yule_engineering.agents.product_intake.models import (
    READINESS_CLARIFICATION,
    READINESS_RESEARCH_ONLY,
)


def _q_categories(packet):
    return {q.category for q in packet.decision_questions}


def _implied(packet):
    return {g.name for g in packet.implied_features}


class VideoUploadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.p = shape_product_intent("영상 업로드 서비스 구현해줘")

    def test_detects_media_upload_family(self) -> None:
        self.assertIn("media_upload", self.p.detected_families)

    def test_visibility_ordering_role_questions(self) -> None:
        cats = _q_categories(self.p)
        self.assertIn("permission", cats)   # who can upload/view
        self.assertIn("visibility", cats)   # 공개 정책
        self.assertIn("ordering", cats)     # 노출 순서

    def test_implied_processing_thumbnail_failure(self) -> None:
        impl = _implied(self.p)
        self.assertIn("processing_state", impl)
        self.assertIn("failure_retry", impl)
        self.assertIn("thumbnail_fallback", impl)
        self.assertIn("visibility_state", impl)

    def test_acceptance_and_non_goals(self) -> None:
        self.assertTrue(self.p.acceptance_criteria)
        self.assertTrue(any("실패" in c for c in self.p.acceptance_criteria))
        self.assertTrue(any("클론" in n for n in self.p.non_goals))

    def test_clarification_needed(self) -> None:
        self.assertEqual(self.p.readiness.readiness, READINESS_CLARIFICATION)

    def test_recommended_options_present(self) -> None:
        for q in self.p.decision_questions:
            rec = [o for o in q.options if o.recommended]
            self.assertEqual(len(rec), 1, q.id)


class AdminCrudTests(unittest.TestCase):
    def test_publish_ordering_draft(self) -> None:
        p = shape_product_intent("관리자 공지사항 CRUD 만들어줘")
        self.assertIn("admin_crud", p.detected_families)
        cats = _q_categories(p)
        self.assertTrue({"visibility", "ordering", "publish"} & cats)
        self.assertIn("draft_state", _implied(p))


class AuthTests(unittest.TestCase):
    def test_auth_session_role_questions(self) -> None:
        p = shape_product_intent("로그인 기능 구현해줘")
        self.assertIn("auth_and_permission", p.detected_families)
        # all auth questions are permission-category; auth_method + session + role
        prompts = " ".join(q.prompt for q in p.decision_questions)
        self.assertTrue("인증" in prompts or "세션" in prompts or "권한" in prompts)
        self.assertIn("session_management", _implied(p))


class QuestionPolicyTests(unittest.TestCase):
    def test_budget_never_exceeds_three(self) -> None:
        # a request hitting many families must still cap at 3 questions
        p = shape_product_intent("관리자 영상 업로드 + 결제 구독 + 로그인 서비스")
        self.assertLessEqual(len(p.decision_questions), question_policy.MAX_QUESTIONS)

    def test_billing_is_high_priority_when_present(self) -> None:
        p = shape_product_intent("영상 업로드 + 결제 구독 + 검색 + 알림 만들어줘")
        # with many families, billing (high priority) must survive the budget cut
        self.assertIn("billing", _q_categories(p))

    def test_safe_defaults_become_assumptions_not_questions(self) -> None:
        p = shape_product_intent("영상 업로드 서비스 구현해줘")
        joined = "\n".join(p.recommended_defaults)
        self.assertIn("로딩", joined)         # baseline cross-cutting auto-filled
        self.assertIn("검증", joined)
        # baseline concerns are not surfaced as questions
        self.assertNotIn("validation", _q_categories(p))

    def test_dropped_decisions_become_assumptions(self) -> None:
        p = shape_product_intent("관리자 영상 업로드 + 결제 + 로그인 + 검색")
        # budget drops some decisions → they appear as explicit assumptions
        self.assertTrue(any("가정" in a for a in p.assumptions))

    def test_all_questions_well_formed(self) -> None:
        p = shape_product_intent("영상 업로드 + 결제 + 로그인")
        for q in p.decision_questions:
            self.assertTrue(question_policy.is_well_formed(q))


class ReadinessTests(unittest.TestCase):
    def test_research_only(self) -> None:
        p = shape_product_intent("경쟁사 리서치 좀 해줘")
        self.assertEqual(p.readiness.readiness, READINESS_RESEARCH_ONLY)

    def test_vague_short_request_clarification(self) -> None:
        p = shape_product_intent("뭔가 만들어줘")
        self.assertEqual(p.readiness.readiness, READINESS_CLARIFICATION)

    def test_packet_serializable(self) -> None:
        d = shape_product_intent("영상 업로드 서비스 구현해줘").to_dict()
        self.assertIn("decision_questions", d)
        self.assertIn("acceptance_criteria", d)
        self.assertEqual(d["readiness"]["readiness"], READINESS_CLARIFICATION)


if __name__ == "__main__":
    unittest.main()
