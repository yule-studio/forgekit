"""frontend-engineer role contract v1 — 강화된 필드 회귀 보장.

frontend-engineer profile은 단순 UI 구현자가 아니라 화면 흐름, 컴포넌트
구조, 상태 관리, API 연동, 접근성, 사용자 상태 표현을 책임지는
프론트엔드 엔지니어로 계약되어 있다. loading/error/empty 처리, API
계약, 접근성 가드가 누락되어 출시되지 않도록 회귀로 고정.
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
    REPO_ROOT / "agents" / "engineering-agent" / "frontend-engineer" / "manifest.json"
)


def _load() -> dict:
    return json.loads(PROFILE.read_text(encoding="utf-8"))


class CorePrincipleAndModeFieldsTests(unittest.TestCase):
    def test_core_principles_cover_states_and_api_contract(self) -> None:
        principles = _load().get("core_principles") or []
        joined = " ".join(principles)
        for needle in (
            "loading",
            "error",
            "empty",
            "API",
            "접근성",
            "디자인 시스템",
        ):
            self.assertIn(needle, joined, f"core_principles missing: {needle}")

    def test_operating_modes_cover_lifecycle(self) -> None:
        modes = _load().get("operating_modes") or {}
        for mode in (
            "ui_flow_design",
            "component_design",
            "api_integration",
            "state_modeling",
            "accessibility_review",
            "frontend_review",
            "debugging",
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

    def test_component_contract_standard_includes_states(self) -> None:
        std = self._std("component_contract_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("props", "events", "loading_error_empty_success_states", "accessibility_requirements"):
            self.assertIn(needle, items, f"component_contract missing: {needle}")

    def test_state_management_standard_separates_server_local_form(self) -> None:
        std = self._std("state_management_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("server_state_strategy", "local_state_strategy", "form_state_strategy", "ownership_boundary"):
            self.assertIn(needle, items, f"state_management missing: {needle}")

    def test_api_integration_standard_requires_error_and_auth(self) -> None:
        std = self._std("api_integration_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("endpoint_list", "auth_handling", "error_handling", "cache_or_freshness_policy"):
            self.assertIn(needle, items, f"api_integration missing: {needle}")

    def test_accessibility_standard_lists_keyboard_and_aria(self) -> None:
        std = self._std("accessibility_standard")
        joined = " ".join(std.get("must_check", []))
        for needle in ("키보드", "포커스", "스크린 리더", "색 대비"):
            self.assertIn(needle, joined, f"accessibility missing: {needle}")

    def test_responsive_standard_covers_breakpoints(self) -> None:
        std = self._std("responsive_standard")
        joined = " ".join(std.get("must_check", []))
        self.assertIn("모바일", joined)
        self.assertIn("breakpoint", joined)

    def test_error_empty_loading_standard_defines_all_states(self) -> None:
        std = self._std("error_empty_loading_standard")
        items = " ".join(std.get("must_define", []))
        for needle in ("loading_state", "empty_state", "error_state", "permission_denied_state", "retry_path"):
            self.assertIn(needle, items, f"states missing: {needle}")

    def test_performance_standard_covers_bundle_and_renders(self) -> None:
        std = self._std("performance_standard")
        joined = " ".join(std.get("must_check", []))
        self.assertIn("번들", joined)
        self.assertIn("재렌더", joined)

    def test_test_handoff_standard_includes_states_and_a11y(self) -> None:
        std = self._std("test_handoff_standard")
        items = " ".join(std.get("must_include", []))
        for needle in ("happy_path", "loading_state", "error_state", "permission_denied_state", "accessibility_check"):
            self.assertIn(needle, items, f"test_handoff missing: {needle}")


class DefaultResponseTemplateTests(unittest.TestCase):
    def test_template_includes_state_api_a11y(self) -> None:
        template = _load().get("default_response_template") or []
        joined = " ".join(template)
        for needle in (
            "화면 흐름",
            "컴포넌트 구조",
            "상태 관리",
            "API 연동",
            "loading",
            "접근성",
            "handoff",
        ):
            self.assertIn(needle, joined, f"template missing: {needle}")


class StopConditionAndEscalationTests(unittest.TestCase):
    def test_stop_conditions_block_unsafe_ship(self) -> None:
        stops = _load().get("stop_conditions") or []
        self.assertGreaterEqual(len(stops), 5)
        joined = " ".join(stops)
        for needle in ("API 계약", "loading", "접근성", "디자인 시스템", "권한별"):
            self.assertIn(needle, joined, f"stop_conditions missing: {needle}")

    def test_escalation_triggers_cover_member_roles(self) -> None:
        triggers = _load().get("escalation_triggers") or {}
        for target in (
            "tech-lead",
            "backend-engineer",
            "product-designer",
            "qa-engineer",
            "ai-engineer",
            "devops-engineer",
        ):
            self.assertIn(target, triggers, f"escalation missing: {target}")


class BackendOnlyExclusionTests(unittest.TestCase):
    """backend-only 요청에서는 frontend가 primary가 되지 않도록 default
    executor priority가 UI 도메인 키워드로 응집되어 있는지 확인."""

    def test_executor_priority_is_ui_focused(self) -> None:
        priority = _load().get("default_executor_priority") or {}
        joined = " ".join(priority.get("high", [])).lower()
        for needle in ("react", "ui", "css", "컴포넌트", "frontend"):
            self.assertIn(needle, joined, f"priority missing: {needle}")

    def test_forbidden_scope_blocks_backend_writes(self) -> None:
        forbidden = _load().get("forbidden_scope") or []
        joined = " ".join(forbidden)
        self.assertIn("backend", joined.lower())


if __name__ == "__main__":
    unittest.main()
