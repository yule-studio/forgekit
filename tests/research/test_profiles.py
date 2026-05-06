from __future__ import annotations

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

import unittest

from yule_orchestrator.agents.research_profiles import (
    ALL_ROLES,
    ALL_SOURCE_TYPES,
    ROLE_AI_ENGINEER,
    ROLE_BACKEND_ENGINEER,
    ROLE_FRONTEND_ENGINEER,
    ROLE_PRODUCT_DESIGNER,
    ROLE_QA_ENGINEER,
    ROLE_TECH_LEAD,
    SOURCE_TYPE_AI_FRAMEWORK_DOCS,
    SOURCE_TYPE_CODE_CONTEXT,
    SOURCE_TYPE_DESIGN_REFERENCE,
    SOURCE_TYPE_GITHUB_ISSUE,
    SOURCE_TYPE_IMAGE_REFERENCE,
    SOURCE_TYPE_MODEL_DOCS,
    SOURCE_TYPE_OFFICIAL_DOCS,
    SOURCE_TYPE_RESEARCH_PAPER,
    RoleResearchProfile,
    build_role_query_hints,
    format_research_hints_block,
    get_role_profile,
    list_role_profiles,
    replace_role_profile_for_tests,
)


def _top_n_source_types(hints, n: int) -> list[str]:
    return [source_type for source_type, _weight in hints.weighted_source_types[:n]]


