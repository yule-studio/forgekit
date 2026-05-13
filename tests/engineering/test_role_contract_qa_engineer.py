"""qa-engineer role contract v1 — 강화된 필드 회귀 보장.

qa-engineer profile은 단순 테스트 작성자가 아니라 요구사항 검증, 인수
조건, 회귀 범위, 실패 시나리오, 릴리즈 차단 기준을 책임지는 품질
엔지니어로 계약되어 있다. 인수 조건/회귀/release blocker 가드가
누락된 채로 ship되지 않도록 회귀로 고정.
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
PROFILE = (
    REPO_ROOT / "agents" / "engineering-agent" / "qa-engineer" / "manifest.json"
)


def _load() -> dict:
    return json.loads(PROFILE.read_text(encoding="utf-8"))


class CorePrincipleAndModeFieldsTests(unittest.TestCase):
    def test_core_principles_focus_on_requirements_and_risk(self) -> None:
        principles = _load().get("core_principles") or []
        joined = " ".join(principles)
        for needle in ("요구사항", "실패", "위험", "인수 조건", "릴리즈"):
            self.assertIn(needle, joined, f"core_principles missing: {needle}")

    def test_operating_modes_cover_lifecycle(self) -> None:
        modes = _load().get("operating_modes") or {}
        for mode in (
            "acceptance_design",
            "test_planning",
            "regression_analysis",
            "bug_reproduction",
            "release_readiness_review",
            "automation_planning",
        ):
            self.assertIn(mode, modes, f"operating_modes missing: {mode}")
            self.assertGreaterEqual(len(modes[mode]["required_output"]), 1)

    def test_phase_advanced_past_skeleton(self) -> None:
        self.assertEqual(_load().get("phase"), "role-contract-v1")


class StandardFieldsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = _load()

    def _std(self, name: str) -> dict:
        v = self.profile.get(name)
        self.assertIsInstance(v, dict, f"{name} not a dict")
        return v

    def test_acceptance_criteria_standard_requires_measurability(self) -> None:
        std = self._std("acceptance_criteria_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("given_when_then", "measurable_outcome", "out_of_scope", "user_value"):
            self.assertIn(needle, items, f"acceptance missing: {needle}")

    def test_test_scenario_standard_includes_all_failure_axes(self) -> None:
        std = self._std("test_scenario_standard")
        items = " ".join(std.get("must_include", []))
        for needle in (
            "happy_path",
            "failure_cases",
            "boundary_cases",
            "permission_cases",
            "concurrency_cases",
            "external_dependency_failure",
            "rollback_or_recovery_case",
        ):
            self.assertIn(needle, items, f"scenario missing: {needle}")

    def test_regression_standard_requires_scope_and_smoke(self) -> None:
        std = self._std("regression_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("regression_scope", "high_risk_modules", "smoke_set", "regression_set"):
            self.assertIn(needle, items, f"regression missing: {needle}")

    def test_risk_based_testing_standard_lists_user_and_data_impact(self) -> None:
        std = self._std("risk_based_testing_standard")
        joined = " ".join(std.get("must_check", []))
        for needle in ("사용자 영향", "데이터", "보안", "외부 의존", "비가역"):
            self.assertIn(needle, joined, f"risk_based missing: {needle}")

    def test_bug_report_standard_requires_reproduction(self) -> None:
        std = self._std("bug_report_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("reproduction_steps", "expected_vs_actual", "severity", "minimum_repro_case"):
            self.assertIn(needle, items, f"bug_report missing: {needle}")

    def test_release_blocker_standard_defines_threshold(self) -> None:
        std = self._std("release_blocker_standard")
        items = " ".join(std.get("must_define", []))
        for needle in ("blocker_severity_threshold", "release_decision_owner"):
            self.assertIn(needle, items, f"release_blocker missing: {needle}")

    def test_automation_standard_evaluates_cost(self) -> None:
        std = self._std("automation_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("automation_candidates", "estimated_runtime", "maintenance_cost_estimate"):
            self.assertIn(needle, items, f"automation missing: {needle}")


class DefaultResponseTemplateTests(unittest.TestCase):
    def test_template_includes_acceptance_regression_blocker(self) -> None:
        template = _load().get("default_response_template") or []
        joined = " ".join(template)
        for needle in (
            "인수 조건",
            "정상 시나리오",
            "실패 시나리오",
            "회귀 범위",
            "릴리즈 차단",
            "handoff",
        ):
            self.assertIn(needle, joined, f"template missing: {needle}")


class StopConditionAndEscalationTests(unittest.TestCase):
    def test_stop_conditions_block_ship_without_acceptance_or_repro(self) -> None:
        stops = _load().get("stop_conditions") or []
        self.assertGreaterEqual(len(stops), 5)
        joined = " ".join(stops)
        for needle in ("인수 조건", "회귀", "재현 절차", "권한", "자동화"):
            self.assertIn(needle, joined, f"stop_conditions missing: {needle}")

    def test_escalation_triggers_cover_member_roles(self) -> None:
        triggers = _load().get("escalation_triggers") or {}
        for target in (
            "tech-lead",
            "backend-engineer",
            "frontend-engineer",
            "devops-engineer",
            "ai-engineer",
            "product-designer",
        ):
            self.assertIn(target, triggers, f"escalation missing: {target}")


class ResearchOnlyExclusionTests(unittest.TestCase):
    """research-only / infra research 요청에서 qa가 과도하게 primary가
    되지 않도록 default_executor_priority가 테스트 도메인 키워드로
    응집되어 있는지 확인한다."""

    def test_executor_priority_is_test_focused(self) -> None:
        priority = _load().get("default_executor_priority") or {}
        joined = " ".join(priority.get("high", [])).lower()
        for needle in ("test", "regression", "smoke", "acceptance"):
            self.assertIn(needle, joined, f"priority missing: {needle}")

    def test_decision_criteria_focuses_on_quality_gates(self) -> None:
        criteria = _load().get("decision_criteria") or []
        joined = " ".join(criteria)
        # 테스트 차원이 명확히 들어가는지
        self.assertTrue(
            any(kw in joined for kw in ("테스트", "회귀", "인수", "검증")),
            f"decision_criteria 품질 도메인 회귀 실패: {criteria}",
        )


if __name__ == "__main__":
    unittest.main()
