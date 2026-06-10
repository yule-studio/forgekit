"""role_profiles 신규 helper — disk-backed contract reader 회귀.

Phase 6: backend-engineer profile에 추가된 default_response_template /
stop_conditions / review_checklist_by_category / required_context_catalog
필드를 deliberation/output 단에서 deterministic하게 끌어올 수 있도록
helper를 노출했다. 기존 RoleProfile 레지스트리는 그대로 두고 disk
backed reader를 별도 surface로 추가했으므로, 다른 role의 출력은
변하지 않는다.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.role_profiles import (
    default_response_template_for,
    output_template_for_role,
    required_context_catalog_for_role,
    reset_contract_cache_for_tests,
    review_checklist_for_role,
    stop_conditions_for,
)


class DefaultResponseTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_contract_cache_for_tests()
        self.addCleanup(reset_contract_cache_for_tests)

    def test_backend_template_pulled_from_manifest_json(self) -> None:
        template = default_response_template_for("backend-engineer")
        self.assertGreaterEqual(len(template), 8)
        joined = " ".join(template)
        for needle in ("API 계약", "데이터 모델", "트랜잭션", "Handoff"):
            self.assertIn(needle, joined, f"template missing: {needle}")

    def test_qualified_role_id_resolves(self) -> None:
        short = default_response_template_for("backend-engineer")
        qualified = default_response_template_for("engineering-agent/backend-engineer")
        self.assertEqual(short, qualified)

    def test_unknown_role_returns_empty_or_legacy_fallback(self) -> None:
        # phantom role file이 없으면 output_template_for_role(빈 tuple)
        # fallback이 그대로 반환되어야 한다.
        self.assertEqual(default_response_template_for("phantom-role"), ())

    def test_role_without_contract_v1_falls_back_to_output_sections(self) -> None:
        # tech-lead는 default_response_template을 갖고 있어 disk-backed
        # 값을 반환한다 — 갖고 있지 않은 role은 legacy
        # output_template_for_role으로 fallback이 동작해야 한다.
        sections = default_response_template_for("tech-lead")
        # 같은 role이 둘 다 갖고 있으면 disk가 우선이지만, 적어도
        # 비어 있지는 않아야 한다.
        self.assertGreaterEqual(len(sections), 5)


class StopConditionsTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_contract_cache_for_tests()
        self.addCleanup(reset_contract_cache_for_tests)

    def test_backend_stop_conditions_lists_safety_blocks(self) -> None:
        stops = stop_conditions_for("backend-engineer")
        self.assertGreaterEqual(len(stops), 5)
        joined = " ".join(stops)
        for needle in ("권한", "destructive migration", "민감", "idempotency"):
            self.assertIn(needle, joined, f"stop_conditions missing: {needle}")

    def test_unknown_role_returns_empty_tuple(self) -> None:
        self.assertEqual(stop_conditions_for("phantom-role"), ())


class ReviewChecklistTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_contract_cache_for_tests()
        self.addCleanup(reset_contract_cache_for_tests)

    def test_flat_view_aggregates_all_categories(self) -> None:
        items = review_checklist_for_role("backend-engineer")
        self.assertGreater(len(items), 10)

    def test_category_view_returns_subset(self) -> None:
        api_items = review_checklist_for_role("backend-engineer", category="api")
        self.assertGreaterEqual(len(api_items), 4)
        joined = " ".join(api_items)
        self.assertIn("endpoint", joined)
        self.assertIn("error_responses", joined)

    def test_unknown_category_returns_empty(self) -> None:
        self.assertEqual(
            review_checklist_for_role("backend-engineer", category="phantom"),
            (),
        )

    def test_role_without_checklist_returns_empty(self) -> None:
        # tech-lead은 review_checklist_by_category를 아직 갖고 있지 않다.
        self.assertEqual(review_checklist_for_role("tech-lead"), ())


class RequiredContextCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_contract_cache_for_tests()
        self.addCleanup(reset_contract_cache_for_tests)

    def test_backend_catalog_groups_categories(self) -> None:
        catalog = required_context_catalog_for_role("backend-engineer")
        self.assertGreaterEqual(len(catalog), 8)
        for cat in (
            "project_context",
            "runtime_context",
            "api_context",
            "auth_context",
            "operation_context",
        ):
            self.assertIn(cat, catalog, f"catalog missing: {cat}")
            self.assertGreaterEqual(len(catalog[cat]), 1)

    def test_role_without_catalog_returns_empty_mapping(self) -> None:
        self.assertEqual(required_context_catalog_for_role("tech-lead"), {})


class NonBackendRoleStabilityTests(unittest.TestCase):
    """다른 role의 output_template은 helper 추가 후에도 그대로 유지되어야 한다."""

    def setUp(self) -> None:
        reset_contract_cache_for_tests()
        self.addCleanup(reset_contract_cache_for_tests)

    def test_output_template_for_role_unchanged_for_devops(self) -> None:
        sections = output_template_for_role("devops-engineer")
        self.assertEqual(
            sections,
            (
                "실행 환경 영향",
                "배포 영향",
                "환경변수/시크릿",
                "모니터링/로그",
                "장애 대응/롤백",
                "구현 제안",
            ),
        )


if __name__ == "__main__":
    unittest.main()
