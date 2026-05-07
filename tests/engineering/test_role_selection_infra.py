"""Live MVP regression — role selection for k8s / infra / RAG prompts.

Pin the live-bug ask:

  • k8s / Kubernetes / cluster / container / orchestration / Helm /
    ingress / service-mesh / observability / deployment requests stay
    centred on tech-lead + devops-engineer + backend-engineer.
  • qa-engineer joins only when test / regression / acceptance is
    mentioned.
  • ai-engineer joins only when AI / RAG / LLM / agent / memory is
    mentioned.
  • frontend-engineer joins only when UI / dashboard is mentioned.
  • product-designer joins only when UX / design / screen / reference
    is mentioned.
  • Explicit "tech-lead / ai / backend / qa / devops 관점" still wins
    over the rule bank.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.lifecycle.role_selection import (
    SOURCE_TECH_LEAD_RULE,
    SOURCE_USER_EXPLICIT,
    recommend_active_roles,
)


class K8sResearchOnlyTests(unittest.TestCase):
    """Live live-test prompt: '오늘은 k8s 쿠버네티스에 대해서 다루고
    싶어. 어떤 지식들이 필요할까? 오늘은 코드 수정 없이 자료 수집이
    목표야.' — must NOT pull in product-designer / ai-engineer /
    qa-engineer."""

    PROMPT = (
        "오늘은 k8s 쿠버네티스에 대해서 다루고 싶어. "
        "어떤 지식들이 필요할까? "
        "오늘은 코드 수정 없이 자료 수집이 목표야."
    )

    def test_selects_only_techlead_devops_backend(self) -> None:
        selection = recommend_active_roles(user_prompt=self.PROMPT)
        self.assertEqual(selection.selection_source, SOURCE_TECH_LEAD_RULE)
        self.assertIn("tech-lead", selection.selected_roles)
        self.assertIn("devops-engineer", selection.selected_roles)
        self.assertIn("backend-engineer", selection.selected_roles)
        # The live regression — these must NOT auto-join a k8s
        # research-only request.
        self.assertNotIn("ai-engineer", selection.selected_roles)
        self.assertNotIn("qa-engineer", selection.selected_roles)
        self.assertNotIn("product-designer", selection.selected_roles)
        self.assertNotIn("frontend-engineer", selection.selected_roles)
        # Excluded list explains the silence to the supervisor.
        self.assertIn("ai-engineer", selection.excluded_roles)
        self.assertIn("qa-engineer", selection.excluded_roles)

    def test_kubernetes_korean_synonyms_route_to_devops(self) -> None:
        selection = recommend_active_roles(
            user_prompt="쿠버네티스 클러스터 ingress 운영 검토"
        )
        self.assertIn("devops-engineer", selection.selected_roles)


class K8sWithTestStrategyTests(unittest.TestCase):
    def test_qa_joins_when_regression_mentioned(self) -> None:
        selection = recommend_active_roles(
            user_prompt="k8s deployment 회귀 테스트 시나리오 정리"
        )
        self.assertIn("devops-engineer", selection.selected_roles)
        self.assertIn("qa-engineer", selection.selected_roles)


class K8sWithUiDashboardTests(unittest.TestCase):
    def test_frontend_joins_when_ui_mentioned(self) -> None:
        selection = recommend_active_roles(
            user_prompt="k8s 운영 대시보드 UI 검토"
        )
        self.assertIn("devops-engineer", selection.selected_roles)
        self.assertIn("frontend-engineer", selection.selected_roles)


class RagMemoryTests(unittest.TestCase):
    def test_rag_keyword_pulls_ai_engineer_via_explicit_mention(self) -> None:
        # Live live-test prompt #2 — explicit "tech-lead / ai-engineer
        # / backend-engineer / qa-engineer 관점" must keep exactly that
        # subset.
        selection = recommend_active_roles(
            user_prompt=(
                "RAG/CAG memory 구조를 조사해줘. "
                "tech-lead / ai-engineer / backend-engineer / "
                "qa-engineer 관점으로 토의해줘."
            )
        )
        self.assertEqual(selection.selection_source, SOURCE_USER_EXPLICIT)
        self.assertEqual(
            set(selection.selected_roles),
            {"tech-lead", "ai-engineer", "backend-engineer", "qa-engineer"},
        )
        # devops/frontend/product-designer NOT in the user's explicit
        # mention.
        self.assertIn("devops-engineer", selection.excluded_roles)
        self.assertIn("frontend-engineer", selection.excluded_roles)


class StrictUserExplicitOverridesRuleBankTests(unittest.TestCase):
    def test_user_explicit_with_devops_keeps_only_named_roles(self) -> None:
        selection = recommend_active_roles(
            user_prompt=(
                "tech-lead / ai-engineer / backend-engineer / "
                "qa-engineer / devops-engineer 관점에서 검토"
            )
        )
        self.assertEqual(selection.selection_source, SOURCE_USER_EXPLICIT)
        self.assertEqual(
            set(selection.selected_roles),
            {
                "tech-lead",
                "ai-engineer",
                "backend-engineer",
                "qa-engineer",
                "devops-engineer",
            },
        )
        self.assertIn("frontend-engineer", selection.excluded_roles)
        self.assertIn("product-designer", selection.excluded_roles)


if __name__ == "__main__":
    unittest.main()
