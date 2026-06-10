"""Phase 6 — TechLeadAggregator helpers.

Pin :func:`aggregate_role_outputs` and
:func:`build_tech_lead_summary_context`. These primitives merge per-role
notes into a single executable conclusion and feed the synthesis prompt
context, so a regression here is felt by every work_report and
status surface downstream.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.lifecycle.role_selection import (
    recommend_active_roles,
)
from yule_engineering.agents.tech_lead_aggregator import (
    AggregateResult,
    RoleAggregateNote,
    aggregate_role_outputs,
    build_tech_lead_summary_context,
)


def _note(role, **kwargs):
    return RoleAggregateNote(role=role, **kwargs)


class BuildSummaryContextTests(unittest.TestCase):
    def test_includes_canonical_prompt_and_role_notes(self) -> None:
        sel = recommend_active_roles(
            user_prompt="ai-engineer / backend-engineer 관점 정리"
        )
        ctx = build_tech_lead_summary_context(
            role_notes=[
                _note(
                    role="ai-engineer",
                    perspective="LLM RAG 정리 필요",
                    risks=("비용 한도 초과",),
                ),
                _note(
                    role="backend-engineer",
                    perspective="API 안정성 검토",
                    next_actions=("API 계약 정리",),
                ),
            ],
            selection=sel,
            canonical_prompt="RAG 도입 검토",
        )
        self.assertEqual(ctx["canonical_prompt"], "RAG 도입 검토")
        self.assertEqual(
            [n["role"] for n in ctx["role_notes"]],
            ["ai-engineer", "backend-engineer"],
        )
        self.assertIn("ai-engineer", ctx["selected_roles"])
        # forbidden_actions_by_role pulls the canonical hard-veto list
        # off the role profile registry.
        self.assertIn("ai-engineer", ctx["forbidden_actions_by_role"])

    def test_handles_dataclass_like_role_takes(self) -> None:
        # Real RoleTake instances have ``role`` / ``risks`` / ``next_actions``
        # attributes — coercion must accept them without explicit
        # conversion at the call site.
        class _FakeBackendTake:
            role = "engineering-agent/backend-engineer"
            perspective = "API 변경 안전성"
            risks = ("DB 마이그레이션 영향",)
            next_actions = ("마이그레이션 plan 정리",)
            decisions_needed = ("기존 endpoint 호환 유지 여부",)

        ctx = build_tech_lead_summary_context(
            role_notes=[_FakeBackendTake()],
            selection=None,
            canonical_prompt="결제 모듈 정리",
        )
        self.assertEqual(len(ctx["role_notes"]), 1)
        self.assertEqual(ctx["role_notes"][0]["role"], "backend-engineer")
        self.assertEqual(
            ctx["role_notes"][0]["decisions"],
            ["기존 endpoint 호환 유지 여부"],
        )

    def test_excluded_reasons_pulled_from_selection(self) -> None:
        sel = recommend_active_roles(
            user_prompt=(
                "오늘은 k8s 쿠버네티스에 대해서 다루고 싶어. "
                "코드 수정 없이 자료 수집이 목표야."
            ),
        )
        ctx = build_tech_lead_summary_context(
            role_notes=[],
            selection=sel,
            canonical_prompt="k8s 자료 수집",
        )
        self.assertIn("ai-engineer", ctx["excluded_roles"])
        # Excluded reasons mirror role_selection.reason_by_role when
        # populated; for k8s prompt the rule branch fires so excluded
        # reasons may stay empty (rule-branch records reasons for
        # selected only). Still ensure the key exists for the schema.
        self.assertIn("excluded_reasons", ctx)


class AggregateRoleOutputsTests(unittest.TestCase):
    def test_consensus_carries_canonical_prompt_and_first_perspective(self) -> None:
        result = aggregate_role_outputs(
            role_notes=[
                _note(role="tech-lead", perspective="작업 분해 후 backend primary"),
                _note(role="backend-engineer", perspective="API 변경 영향 정리"),
            ],
            selection=None,
            canonical_prompt="결제 멱등성 검토",
        )
        self.assertIsInstance(result, AggregateResult)
        self.assertIn("결제 멱등성 검토", result.consensus)
        self.assertIn("작업 분해", result.consensus)

    def test_risks_and_next_actions_are_deduplicated_in_role_order(self) -> None:
        result = aggregate_role_outputs(
            role_notes=[
                _note(
                    role="backend-engineer",
                    risks=("DB 영향", "트랜잭션 이슈"),
                    next_actions=("API 계약 갱신", "마이그레이션 plan"),
                ),
                _note(
                    role="qa-engineer",
                    risks=("DB 영향",),  # 중복 — dedupe 대상
                    next_actions=("회귀 시나리오", "API 계약 갱신"),  # 후자 중복
                ),
            ],
            canonical_prompt="결제 멱등성",
        )
        self.assertEqual(
            list(result.risks),
            ["DB 영향", "트랜잭션 이슈"],
        )
        self.assertEqual(
            list(result.next_actions),
            ["API 계약 갱신", "마이그레이션 plan", "회귀 시나리오"],
        )

    def test_research_only_blocks_executor_flag(self) -> None:
        result = aggregate_role_outputs(
            role_notes=[
                _note(
                    role="backend-engineer",
                    next_actions=("API endpoint 구현",),
                ),
            ],
            canonical_prompt="자료 수집",
            research_only=True,
        )
        # research_only=True 라면 next_actions 가 "구현" 을 포함해도
        # requires_executor 가 False 여야 한다 — research-only 에서는
        # 절대 코딩으로 자동 전환되지 않는다.
        self.assertFalse(result.requires_executor)
        self.assertIn("research-only", result.notes)
        # next_actions 자체는 유지(사용자가 다음 round에서 승인할 수 있게).
        self.assertIn("API endpoint 구현", result.next_actions)

    def test_user_decision_keyword_flips_requires_user_decision(self) -> None:
        result = aggregate_role_outputs(
            role_notes=[
                _note(
                    role="tech-lead",
                    decisions=("브랜드 톤은 사용자 결정 필요",),
                ),
            ],
            canonical_prompt="hero copy 분할",
        )
        self.assertTrue(result.requires_user_decision)

    def test_open_question_decisions_surface_separately(self) -> None:
        result = aggregate_role_outputs(
            role_notes=[
                _note(
                    role="ai-engineer",
                    decisions=("어떤 모델을 사용할지 검토 필요",),
                ),
            ],
            canonical_prompt="LLM 도입",
        )
        self.assertIn(
            "어떤 모델을 사용할지 검토 필요",
            result.open_questions,
        )

    def test_conflict_between_implement_and_hold(self) -> None:
        result = aggregate_role_outputs(
            role_notes=[
                _note(
                    role="backend-engineer",
                    next_actions=("API 변경 구현",),
                ),
                _note(
                    role="qa-engineer",
                    risks=("구현 보류 권고 — 회귀 시나리오 부족",),
                ),
            ],
            canonical_prompt="API 변경",
        )
        self.assertTrue(result.conflicts)
        self.assertIn("우선순위", result.conflicts[0])

    def test_excluded_roles_carry_into_aggregate(self) -> None:
        sel = recommend_active_roles(
            user_prompt=(
                "오늘은 k8s 쿠버네티스에 대해서 다루고 싶어. "
                "코드 수정 없이 자료 수집이 목표야."
            ),
        )
        result = aggregate_role_outputs(
            role_notes=[
                _note(role="devops-engineer", perspective="cluster 운영 정리"),
                _note(role="backend-engineer", perspective="runtime contract 점검"),
            ],
            selection=sel,
            canonical_prompt="k8s 자료 수집",
            research_only=True,
        )
        self.assertIn("ai-engineer", result.excluded_roles)
        self.assertIn("product-designer", result.excluded_roles)
        # research_only=True 이므로 코딩 트리거 없음.
        self.assertFalse(result.requires_executor)


if __name__ == "__main__":
    unittest.main()
