"""product-designer role contract v1 — 강화된 필드 회귀 보장.

product-designer profile은 단순 시각 디자인 담당이 아니라 사용자 목표,
정보 구조, UX flow, UX copy, 시각적 위계, 디자인 reference 판단,
사용성 리스크를 책임지는 제품 디자이너로 계약되어 있다. 사용자 목표/
정보 구조/UX 문구가 빠진 채로 화면이 ship되지 않도록 회귀로 고정.
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
    REPO_ROOT / "agents" / "engineering-agent" / "product-designer" / "manifest.json"
)


def _load() -> dict:
    return json.loads(PROFILE.read_text(encoding="utf-8"))


class CorePrincipleAndModeFieldsTests(unittest.TestCase):
    def test_core_principles_focus_on_user_goal_and_structure(self) -> None:
        principles = _load().get("core_principles") or []
        joined = " ".join(principles)
        for needle in ("사용자", "목표", "정보 구조", "UX copy", "MVP", "접근성"):
            self.assertIn(needle, joined, f"core_principles missing: {needle}")

    def test_operating_modes_cover_lifecycle(self) -> None:
        modes = _load().get("operating_modes") or {}
        for mode in (
            "product_flow_design",
            "information_architecture",
            "ux_copy_design",
            "visual_direction",
            "design_review",
            "usability_risk_review",
            "portfolio_design",
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

    def test_user_goal_standard_requires_one_sentence(self) -> None:
        std = self._std("user_goal_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("primary_user", "user_goal_in_one_sentence", "user_motivation"):
            self.assertIn(needle, items, f"user_goal missing: {needle}")

    def test_user_flow_standard_includes_alternate_and_failure(self) -> None:
        std = self._std("user_flow_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("entry_points", "primary_flow_steps", "alternate_flows", "exit_or_failure_paths"):
            self.assertIn(needle, items, f"user_flow missing: {needle}")

    def test_information_architecture_standard_lists_priority(self) -> None:
        std = self._std("information_architecture_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("site_map", "screen_priority", "navigation_grouping", "content_hierarchy"):
            self.assertIn(needle, items, f"ia missing: {needle}")

    def test_ux_copy_standard_covers_all_states(self) -> None:
        std = self._std("ux_copy_standard")
        items = " ".join(std.get("must_include", []))
        for needle in (
            "primary_actions_copy",
            "error_state_copy",
            "empty_state_copy",
            "permission_or_blocked_copy",
            "tone_guideline",
        ):
            self.assertIn(needle, items, f"ux_copy missing: {needle}")

    def test_visual_hierarchy_standard_covers_typography_and_color(self) -> None:
        std = self._std("visual_hierarchy_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("typography_scale", "color_tokens", "spacing_system", "primary_focus_target"):
            self.assertIn(needle, items, f"visual missing: {needle}")

    def test_accessibility_standard_lists_keyboard_and_aria(self) -> None:
        std = self._std("accessibility_standard")
        joined = " ".join(std.get("must_check", []))
        for needle in ("키보드", "스크린 리더", "색 대비", "포커스"):
            self.assertIn(needle, joined, f"a11y missing: {needle}")

    def test_design_reference_standard_requires_reason(self) -> None:
        std = self._std("design_reference_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("reference_source", "reason_for_reference", "what_to_borrow", "license_or_attribution"):
            self.assertIn(needle, items, f"reference missing: {needle}")

    def test_usability_test_standard_includes_hypothesis(self) -> None:
        std = self._std("usability_test_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("hypothesis", "test_method", "participant_profile", "success_metrics"):
            self.assertIn(needle, items, f"usability missing: {needle}")


class DefaultResponseTemplateTests(unittest.TestCase):
    def test_template_includes_user_goal_ia_copy(self) -> None:
        template = _load().get("default_response_template") or []
        joined = " ".join(template)
        for needle in (
            "사용자 목표",
            "사용자 흐름",
            "정보 구조",
            "UX 문구",
            "디자인 reference",
            "MVP",
            "handoff",
        ):
            self.assertIn(needle, joined, f"template missing: {needle}")


class StopConditionAndEscalationTests(unittest.TestCase):
    def test_stop_conditions_block_ship_without_user_goal_or_copy(self) -> None:
        stops = _load().get("stop_conditions") or []
        self.assertGreaterEqual(len(stops), 5)
        joined = " ".join(stops)
        for needle in ("사용자 목표", "UX copy", "디자인 시스템", "MVP", "흐름", "접근성", "reference"):
            self.assertIn(needle, joined, f"stop_conditions missing: {needle}")

    def test_escalation_triggers_cover_engineering_roles(self) -> None:
        triggers = _load().get("escalation_triggers") or {}
        for target in (
            "tech-lead",
            "frontend-engineer",
            "backend-engineer",
            "qa-engineer",
            "ai-engineer",
        ):
            self.assertIn(target, triggers, f"escalation missing: {target}")


class InfraExclusionTests(unittest.TestCase):
    """backend/infra-only 요청에서는 product-designer가 primary가 되지
    않도록 default_executor_priority가 디자인 도메인 키워드로
    응집되어 있는지 확인."""

    def test_executor_priority_is_design_focused(self) -> None:
        priority = _load().get("default_executor_priority") or {}
        joined = " ".join(priority.get("high", [])).lower()
        for needle in ("ux", "디자인", "사용자 흐름"):
            self.assertIn(needle, joined, f"priority missing: {needle}")


if __name__ == "__main__":
    unittest.main()
