"""Phase 5 — RoleProfile.output_sections wiring into the runtime preface.

The role bot's open-call preface must surface the canonical output
section list so the bot's take (deterministic or LLM-driven) follows
the template defined on the role profile rather than inventing a new
section layout per turn.
"""

from __future__ import annotations

import unittest
from datetime import datetime

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.role_profiles import (
    forbidden_actions_for_role,
    output_template_for_role,
    required_context_for_role,
)


class OutputTemplateHelperTests(unittest.TestCase):
    def test_known_role_returns_sections_in_profile_order(self) -> None:
        sections = output_template_for_role("backend-engineer")
        self.assertEqual(
            sections,
            (
                "핵심 판단",
                "API 영향",
                "DB 영향",
                "트랜잭션/동시성",
                "예외 케이스",
                "구현 제안",
            ),
        )

    def test_qualified_role_id_resolves(self) -> None:
        # Both short and qualified role ids resolve to the same template.
        self.assertEqual(
            output_template_for_role("engineering-agent/devops-engineer"),
            output_template_for_role("devops-engineer"),
        )

    def test_unknown_role_returns_empty_tuple(self) -> None:
        # Caller can fall back to legacy unbounded format on miss.
        self.assertEqual(output_template_for_role("phantom-role"), ())
        self.assertEqual(output_template_for_role(""), ())


class ForbiddenAndRequiredHelperTests(unittest.TestCase):
    def test_forbidden_actions_pulled_from_profile(self) -> None:
        forbids = forbidden_actions_for_role("ai-engineer")
        # ai-engineer profile lists at least 3 forbidden_actions; we
        # don't pin exact wording so future copy edits don't fail
        # this regression — just that the channel is non-empty.
        self.assertGreaterEqual(len(forbids), 3)
        # Check at least one entry mentions the role's signature
        # forbidden category (cost / mask / 외부 API 호출 etc.).
        joined = " ".join(forbids)
        self.assertTrue(
            any(token in joined for token in ("비용", "마스킹", "출처", "필요 없는")),
            f"ai-engineer forbidden_actions seems off: {forbids}",
        )

    def test_required_context_for_qa_role(self) -> None:
        ctx = required_context_for_role("qa-engineer")
        joined = " ".join(ctx)
        self.assertIn("인수 조건", joined)
        self.assertIn("회귀", joined)


class RuntimePrefaceIncludesOutputTemplateTests(unittest.TestCase):
    """When the open-call handler builds the role-runtime preface, the
    canonical output_sections from the profile must show up so the
    role bot's take follows the template."""

    def setUp(self) -> None:
        from yule_discord.engineering_team_runtime import (
            reset_handled_turns_for_tests,
        )

        reset_handled_turns_for_tests()
        self.addCleanup(reset_handled_turns_for_tests)

    def _session(self):
        from yule_engineering.agents.workflow_state import (
            WorkflowSession,
            WorkflowState,
        )

        return WorkflowSession(
            session_id="sess-out",
            prompt=(
                "운영 환경 k8s 파이프라인 개선 — 자료 수집과 운영 점검 검토"
            ),
            task_type="research",
            state=WorkflowState.APPROVED,
            created_at=datetime(2026, 5, 7),
            updated_at=datetime(2026, 5, 7),
            role_sequence=("tech-lead", "devops-engineer", "backend-engineer"),
        )

    def test_devops_open_call_preface_lists_output_sections(self) -> None:
        from yule_discord.engineering_team_runtime import (
            handle_research_turn_message,
        )

        outcome = handle_research_turn_message(
            role="devops-engineer",
            text="[research-open:sess-out]",
            session_loader=lambda _sid: self._session(),
        )

        self.assertIsNotNone(outcome)
        msg = outcome.message
        self.assertIn("출력 섹션 템플릿", msg)
        # Preface must surface devops profile's first section name so
        # the bot's deterministic take has a stable template anchor.
        self.assertIn("실행 환경 영향", msg)


if __name__ == "__main__":
    unittest.main()
