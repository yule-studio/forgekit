"""Phase 4 — narrow fallback policies.

When the user's prompt has no profile-keyword hit at all, the
selector now picks a domain-focused 2-role pair instead of always
inflating to the historical quartet. Pin each branch:

  * ``empty_prompt`` → tech-lead only.
  * ``vague_infra``  → tech-lead + devops + backend.
  * ``vague_ai_research`` → tech-lead + ai + backend.
  * ``vague_product`` → tech-lead + product-designer + frontend.
  * ``vague_engineering`` → tech-lead + backend + qa.
  * ``legacy_quartet`` (last resort) → tech-lead + ai + backend + qa.

The hint vocabularies must NOT overlap with profile activation_keywords
(otherwise the rule-branch would fire first); each test uses words
that are intentionally outside the rule banks.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.lifecycle.role_selection import (
    FALLBACK_EMPTY_PROMPT,
    FALLBACK_LEGACY_QUARTET,
    FALLBACK_VAGUE_AI_RESEARCH,
    FALLBACK_VAGUE_ENGINEERING,
    FALLBACK_VAGUE_INFRA,
    FALLBACK_VAGUE_PRODUCT,
    ROLE_TECH_LEAD,
    SOURCE_FALLBACK,
    recommend_active_roles,
)


class EmptyPromptFallbackTests(unittest.TestCase):
    def test_empty_prompt_selects_tech_lead_only(self) -> None:
        sel = recommend_active_roles(user_prompt="")
        self.assertEqual(sel.selection_source, SOURCE_FALLBACK)
        self.assertEqual(sel.fallback_policy, FALLBACK_EMPTY_PROMPT)
        self.assertEqual(set(sel.selected_roles), {ROLE_TECH_LEAD})


class VagueInfraFallbackTests(unittest.TestCase):
    def test_server_only_prompt_routes_to_devops_backend(self) -> None:
        # "서버 좀 봐줘" — no profile keyword fires (devops needs
        # 운영/배포/k8s/docker/etc), so fallback narrowing kicks in.
        sel = recommend_active_roles(user_prompt="서버 좀 봐줘")
        self.assertEqual(sel.selection_source, SOURCE_FALLBACK)
        self.assertEqual(sel.fallback_policy, FALLBACK_VAGUE_INFRA)
        self.assertEqual(
            set(sel.selected_roles),
            {ROLE_TECH_LEAD, "devops-engineer", "backend-engineer"},
        )

    def test_production_prompt_routes_to_devops_backend(self) -> None:
        sel = recommend_active_roles(user_prompt="프로덕션 환경 점검해 봐줘")
        self.assertEqual(sel.fallback_policy, FALLBACK_VAGUE_INFRA)


class VagueAiResearchFallbackTests(unittest.TestCase):
    def test_dataset_prompt_routes_to_ai_backend(self) -> None:
        sel = recommend_active_roles(user_prompt="데이터셋 정리 좀 검토해 봐줘")
        self.assertEqual(sel.selection_source, SOURCE_FALLBACK)
        self.assertEqual(sel.fallback_policy, FALLBACK_VAGUE_AI_RESEARCH)
        self.assertEqual(
            set(sel.selected_roles),
            {ROLE_TECH_LEAD, "ai-engineer", "backend-engineer"},
        )


class VagueProductFallbackTests(unittest.TestCase):
    def test_user_experience_prompt_routes_to_designer_frontend(self) -> None:
        sel = recommend_active_roles(user_prompt="사용자 경험 점검 좀 해줘")
        self.assertEqual(sel.selection_source, SOURCE_FALLBACK)
        self.assertEqual(sel.fallback_policy, FALLBACK_VAGUE_PRODUCT)
        self.assertEqual(
            set(sel.selected_roles),
            {ROLE_TECH_LEAD, "product-designer", "frontend-engineer"},
        )


class VagueEngineeringFallbackTests(unittest.TestCase):
    def test_dev_request_routes_to_backend_qa(self) -> None:
        sel = recommend_active_roles(user_prompt="개발 관련해서 봐줘")
        self.assertEqual(sel.selection_source, SOURCE_FALLBACK)
        self.assertEqual(sel.fallback_policy, FALLBACK_VAGUE_ENGINEERING)
        self.assertEqual(
            set(sel.selected_roles),
            {ROLE_TECH_LEAD, "backend-engineer", "qa-engineer"},
        )

    def test_bug_request_routes_to_backend_qa(self) -> None:
        sel = recommend_active_roles(user_prompt="버그 좀 봐줘")
        self.assertEqual(sel.fallback_policy, FALLBACK_VAGUE_ENGINEERING)


class LegacyQuartetSafetyNetTests(unittest.TestCase):
    def test_pure_greeting_returns_legacy_quartet(self) -> None:
        sel = recommend_active_roles(user_prompt="안녕하세요")
        self.assertEqual(sel.fallback_policy, FALLBACK_LEGACY_QUARTET)
        self.assertEqual(
            set(sel.selected_roles),
            {
                ROLE_TECH_LEAD,
                "ai-engineer",
                "backend-engineer",
                "qa-engineer",
            },
        )

    def test_keyword_hit_does_not_fall_back(self) -> None:
        # "k8s" hits the devops profile, so this never reaches the
        # fallback branch — fallback_policy stays None.
        sel = recommend_active_roles(user_prompt="k8s 운영 검토")
        self.assertNotEqual(sel.selection_source, SOURCE_FALLBACK)
        self.assertIsNone(sel.fallback_policy)


if __name__ == "__main__":
    unittest.main()
