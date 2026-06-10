"""Phase 1 — role_selection lifecycle gate.

Tech-lead picks the *minimum* set of roles that participate in
research and deliberation. Default-everyone fan-out produced shallow
generic answers and burned forum budget on roles that had no signal
to give; this module makes the participation list deterministic and
explainable.

Pin the four selection sources we ship today:

  * user_explicit     — user named roles in the prompt
  * tech_lead_rule    — keyword bank scored against role research
                        focus
  * fallback (vague)  — no keyword hit
  * fallback (empty)  — empty prompt
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.lifecycle.role_selection import (
    ALL_ENGINEERING_ROLES,
    ROLE_TECH_LEAD,
    SOURCE_FALLBACK,
    SOURCE_TECH_LEAD_RULE,
    SOURCE_USER_EXPLICIT,
    active_roles_from_extra,
    apply_role_selection_to_extra,
    recommend_active_roles,
)


class UserExplicitSelectionTests(unittest.TestCase):
    def test_named_role_subset_is_selected_exactly(self) -> None:
        prompt = (
            "이번 task는 tech-lead / ai-engineer / backend-engineer / "
            "qa-engineer / devops-engineer 관점에서 검토해줘"
        )
        selection = recommend_active_roles(user_prompt=prompt)
        self.assertEqual(selection.selection_source, SOURCE_USER_EXPLICIT)
        self.assertEqual(
            set(selection.selected_roles),
            {
                ROLE_TECH_LEAD,
                "ai-engineer",
                "backend-engineer",
                "qa-engineer",
                "devops-engineer",
            },
        )
        # frontend-engineer / product-designer were NOT named and must
        # NOT be in the selected set — they sit on the excluded list
        # so the supervisor can explain why they're silent.
        self.assertIn("frontend-engineer", selection.excluded_roles)
        self.assertIn("product-designer", selection.excluded_roles)
        # Reasons map covers every selected role.
        for role in selection.selected_roles:
            self.assertIn(role, selection.reason_by_role)

    def test_tech_lead_added_when_user_doesnt_name_it(self) -> None:
        # User mentions only ai-engineer and backend; tech-lead must
        # still be on the team (always-on requirement).
        selection = recommend_active_roles(
            user_prompt="ai-engineer / backend-engineer 관점에서 정리해줘"
        )
        self.assertEqual(selection.selection_source, SOURCE_USER_EXPLICIT)
        self.assertIn(ROLE_TECH_LEAD, selection.selected_roles)
        self.assertEqual(selection.selected_roles[0], ROLE_TECH_LEAD)

    def test_korean_alias_resolves_to_canonical_role_id(self) -> None:
        selection = recommend_active_roles(
            user_prompt="백엔드 / qa 엔지니어 관점에서 정리"
        )
        self.assertEqual(selection.selection_source, SOURCE_USER_EXPLICIT)
        self.assertEqual(
            set(selection.selected_roles),
            {ROLE_TECH_LEAD, "backend-engineer", "qa-engineer"},
        )


class TechLeadRuleSelectionTests(unittest.TestCase):
    def test_spring_security_routes_to_backend_qa_devops(self) -> None:
        selection = recommend_active_roles(
            user_prompt=(
                "Spring Security API 인증 흐름 추가하고 회귀 테스트 + "
                "운영 모니터링까지 정리해줘"
            )
        )
        self.assertEqual(selection.selection_source, SOURCE_TECH_LEAD_RULE)
        self.assertIn(ROLE_TECH_LEAD, selection.selected_roles)
        self.assertIn("backend-engineer", selection.selected_roles)
        self.assertIn("qa-engineer", selection.selected_roles)
        self.assertIn("devops-engineer", selection.selected_roles)
        # No UI/copy concern → frontend / product-designer excluded.
        self.assertIn("frontend-engineer", selection.excluded_roles)
        self.assertIn("product-designer", selection.excluded_roles)

    def test_react_ui_landing_routes_to_frontend_designer_qa(self) -> None:
        selection = recommend_active_roles(
            user_prompt=(
                "React 랜딩 페이지의 hero 컴포넌트 디자인 + 카피 "
                "정리하고 접근성 회귀 테스트도 같이 봐줘"
            )
        )
        self.assertEqual(selection.selection_source, SOURCE_TECH_LEAD_RULE)
        self.assertIn(ROLE_TECH_LEAD, selection.selected_roles)
        self.assertIn("frontend-engineer", selection.selected_roles)
        self.assertIn("product-designer", selection.selected_roles)
        self.assertIn("qa-engineer", selection.selected_roles)
        # No backend/auth concern → backend-engineer excluded.
        self.assertIn("backend-engineer", selection.excluded_roles)

    def test_ai_harness_prompt_picks_up_ai_role(self) -> None:
        # The live MVP "하네스 엔지니어링" Research case should pull in
        # ai-engineer via the research keyword bank without needing
        # explicit role names.
        selection = recommend_active_roles(
            user_prompt=(
                "하네스 엔지니어링을 yule-studio-agent에 도입할 수 있을지 "
                "조사하고 운영 모니터링 / qa 회귀 시나리오까지 검토"
            )
        )
        self.assertEqual(selection.selection_source, SOURCE_TECH_LEAD_RULE)
        self.assertIn("ai-engineer", selection.selected_roles)
        self.assertIn("devops-engineer", selection.selected_roles)
        self.assertIn("qa-engineer", selection.selected_roles)

    def test_hint_role_sequence_bumps_keyword_quiet_role(self) -> None:
        # Continuation case: research already started with backend +
        # qa. A vague follow-up prompt should keep the team intact via
        # the hint sequence, even when keywords don't fire.
        selection = recommend_active_roles(
            user_prompt="이전 결과 기준으로 자료 더 모아줘",
            hint_role_sequence=("backend-engineer", "qa-engineer"),
        )
        # hint bump elevates these from no-keyword roles into the
        # active set under the rule branch.
        self.assertEqual(selection.selection_source, SOURCE_TECH_LEAD_RULE)
        self.assertIn("backend-engineer", selection.selected_roles)
        self.assertIn("qa-engineer", selection.selected_roles)


class FallbackSelectionTests(unittest.TestCase):
    def test_empty_prompt_returns_tech_lead_only(self) -> None:
        # Phase 4: empty prompt narrows to tech-lead only. The
        # historical "always-on quartet" behaved like a default
        # everywhere even when the user hadn't said anything yet — that
        # spawned generic research sessions with no signal. Now the
        # gateway gets a clear "tech-lead asks for clarification" team.
        selection = recommend_active_roles(user_prompt="")
        self.assertEqual(selection.selection_source, SOURCE_FALLBACK)
        self.assertEqual(
            set(selection.selected_roles),
            {ROLE_TECH_LEAD},
        )
        for role in selection.selected_roles:
            self.assertIn("fallback", selection.reason_by_role[role])

    def test_unrelated_prompt_returns_legacy_quartet(self) -> None:
        # Non-empty prompt with no keyword hit → keep the legacy
        # quartet (until Phase 4 narrows by domain hint). tech-lead is
        # always required.
        selection = recommend_active_roles(user_prompt="안녕하세요")
        self.assertEqual(selection.selection_source, SOURCE_FALLBACK)
        self.assertIn(ROLE_TECH_LEAD, selection.selected_roles)


class SessionExtraIntegrationTests(unittest.TestCase):
    def test_apply_writes_active_and_excluded_lists(self) -> None:
        selection = recommend_active_roles(
            user_prompt="ai-engineer / qa-engineer 관점에서 정리"
        )
        extra = apply_role_selection_to_extra({"existing": "keep"}, selection)
        self.assertEqual(extra["existing"], "keep")
        self.assertEqual(
            extra["active_research_roles"],
            list(selection.selected_roles),
        )
        self.assertEqual(
            extra["excluded_research_roles"],
            list(selection.excluded_roles),
        )
        self.assertEqual(extra["role_selection_source"], SOURCE_USER_EXPLICIT)
        self.assertIn(ROLE_TECH_LEAD, extra["role_selection_reasons"])

    def test_apply_does_not_mutate_input_extra(self) -> None:
        original: dict = {"foo": "bar"}
        selection = recommend_active_roles(user_prompt="ai-engineer 관점")
        result = apply_role_selection_to_extra(original, selection)
        # Caller's dict stays clean; result is a fresh dict.
        self.assertNotIn("active_research_roles", original)
        self.assertIn("active_research_roles", result)

    def test_active_roles_from_extra_reads_back_list(self) -> None:
        selection = recommend_active_roles(
            user_prompt="backend-engineer / qa 엔지니어 관점"
        )
        extra = apply_role_selection_to_extra(None, selection)
        self.assertEqual(
            active_roles_from_extra(extra),
            selection.selected_roles,
        )

    def test_active_roles_from_extra_returns_empty_when_unset(self) -> None:
        # Older session rows from before the Phase 1 fix have no
        # active_research_roles key — read-back must return an empty
        # tuple so callers can fall back to legacy behaviour.
        self.assertEqual(active_roles_from_extra(None), ())
        self.assertEqual(active_roles_from_extra({}), ())
        self.assertEqual(
            active_roles_from_extra({"unrelated": "field"}),
            (),
        )


class CatalogueIntegrityTests(unittest.TestCase):
    def test_all_engineering_roles_includes_tech_lead_first(self) -> None:
        self.assertEqual(ALL_ENGINEERING_ROLES[0], ROLE_TECH_LEAD)

    def test_all_engineering_roles_covers_six_member_roles(self) -> None:
        self.assertEqual(
            set(ALL_ENGINEERING_ROLES),
            {
                ROLE_TECH_LEAD,
                "backend-engineer",
                "frontend-engineer",
                "ai-engineer",
                "devops-engineer",
                "qa-engineer",
                "product-designer",
            },
        )


if __name__ == "__main__":
    unittest.main()
