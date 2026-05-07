"""Tech-lead role contract v1 — 강화된 필드 회귀 보장.

tech-lead profile은 단순 진행자가 아니라 요청 해석, 범위 통제, 역할 선정,
충돌 조정, 최종 의사결정, 사용자 보고를 담당하는 engineering lead로
계약되어 있다. 운영 중 다른 변경이 이 계약을 깨지 않도록 핵심 필드/
규칙을 회귀로 고정한다.

다른 role도 비슷한 강화를 받지만, 이번 회귀는 tech-lead에 한정한다 —
공통 회귀(모든 role이 가져야 하는 필드)는 별도 회귀로 분리한다.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


REPO_ROOT = Path(__file__).resolve().parents[2]
TECH_LEAD_PROFILE = (
    REPO_ROOT / "agents" / "engineering-agent" / "tech-lead" / "agent.json"
)


def _load() -> dict:
    return json.loads(TECH_LEAD_PROFILE.read_text(encoding="utf-8"))


class CorePrincipleAndModeFieldsTests(unittest.TestCase):
    def test_core_principles_are_listed(self) -> None:
        profile = _load()
        principles = profile.get("core_principles") or []
        self.assertIsInstance(principles, list)
        self.assertGreaterEqual(len(principles), 6)
        # 핵심 원칙 키워드: 사용자 최종 목표 분리, 필요한 role만,
        # research vs code, 사용자 승인 등이 있어야 한다.
        joined = " ".join(principles)
        for needle in ("최종 목표", "필요한 role", "research", "승인"):
            self.assertIn(needle, joined, f"core_principles missing: {needle}")

    def test_operating_modes_cover_lifecycle(self) -> None:
        modes = _load().get("operating_modes") or {}
        self.assertIsInstance(modes, dict)
        for mode in (
            "intake_triage",
            "role_selection",
            "research_planning",
            "deliberation_moderation",
            "synthesis",
            "approval_handoff",
            "execution_planning",
            "postmortem",
        ):
            self.assertIn(mode, modes, f"operating_modes missing: {mode}")
            entry = modes[mode]
            self.assertIn("required_output", entry, f"{mode}: required_output missing")
            self.assertGreaterEqual(len(entry["required_output"]), 1)

    def test_phase_advanced_past_skeleton(self) -> None:
        # Phase = role-contract-v1. selector / aggregator / status
        # 진단이 phase 값을 보고 "어떤 단계인지" 표시할 수 있도록 stable 키 유지.
        self.assertEqual(_load().get("phase"), "role-contract-v1")


class ReasoningFlowAndQuestionsTests(unittest.TestCase):
    def test_reasoning_flow_is_long_enough(self) -> None:
        flow = _load().get("reasoning_flow") or []
        self.assertGreaterEqual(len(flow), 8)

    def test_required_questions_cover_scope_and_approval(self) -> None:
        questions = _load().get("required_questions") or []
        joined = " ".join(questions)
        for needle in ("최종", "범위", "승인", "research"):
            self.assertIn(needle, joined, f"required_questions missing: {needle}")


class StandardFieldsTests(unittest.TestCase):
    """tech-lead에 추가된 7개 standard 필드의 형태와 핵심 키워드를 고정한다."""

    def setUp(self) -> None:
        self.profile = _load()

    def _standard(self, name: str) -> dict:
        value = self.profile.get(name)
        self.assertIsInstance(value, dict, f"{name} should be a dict")
        return value

    def test_role_selection_standard_lists_required_checks(self) -> None:
        std = self._standard("role_selection_standard")
        self.assertIn("must_check", std)
        self.assertIn("rules", std)
        joined = " ".join(std["must_check"])
        self.assertIn("tech-lead", joined)
        self.assertIn("research-only", joined)

    def test_scope_control_standard_separates_user_goal_and_task(self) -> None:
        std = self._standard("scope_control_standard")
        joined = " ".join(std.get("must_separate", []))
        self.assertIn("최종 목표", joined)
        self.assertIn("범위", joined)

    def test_synthesis_standard_includes_consensus_and_open_research(self) -> None:
        std = self._standard("synthesis_standard")
        items = " ".join(std.get("must_include", []))
        self.assertIn("consensus_summary", items)
        self.assertIn("open_research", items)

    def test_decision_standard_requires_rationale(self) -> None:
        std = self._standard("decision_standard")
        items = " ".join(std.get("must_include", []))
        self.assertIn("rationale", items)
        self.assertIn("tradeoffs", items)

    def test_risk_review_standard_lists_data_and_security(self) -> None:
        std = self._standard("risk_review_standard")
        items = " ".join(std.get("must_check", []))
        self.assertIn("데이터", items)
        self.assertIn("보안", items)

    def test_approval_handoff_standard_includes_executor_and_scope(self) -> None:
        std = self._standard("approval_handoff_standard")
        items = " ".join(std.get("must_include", []))
        self.assertIn("executor_role", items)
        self.assertIn("write_scope", items)
        self.assertIn("forbidden_scope", items)


class DefaultResponseTemplateTests(unittest.TestCase):
    def test_template_includes_request_interpretation_and_next_actions(self) -> None:
        template = _load().get("default_response_template") or []
        self.assertIsInstance(template, list)
        self.assertGreaterEqual(len(template), 7)
        joined = " ".join(template)
        for needle in (
            "요청 해석",
            "이번 작업 범위",
            "선택된 역할",
            "다음 액션",
            "승인",
        ):
            self.assertIn(needle, joined, f"template missing: {needle}")


class StopConditionAndEscalationTests(unittest.TestCase):
    def test_stop_conditions_block_unsafe_handoff(self) -> None:
        stops = _load().get("stop_conditions") or []
        self.assertGreaterEqual(len(stops), 5)
        joined = " ".join(stops)
        # research-only가 code-change로 끼어드는 경우 + 승인 필요 결정
        # + executor/write scope 미정 케이스가 명시되어야 한다.
        self.assertIn("research-only", joined)
        self.assertIn("승인", joined)
        self.assertIn("scope", joined)

    def test_escalation_triggers_cover_user_and_member_roles(self) -> None:
        triggers = _load().get("escalation_triggers") or {}
        self.assertIsInstance(triggers, dict)
        for target in (
            "user",
            "ai-engineer",
            "backend-engineer",
            "frontend-engineer",
            "devops-engineer",
            "qa-engineer",
            "product-designer",
        ):
            self.assertIn(target, triggers, f"escalation target missing: {target}")
            entries = triggers[target]
            self.assertGreaterEqual(len(entries), 1)


class ResearchOnlyHandoffSafetyTests(unittest.TestCase):
    """research-only 요청에서 coding authorization이 자동으로 발생하지 않도록
    명시되어 있는지 확인한다 — tech-lead 합의 흐름의 안전 가드."""

    def test_decision_criteria_blocks_unauthorized_destructive_writes(self) -> None:
        criteria = _load().get("decision_criteria") or []
        joined = " ".join(criteria)
        self.assertIn("승인", joined)
        self.assertIn("destructive", joined)

    def test_stop_conditions_mention_research_only_split(self) -> None:
        stops = _load().get("stop_conditions") or []
        joined = " ".join(stops)
        self.assertIn("research-only", joined)
        self.assertIn("code-change", joined)


if __name__ == "__main__":
    unittest.main()
