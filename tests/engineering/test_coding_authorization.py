"""Coding Agent Authorization MVP — proposal / executor selection tests.

The recommender is the only thing standing between a user request and
which role gets a write gate. Pin every required category so a typo in
a keyword bank or a profile reorder fails loudly.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.coding.authorization import (
    CodingAuthorizationProposal,
    format_authorization_message,
    recommend_authorization,
    reset_role_profile_cache,
)


class ExecutorRoleSelectionTests(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401 - test setup
        # The recommender caches profiles per process; clear before
        # each test so a future fixture-injection test doesn't leak.
        reset_role_profile_cache()

    def test_frontend_request_picks_frontend_engineer(self) -> None:
        proposal = recommend_authorization(
            user_request="React 결제 페이지 hero 컴포넌트 CSS 좀 다듬어줘",
            session_id="sess-1",
        )
        self.assertEqual(proposal.executor_role, "frontend-engineer")
        self.assertIn("tech-lead", proposal.review_roles)
        self.assertTrue(proposal.approval_required)
        self.assertEqual(proposal.session_id, "sess-1")
        # Forbidden scope must include backend/secret guards.
        forbidden_joined = "\n".join(proposal.forbidden_scope)
        self.assertIn("backend", forbidden_joined.lower())

    def test_spring_security_request_picks_backend_engineer(self) -> None:
        proposal = recommend_authorization(
            user_request="Spring Security 기반 API 인증 흐름 추가해줘",
        )
        self.assertEqual(proposal.executor_role, "backend-engineer")
        self.assertIn("tech-lead", proposal.review_roles)

    def test_api_database_request_picks_backend_engineer(self) -> None:
        proposal = recommend_authorization(
            user_request="결제 API와 transaction schema migration 필요해",
        )
        self.assertEqual(proposal.executor_role, "backend-engineer")

    def test_rag_memory_request_picks_ai_engineer(self) -> None:
        proposal = recommend_authorization(
            user_request="RAG memory layer에 prompt 정책 추가하자",
        )
        self.assertEqual(proposal.executor_role, "ai-engineer")

    def test_agent_runtime_request_picks_ai_engineer(self) -> None:
        proposal = recommend_authorization(
            user_request="agent runtime의 LLM model routing 로직 정리해줘",
        )
        self.assertEqual(proposal.executor_role, "ai-engineer")

    def test_docker_ci_request_picks_devops_engineer(self) -> None:
        proposal = recommend_authorization(
            user_request="Docker 이미지와 GitHub Actions CI 파이프라인 분리해줘",
        )
        self.assertEqual(proposal.executor_role, "devops-engineer")

    def test_env_deploy_request_picks_devops_engineer(self) -> None:
        proposal = recommend_authorization(
            user_request="env supervisor 실행 흐름과 monitoring deploy 정리",
        )
        self.assertEqual(proposal.executor_role, "devops-engineer")

    def test_test_only_request_picks_qa_engineer(self) -> None:
        proposal = recommend_authorization(
            user_request="회귀 regression test와 acceptance smoke 시나리오 정리해줘",
        )
        self.assertEqual(proposal.executor_role, "qa-engineer")

    def test_ux_copy_request_picks_product_designer(self) -> None:
        proposal = recommend_authorization(
            user_request="운영 UX copy 정리 — 사용자 흐름 문서와 디자인 토큰 문서",
        )
        self.assertEqual(proposal.executor_role, "product-designer")


class FallbackTests(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401 - test setup
        reset_role_profile_cache()

    def test_empty_request_falls_back_to_tech_lead(self) -> None:
        proposal = recommend_authorization(user_request="   ")
        self.assertEqual(proposal.executor_role, "tech-lead")
        self.assertTrue(proposal.approval_required)
        self.assertIn("clarification", proposal.reason.lower())
        self.assertIs(proposal.metadata.get("fallback"), True)

    def test_unmatched_request_falls_back_to_tech_lead(self) -> None:
        # No domain keyword — expect tech-lead clarification fallback.
        proposal = recommend_authorization(
            user_request="음 일단 한 번 봐줘",
        )
        self.assertEqual(proposal.executor_role, "tech-lead")
        self.assertIn("tech-lead", proposal.reason.lower())


class ReviewerAssignmentTests(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401 - test setup
        reset_role_profile_cache()

    def test_tech_lead_is_always_a_reviewer(self) -> None:
        proposal = recommend_authorization(
            user_request="React 화면 hero 컴포넌트 다듬자",
        )
        self.assertIn("tech-lead", proposal.review_roles)

    def test_qa_engineer_is_a_default_reviewer_when_not_executor(self) -> None:
        proposal = recommend_authorization(
            user_request="React 컴포넌트 회귀 잡으면서 hero copy 적용",
        )
        # qa-engineer must show up as reviewer because the executor is
        # frontend (qa is not the executor here).
        self.assertNotEqual(proposal.executor_role, "qa-engineer")
        self.assertIn("qa-engineer", proposal.review_roles)


class ProposalShapeTests(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401 - test setup
        reset_role_profile_cache()

    def test_proposal_has_required_fields(self) -> None:
        proposal = recommend_authorization(
            user_request="Spring Security API 인증 흐름 추가",
            session_id="abc123",
        )
        self.assertIsInstance(proposal, CodingAuthorizationProposal)
        # Spec requires these fields on every proposal.
        self.assertEqual(proposal.session_id, "abc123")
        self.assertTrue(proposal.user_request)
        self.assertTrue(proposal.executor_role)
        self.assertGreaterEqual(len(proposal.review_roles), 1)
        self.assertGreaterEqual(len(proposal.participant_roles), 1)
        self.assertGreaterEqual(len(proposal.write_scope), 1)
        self.assertGreaterEqual(len(proposal.forbidden_scope), 1)
        self.assertTrue(proposal.reason)
        self.assertGreaterEqual(len(proposal.safety_rules), 1)
        self.assertTrue(proposal.approval_required)

    def test_safety_rules_include_destructive_command_ban(self) -> None:
        proposal = recommend_authorization(
            user_request="React 화면 hero 컴포넌트 추가",
        )
        joined = "\n".join(proposal.safety_rules).lower()
        self.assertIn("git reset", joined)
        self.assertIn("secret", joined)
        self.assertIn("write_scope", joined)

    def test_executor_role_appears_in_participant_roles(self) -> None:
        proposal = recommend_authorization(
            user_request="React UI hero 컴포넌트 정리",
        )
        self.assertIn(proposal.executor_role, proposal.participant_roles)


class FormatAuthorizationMessageTests(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401 - test setup
        reset_role_profile_cache()

    def test_message_includes_executor_review_and_approval_phrase(self) -> None:
        proposal = recommend_authorization(
            user_request="Spring Security API 인증 흐름 추가",
        )
        text = format_authorization_message(proposal)
        self.assertIn("코딩 권한 제안", text)
        self.assertIn(proposal.executor_role, text)
        # At least one approval phrase suggestion must appear.
        self.assertTrue(
            any(
                phrase in text
                for phrase in ("수정 승인", "이대로 구현 진행", "구현 시작")
            )
        )
        # Safety rules visible.
        self.assertIn("safety rules", text)


if __name__ == "__main__":
    unittest.main()