class DefaultProfilesTestCase(unittest.TestCase):
    def test_each_canonical_role_has_a_profile(self) -> None:
        for role in ALL_ROLES:
            profile = get_role_profile(role)
            self.assertIsInstance(profile, RoleResearchProfile)
            self.assertEqual(profile.role, role)
            self.assertGreater(len(profile.preferred_source_types), 0)
            self.assertGreater(len(profile.suggested_queries), 0)

    def test_unknown_role_raises_with_available_list(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            get_role_profile("marketing-engineer")
        self.assertIn("marketing-engineer", str(ctx.exception))
        self.assertIn("tech-lead", str(ctx.exception))

    def test_ai_engineer_default_top_priorities_are_ai_focused(self) -> None:
        profile = get_role_profile(ROLE_AI_ENGINEER)
        # 1순위: official_docs (모델/API 사양)
        self.assertEqual(profile.preferred_source_types[0], SOURCE_TYPE_OFFICIAL_DOCS)
        # 2~4위에 research_paper, model_docs, ai_framework_docs가 들어가야 함
        ai_specific = {
            SOURCE_TYPE_RESEARCH_PAPER,
            SOURCE_TYPE_MODEL_DOCS,
            SOURCE_TYPE_AI_FRAMEWORK_DOCS,
        }
        self.assertTrue(ai_specific.issubset(set(profile.preferred_source_types)))

    def test_new_ai_source_types_are_registered(self) -> None:
        for source_type in (
            SOURCE_TYPE_RESEARCH_PAPER,
            SOURCE_TYPE_MODEL_DOCS,
            SOURCE_TYPE_AI_FRAMEWORK_DOCS,
        ):
            self.assertIn(source_type, ALL_SOURCE_TYPES)

    def test_ai_engineer_is_in_canonical_role_list(self) -> None:
        self.assertIn(ROLE_AI_ENGINEER, ALL_ROLES)
        # 권장 순서: tech-lead 다음, product-designer 앞
        self.assertEqual(ALL_ROLES.index(ROLE_AI_ENGINEER), 1)

    def test_list_role_profiles_returns_canonical_order(self) -> None:
        profiles = list_role_profiles()
        self.assertEqual(tuple(p.role for p in profiles), ALL_ROLES)

    def test_all_preferred_source_types_are_known(self) -> None:
        for profile in list_role_profiles():
            for source_type in profile.preferred_source_types:
                self.assertIn(source_type, ALL_SOURCE_TYPES, f"{profile.role}: {source_type}")
            for source_type in profile.weight_hints:
                self.assertIn(source_type, ALL_SOURCE_TYPES, f"{profile.role}: {source_type}")

    def test_product_designer_default_top_priorities_are_visual(self) -> None:
        profile = get_role_profile(ROLE_PRODUCT_DESIGNER)
        self.assertEqual(profile.preferred_source_types[0], SOURCE_TYPE_IMAGE_REFERENCE)
        self.assertEqual(profile.preferred_source_types[1], SOURCE_TYPE_DESIGN_REFERENCE)

    def test_backend_engineer_default_top_priorities_are_docs_and_code(self) -> None:
        profile = get_role_profile(ROLE_BACKEND_ENGINEER)
        self.assertEqual(profile.preferred_source_types[0], SOURCE_TYPE_OFFICIAL_DOCS)
        self.assertEqual(profile.preferred_source_types[1], SOURCE_TYPE_CODE_CONTEXT)

    def test_qa_engineer_prioritizes_github_issues_and_user_messages(self) -> None:
        profile = get_role_profile(ROLE_QA_ENGINEER)
        self.assertIn(SOURCE_TYPE_GITHUB_ISSUE, profile.preferred_source_types[:3])

    def test_tech_lead_includes_official_docs_and_github(self) -> None:
        profile = get_role_profile(ROLE_TECH_LEAD)
        self.assertIn(SOURCE_TYPE_OFFICIAL_DOCS, profile.preferred_source_types)
        self.assertIn(SOURCE_TYPE_GITHUB_ISSUE, profile.preferred_source_types)


class BuildRoleQueryHintsTestCase(unittest.TestCase):
    def test_topic_is_substituted_into_suggested_queries(self) -> None:
        hints = build_role_query_hints(ROLE_BACKEND_ENGINEER, "backend-feature", topic="결제 API")
        self.assertTrue(any("결제 API" in q for q in hints.suggested_queries))
        self.assertFalse(any("{topic}" in q for q in hints.suggested_queries))

    def test_missing_topic_keeps_template_placeholder(self) -> None:
        hints = build_role_query_hints(ROLE_BACKEND_ENGINEER, "backend-feature")
        self.assertTrue(any("{topic}" in q for q in hints.suggested_queries))

    def test_design_task_boosts_image_and_design_reference_for_designer(self) -> None:
        hints = build_role_query_hints(ROLE_PRODUCT_DESIGNER, "landing-page", topic="hero")
        top = _top_n_source_types(hints, 2)
        self.assertEqual(top[0], SOURCE_TYPE_IMAGE_REFERENCE)
        self.assertEqual(top[1], SOURCE_TYPE_DESIGN_REFERENCE)
        self.assertTrue(any("design-heavy" in note for note in hints.notes))

    def test_visual_polish_also_counts_as_design_heavy(self) -> None:
        hints = build_role_query_hints(ROLE_PRODUCT_DESIGNER, "visual-polish")
        top = _top_n_source_types(hints, 2)
        self.assertEqual(top[0], SOURCE_TYPE_IMAGE_REFERENCE)
        self.assertEqual(top[1], SOURCE_TYPE_DESIGN_REFERENCE)

    def test_backend_task_boosts_official_docs_and_code_context_for_backend(self) -> None:
        hints = build_role_query_hints(ROLE_BACKEND_ENGINEER, "backend-feature")
        top = _top_n_source_types(hints, 2)
        self.assertEqual(top[0], SOURCE_TYPE_OFFICIAL_DOCS)
        self.assertEqual(top[1], SOURCE_TYPE_CODE_CONTEXT)
        self.assertTrue(any("backend-heavy" in note for note in hints.notes))

    def test_platform_infra_also_counts_as_backend_heavy(self) -> None:
        hints = build_role_query_hints(ROLE_BACKEND_ENGINEER, "platform-infra")
        self.assertTrue(any("backend-heavy" in note for note in hints.notes))

    def test_frontend_task_boosts_code_context_and_official_docs(self) -> None:
        hints = build_role_query_hints(ROLE_FRONTEND_ENGINEER, "frontend-feature")
        top = _top_n_source_types(hints, 2)
        self.assertIn(SOURCE_TYPE_CODE_CONTEXT, top)
        self.assertIn(SOURCE_TYPE_OFFICIAL_DOCS, top)
        self.assertTrue(any("frontend-heavy" in note for note in hints.notes))

    def test_qa_task_boosts_github_issue_for_qa(self) -> None:
        hints = build_role_query_hints(ROLE_QA_ENGINEER, "qa-test")
        top = _top_n_source_types(hints, 2)
        self.assertIn(SOURCE_TYPE_GITHUB_ISSUE, top)
        self.assertTrue(any("qa-heavy" in note for note in hints.notes))

    def test_unknown_task_type_does_not_add_notes(self) -> None:
        hints = build_role_query_hints(ROLE_BACKEND_ENGINEER, "totally-unknown")
        self.assertEqual(hints.notes, ())

    def test_blank_task_type_normalizes_to_unknown(self) -> None:
        hints = build_role_query_hints(ROLE_TECH_LEAD, None)
        self.assertEqual(hints.task_type, "unknown")
        self.assertEqual(hints.notes, ())

    def test_designer_does_not_get_backend_boost(self) -> None:
        hints = build_role_query_hints(ROLE_PRODUCT_DESIGNER, "backend-feature")
        # 디자이너는 백엔드 task에서 design-heavy 보정을 받지 않으므로 image_reference가 그대로 1위
        top = _top_n_source_types(hints, 2)
        self.assertEqual(top[0], SOURCE_TYPE_IMAGE_REFERENCE)
        self.assertEqual(hints.notes, ())

    def test_weighted_pairs_are_sorted_descending_by_weight(self) -> None:
        hints = build_role_query_hints(ROLE_TECH_LEAD)
        weights = [w for _, w in hints.weighted_source_types]
        self.assertEqual(weights, sorted(weights, reverse=True))

    def test_zero_weight_source_types_are_excluded(self) -> None:
        hints = build_role_query_hints(ROLE_BACKEND_ENGINEER, "backend-feature")
        for _source, weight in hints.weighted_source_types:
            self.assertGreater(weight, 0)


class FormatResearchHintsBlockTestCase(unittest.TestCase):
    def test_blank_role_sequence_returns_empty(self) -> None:
        self.assertEqual(format_research_hints_block((), "landing-page"), "")

    def test_unknown_roles_only_returns_empty(self) -> None:
        self.assertEqual(
            format_research_hints_block(("totally-fake-role",), "landing-page"),
            "",
        )

    def test_known_roles_render_block_with_label_and_sources(self) -> None:
        block = format_research_hints_block(
            (ROLE_PRODUCT_DESIGNER, ROLE_FRONTEND_ENGINEER),
            task_type="landing-page",
        )
        self.assertIn("**역할별 자료 가이드**", block)
        self.assertIn("`product-designer`", block)
        self.assertIn("`frontend-engineer`", block)
        self.assertIn("우선 자료:", block)
        # design task → designer top source는 image_reference
        self.assertIn("image_reference", block)

    def test_ai_engineer_block_surfaces_ai_specific_sources(self) -> None:
        block = format_research_hints_block(
            (ROLE_AI_ENGINEER,),
            task_type="backend-feature",
        )
        self.assertIn("`ai-engineer`", block)
        self.assertIn("우선 자료:", block)
        # 상위 3개 안에 official_docs와 ai-only source 한 종류 이상이 들어가야 함
        self.assertIn("official_docs", block)
        ai_only_present = any(
            keyword in block
            for keyword in ("research_paper", "model_docs", "ai_framework_docs")
        )
        self.assertTrue(ai_only_present)

    def test_topic_substituted_into_recommended_query(self) -> None:
        block = format_research_hints_block(
            (ROLE_BACKEND_ENGINEER,),
            task_type="backend-feature",
            topic="결제 API",
        )
        self.assertIn("결제 API", block)
        self.assertNotIn("{topic}", block)

    def test_unknown_roles_in_sequence_are_skipped_silently(self) -> None:
        block = format_research_hints_block(
            ("totally-fake-role", ROLE_QA_ENGINEER),
            task_type="qa-test",
        )
        self.assertNotIn("totally-fake-role", block)
        self.assertIn("`qa-engineer`", block)


class ReplaceRoleProfileForTestsTestCase(unittest.TestCase):
    def test_override_does_not_mutate_default(self) -> None:
        baseline = get_role_profile(ROLE_BACKEND_ENGINEER)
        overridden = replace_role_profile_for_tests(
            ROLE_BACKEND_ENGINEER,
            weight_hints={SOURCE_TYPE_OFFICIAL_DOCS: 1},
        )
        self.assertNotEqual(overridden.weight_hints, baseline.weight_hints)
        # baseline은 그대로 살아 있어야 한다.
        again = get_role_profile(ROLE_BACKEND_ENGINEER)
        self.assertEqual(again.weight_hints, baseline.weight_hints)


if __name__ == "__main__":
    unittest.main()
