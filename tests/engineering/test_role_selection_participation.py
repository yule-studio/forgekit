"""Phase 3 — participation-level surface on RoleSelection.

The selector now classifies each scored role into a participation
bucket (required / primary / reviewer / optional / excluded) and
records the matched keyword list per role. Pin the contract here so
the supervisor / status / aggregator can rely on it.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.lifecycle.role_selection import (
    SOURCE_TECH_LEAD_RULE,
    SOURCE_USER_EXPLICIT,
    apply_role_selection_to_extra,
    recommend_active_roles,
)
from yule_engineering.agents.role_profiles import (
    PARTICIPATION_EXCLUDED,
    PARTICIPATION_PRIMARY,
    PARTICIPATION_REQUIRED,
    PARTICIPATION_REVIEWER,
)


class ParticipationLevelTests(unittest.TestCase):
    def test_tech_lead_is_required_in_user_explicit_branch(self) -> None:
        selection = recommend_active_roles(
            user_prompt="ai-engineer / backend-engineer 관점에서 봐줘",
        )
        self.assertEqual(selection.selection_source, SOURCE_USER_EXPLICIT)
        self.assertEqual(
            selection.participation_by_role["tech-lead"],
            PARTICIPATION_REQUIRED,
        )
        # User-named non-tech-lead roles are primary — the user
        # explicitly asked for their take.
        self.assertEqual(
            selection.participation_by_role["ai-engineer"],
            PARTICIPATION_PRIMARY,
        )
        self.assertEqual(
            selection.participation_by_role["backend-engineer"],
            PARTICIPATION_PRIMARY,
        )
        self.assertEqual(
            set(selection.primary_roles),
            {"ai-engineer", "backend-engineer"},
        )
        # Roles the user didn't name → excluded so the supervisor
        # knows why they're silent.
        self.assertEqual(
            selection.participation_by_role["frontend-engineer"],
            PARTICIPATION_EXCLUDED,
        )

    def test_kubernetes_research_only_routes_devops_primary_backend_reviewer(
        self,
    ) -> None:
        # Live MVP regression — k8s research-only must put devops on
        # the front line and backend as reviewer (runtime contract
        # owner). product-designer / ai / qa / frontend stay excluded.
        selection = recommend_active_roles(
            user_prompt=(
                "오늘은 k8s 쿠버네티스에 대해서 다루고 싶어. "
                "어떤 지식들이 필요할까? 오늘은 코드 수정 없이 자료 수집이 목표야."
            ),
        )
        self.assertEqual(selection.selection_source, SOURCE_TECH_LEAD_RULE)
        self.assertEqual(
            selection.participation_by_role["tech-lead"],
            PARTICIPATION_REQUIRED,
        )
        # devops should be the primary on a pure infra prompt.
        self.assertEqual(
            selection.participation_by_role["devops-engineer"],
            PARTICIPATION_PRIMARY,
        )
        # backend has the runtime contract → reviewer (or primary if
        # devops happened to tie). Either way it must be participating.
        self.assertIn(
            selection.participation_by_role["backend-engineer"],
            (PARTICIPATION_PRIMARY, PARTICIPATION_REVIEWER),
        )
        # All UX / AI / QA roles excluded — k8s research-only with no
        # test / UI / AI / design signal.
        for silent in (
            "frontend-engineer",
            "product-designer",
            "qa-engineer",
            "ai-engineer",
        ):
            self.assertEqual(
                selection.participation_by_role[silent],
                PARTICIPATION_EXCLUDED,
                f"{silent} must be excluded for pure k8s research-only",
            )

    def test_matched_keywords_populated_for_scored_roles(self) -> None:
        selection = recommend_active_roles(
            user_prompt="Spring Security API 인증 + 회귀 테스트 + 운영 모니터링",
        )
        self.assertEqual(selection.selection_source, SOURCE_TECH_LEAD_RULE)
        kw = dict(selection.matched_keywords_by_role)
        # backend should have at least api / spring / 인증 hits.
        backend_hits = set(kw.get("backend-engineer", ()))
        self.assertTrue(
            {"api", "spring", "인증"} & backend_hits,
            f"backend keyword hits unexpectedly empty: {backend_hits}",
        )
        # qa should have 회귀 / test, devops should have 운영 / 모니터링.
        self.assertTrue(set(kw.get("qa-engineer", ())) & {"회귀", "test"})
        self.assertTrue(set(kw.get("devops-engineer", ())) & {"운영", "모니터링"})

    def test_apply_extra_persists_participation_and_keywords(self) -> None:
        selection = recommend_active_roles(
            user_prompt="k8s 운영 대시보드 UI 검토 회귀 테스트",
        )
        extra = apply_role_selection_to_extra(None, selection)
        self.assertIn("role_participation", extra)
        # Each role lands in the participation map (required / primary
        # / reviewer / optional / excluded).
        self.assertEqual(
            set(extra["role_participation"].keys()),
            set(selection.participation_by_role.keys()),
        )
        # primary / reviewer lists round-trip too.
        self.assertEqual(
            extra["role_selection_primary"], list(selection.primary_roles)
        )
        self.assertEqual(
            extra["role_selection_reviewer"], list(selection.reviewer_roles)
        )
        # When any role had a keyword hit we expose the per-role list
        # so status surfaces show signals, not raw counts.
        if selection.matched_keywords_by_role:
            self.assertIn("role_selection_keywords", extra)


class FallbackPolicyTagTests(unittest.TestCase):
    def test_empty_prompt_carries_empty_prompt_policy_tag(self) -> None:
        from yule_engineering.agents.lifecycle.role_selection import (
            FALLBACK_EMPTY_PROMPT,
            FALLBACK_LEGACY_QUARTET,
        )

        selection = recommend_active_roles(user_prompt="")
        self.assertEqual(selection.fallback_policy, FALLBACK_EMPTY_PROMPT)

        # Non-empty no-hit prompts use the legacy quartet for now —
        # Phase 4 narrows this further per domain.
        unmatched = recommend_active_roles(user_prompt="안녕하세요")
        self.assertEqual(unmatched.fallback_policy, FALLBACK_LEGACY_QUARTET)

    def test_user_explicit_has_no_fallback_policy(self) -> None:
        selection = recommend_active_roles(
            user_prompt="ai-engineer 관점에서 정리"
        )
        self.assertIsNone(selection.fallback_policy)


if __name__ == "__main__":
    unittest.main()
