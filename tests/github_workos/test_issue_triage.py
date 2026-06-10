"""Senior triage matrix — domain routing + coding gate + excluded rationale."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.github_workos.issue_context import (
    build_request_from_discord_intake,
    build_request_from_github_issue,
)
from yule_engineering.agents.github_workos.models import (
    PermissionLevel,
    RoleWorkOrder,
)
from yule_engineering.agents.github_workos.policy import (
    ACTION_BRANCH_PLAN,
    ACTION_PUSH_COMMIT,
    ACTION_REAL_CODE_WRITE_REQUEST,
    ACTION_READY_PR,
)
from yule_engineering.agents.github_workos.triage import senior_triage


def _issue(title: str, body: str = "") -> dict:
    return {
        "number": 1,
        "title": title,
        "body": body,
        "html_url": "https://github.com/yule/foo/issues/1",
        "state": "open",
    }


class DomainRoutingTests(unittest.TestCase):
    """The 6 domain routing rows from the G2 spec."""

    def test_spring_boot_api_routes_backend_primary(self) -> None:
        request = build_request_from_github_issue(
            _issue(
                "Spring Boot API 설계",
                "POST /orders 엔드포인트를 추가하고 backend 쪽 검토가 필요해.",
            )
        )
        plan = senior_triage(request)
        self.assertEqual(plan.primary_role, "tech-lead")
        self.assertIn("backend-engineer", plan.support_roles)
        # frontend / product-designer must not be active for a
        # backend API design ticket.
        self.assertNotIn("frontend-engineer", plan.support_roles)
        self.assertNotIn("product-designer", plan.support_roles)
        self.assertIn("frontend-engineer", plan.excluded_roles)
        self.assertIn("product-designer", plan.excluded_roles)

    def test_nextjs_landing_routes_designer_and_frontend(self) -> None:
        request = build_request_from_github_issue(
            _issue(
                "Next.js 랜딩 UI 개선",
                "사용자 흐름과 UI 컴포넌트 정리가 필요. 프론트 / 디자인 관점.",
            )
        )
        plan = senior_triage(request)
        self.assertEqual(plan.primary_role, "tech-lead")
        active = (plan.primary_role,) + plan.support_roles
        self.assertIn("frontend-engineer", active)
        self.assertIn("product-designer", active)
        self.assertIn("backend-engineer", plan.excluded_roles)

    def test_github_actions_deploy_failure_routes_devops(self) -> None:
        request = build_request_from_github_issue(
            _issue(
                "GitHub Actions 배포 실패",
                "워크플로우 deploy 단계가 깨졌다. CI 가 더 이상 통과하지 않는다.",
            )
        )
        plan = senior_triage(request)
        active = (plan.primary_role,) + plan.support_roles
        self.assertIn("devops-engineer", active)
        # product-designer / frontend 은 본 ops 이슈와 무관해 excluded 여야 한다.
        self.assertIn("product-designer", plan.excluded_roles)
        self.assertIn("frontend-engineer", plan.excluded_roles)

    def test_test_strategy_routes_qa(self) -> None:
        request = build_request_from_github_issue(
            _issue(
                "회귀 테스트 전략 정리",
                "신규 기능에 대한 qa 회귀 테스트 범위를 정해야 한다.",
            )
        )
        plan = senior_triage(request)
        active = (plan.primary_role,) + plan.support_roles
        self.assertIn("qa-engineer", active)

    def test_ai_rag_routes_ai_engineer(self) -> None:
        request = build_request_from_github_issue(
            _issue(
                "AI / RAG 구조 검토",
                "현재 agent / RAG memory 구성에 대한 검토.",
            )
        )
        plan = senior_triage(request)
        active = (plan.primary_role,) + plan.support_roles
        self.assertIn("ai-engineer", active)

    def test_all_team_review_activates_every_role(self) -> None:
        request = build_request_from_github_issue(
            _issue(
                "전체 팀 관점 리스크 리뷰",
                "이번 분기 전체 팀 관점에서 위험 요소를 한 번 보고 싶다.",
            )
        )
        plan = senior_triage(request)
        active_set = set(plan.support_roles) | {plan.primary_role}
        for role in (
            "tech-lead",
            "backend-engineer",
            "frontend-engineer",
            "devops-engineer",
            "qa-engineer",
            "ai-engineer",
            "product-designer",
        ):
            self.assertIn(role, active_set, msg=f"all-team must include {role}")
        # And the excluded list is empty when every role is active.
        self.assertEqual(plan.excluded_roles, ())


class VagueRequestTests(unittest.TestCase):
    """Unknown / vague prompts must NOT fan out to all roles."""

    def test_vague_prompt_keeps_techlead_only(self) -> None:
        request = build_request_from_github_issue(
            _issue("뭔가 봐줘", "이번 주에 뭔가 처리할 게 있는데 잘 모르겠어.")
        )
        plan = senior_triage(request)
        self.assertEqual(plan.primary_role, "tech-lead")
        # Must NOT auto-include the legacy ai/backend/qa quartet.
        for role in (
            "backend-engineer",
            "frontend-engineer",
            "devops-engineer",
            "qa-engineer",
            "ai-engineer",
            "product-designer",
        ):
            self.assertNotIn(
                role,
                plan.support_roles,
                msg=f"vague prompt must not auto-add {role} as support",
            )
            self.assertIn(
                role,
                plan.excluded_roles,
                msg=f"vague prompt must record {role} in excluded_roles",
            )

    def test_empty_prompt_keeps_techlead_only(self) -> None:
        request = build_request_from_github_issue(_issue("", ""))
        plan = senior_triage(request)
        self.assertEqual(plan.primary_role, "tech-lead")
        self.assertEqual(plan.support_roles, ())


class CodingGateTests(unittest.TestCase):
    def test_discord_coding_request_marks_coding_required(self) -> None:
        request = build_request_from_discord_intake(
            "코딩해서 PR 올려줘. backend OrderController 에 새 엔드포인트.",
            message_id="abc",
            sender="codwithyc",
            channel="업무-접수",
        )
        plan = senior_triage(request)
        self.assertTrue(plan.coding_required)
        self.assertTrue(plan.approval_required_before_write)
        # Approval gate names canonical ACTION_* ids.
        for action in (
            ACTION_BRANCH_PLAN,
            ACTION_REAL_CODE_WRITE_REQUEST,
            ACTION_PUSH_COMMIT,
            ACTION_READY_PR,
        ):
            self.assertIn(action, plan.approval_required_actions)
        # Decision text mentions tech-lead approval gate.
        joined = " | ".join(plan.decisions)
        self.assertIn("approval", joined.lower())
        # Autonomy level is L3+ (real write or destructive).
        self.assertIn(
            plan.autonomy_level,
            (PermissionLevel.L3_REAL_WRITE, PermissionLevel.L4_DESTRUCTIVE),
        )

    def test_pure_research_request_does_not_set_coding_required(self) -> None:
        request = build_request_from_discord_intake(
            "오늘은 k8s 쿠버네티스에 대해서 자료 조사만 해줘. 코드는 안 만져도 돼.",
            message_id="xyz",
        )
        plan = senior_triage(request)
        self.assertFalse(plan.coding_required)
        self.assertFalse(plan.approval_required_before_write)
        self.assertEqual(plan.approval_required_actions, ())

    def test_english_pr_request_marks_coding_required(self) -> None:
        request = build_request_from_discord_intake(
            "implement the rate limiter and open a pr",
            message_id="m1",
        )
        plan = senior_triage(request)
        self.assertTrue(plan.coding_required)


class WorkOrderTests(unittest.TestCase):
    def test_active_role_gets_work_order(self) -> None:
        request = build_request_from_github_issue(
            _issue(
                "Spring Boot API 설계",
                "backend 쪽 새 엔드포인트.",
            )
        )
        plan = senior_triage(request)
        active_roles = {plan.primary_role, *plan.support_roles}
        order_roles = {wo.role for wo in plan.role_work_orders}
        self.assertEqual(active_roles, order_roles)
        # All work orders are non-empty in the contract surfaces.
        for wo in plan.role_work_orders:
            self.assertIsInstance(wo, RoleWorkOrder)
            self.assertTrue(wo.mission)
            self.assertTrue(wo.expected_output)
            self.assertTrue(wo.files_or_domains_to_inspect)
            self.assertTrue(wo.done_criteria)

    def test_excluded_role_does_not_get_work_order(self) -> None:
        request = build_request_from_github_issue(
            _issue(
                "Spring Boot API 설계",
                "backend 쪽 새 엔드포인트.",
            )
        )
        plan = senior_triage(request)
        order_roles = {wo.role for wo in plan.role_work_orders}
        for excluded in plan.excluded_roles:
            self.assertNotIn(
                excluded,
                order_roles,
                msg=f"excluded role {excluded} must not have a work order",
            )

    def test_handoff_chain_terminates_at_last_active_role(self) -> None:
        request = build_request_from_github_issue(
            _issue(
                "AI / RAG 구조 검토",
                "agent / RAG memory + backend 쪽 작은 통합.",
            )
        )
        plan = senior_triage(request)
        orders = list(plan.role_work_orders)
        self.assertGreaterEqual(len(orders), 2)
        # Last work order has no handoff.
        self.assertIsNone(orders[-1].handoff_to_next_role)
        # Each non-last order hands off to the next role in the list.
        for i in range(len(orders) - 1):
            self.assertEqual(
                orders[i].handoff_to_next_role,
                orders[i + 1].role,
            )


class RationaleTests(unittest.TestCase):
    def test_excluded_rationale_present_for_each_excluded_role(self) -> None:
        request = build_request_from_github_issue(
            _issue("Spring Boot API 설계", "backend 검토")
        )
        plan = senior_triage(request)
        for excluded in plan.excluded_roles:
            self.assertIn(excluded, plan.rationale_by_role)
            self.assertTrue(plan.rationale_by_role[excluded].strip())

    def test_active_role_rationale_present(self) -> None:
        request = build_request_from_github_issue(
            _issue("Spring Boot API 설계", "backend 검토")
        )
        plan = senior_triage(request)
        for role in (plan.primary_role,) + plan.support_roles:
            self.assertIn(role, plan.rationale_by_role)


class BranchSuggestionTests(unittest.TestCase):
    def test_bug_fix_request_uses_fix_prefix(self) -> None:
        request = build_request_from_github_issue(
            _issue("로그인 버그 픽스", "로그인 시 토큰이 새로 발급 안 되는 결함")
        )
        plan = senior_triage(request)
        self.assertTrue(plan.suggested_branch.startswith("fix/"))

    def test_devops_request_uses_ops_prefix(self) -> None:
        request = build_request_from_github_issue(
            _issue("배포 파이프라인 정리", "deploy 단계 워크플로우 정리")
        )
        plan = senior_triage(request)
        self.assertTrue(plan.suggested_branch.startswith("ops/"))


if __name__ == "__main__":
    unittest.main()
