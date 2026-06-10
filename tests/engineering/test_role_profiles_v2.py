"""Phase 1+2 — engineering-agent role profile registry contract tests.

The new ``agents/role_profiles.py`` + ``role_profiles_data.py`` modules
define one :class:`RoleProfile` per engineering role and feed the
selector / runtime / aggregator a stable contract surface. Pin the
contract here so a typo (or a future profile that forgets a section)
fails loudly instead of silently producing an unsafe selection.

Distinct from ``test_role_profiles.py`` (which checks the on-disk
``manifest.json`` profile fields used by the coding-authorization MVP) —
this file pins the in-process registry exposed via
``all_role_profiles()`` / ``get_role_profile()``.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.role_profiles import (
    PARTICIPATING_LEVELS,
    PARTICIPATION_EXCLUDED,
    PARTICIPATION_LEVELS,
    PARTICIPATION_OPTIONAL,
    PARTICIPATION_PRIMARY,
    PARTICIPATION_REQUIRED,
    PARTICIPATION_REVIEWER,
    RoleContract,
    RoleProfile,
    all_role_profiles,
    get_role_profile,
    role_contract_from_profile,
)


_EXPECTED_ROLES = {
    "tech-lead",
    "ai-engineer",
    "backend-engineer",
    "frontend-engineer",
    "devops-engineer",
    "qa-engineer",
    "product-designer",
}


_REQUIRED_FIELDS = (
    "mission",
    "responsibilities",
    "required_context",
    "must_review",
    "output_sections",
    "forbidden_actions",
    "explicit_patterns",
    "escalation_rules",
    "done_criteria",
)


class ParticipationLevelVocabularyTests(unittest.TestCase):
    def test_all_levels_listed(self) -> None:
        self.assertEqual(
            set(PARTICIPATION_LEVELS),
            {
                PARTICIPATION_REQUIRED,
                PARTICIPATION_PRIMARY,
                PARTICIPATION_REVIEWER,
                PARTICIPATION_OPTIONAL,
                PARTICIPATION_EXCLUDED,
            },
        )

    def test_participating_excludes_excluded(self) -> None:
        self.assertNotIn(PARTICIPATION_EXCLUDED, PARTICIPATING_LEVELS)
        self.assertIn(PARTICIPATION_REQUIRED, PARTICIPATING_LEVELS)
        self.assertIn(PARTICIPATION_PRIMARY, PARTICIPATING_LEVELS)


class RegistryShapeTests(unittest.TestCase):
    def test_registry_covers_seven_engineering_roles(self) -> None:
        self.assertEqual(set(all_role_profiles().keys()), _EXPECTED_ROLES)

    def test_every_profile_has_required_fields(self) -> None:
        for role_id, profile in all_role_profiles().items():
            with self.subTest(role=role_id):
                self.assertIsInstance(profile, RoleProfile)
                for field_name in _REQUIRED_FIELDS:
                    value = getattr(profile, field_name)
                    self.assertTrue(
                        value,
                        f"{role_id}: {field_name} must not be empty",
                    )

    def test_explicit_patterns_include_role_id_or_korean_alias(self) -> None:
        """Selector reads explicit_patterns to detect user-named roles —
        each profile must carry at least one pattern matching its own id
        or the established Korean alias."""

        for role_id, profile in all_role_profiles().items():
            with self.subTest(role=role_id):
                joined = " ".join(profile.explicit_patterns).lower()
                self.assertTrue(
                    role_id.lower() in joined
                    or any(
                        token in joined
                        for token in (
                            "테크",
                            "백엔드",
                            "프론트엔드",
                            "데브옵스",
                            "qa",
                            "디자이너",
                            "ai",
                        )
                    ),
                    f"{role_id}: explicit_patterns missing a detectable token",
                )

    def test_tech_lead_has_no_activation_keywords(self) -> None:
        # tech-lead is always required — selector must not score it via
        # the keyword bank (that path would produce duplicate "tech-lead
        # always included" reasons).
        tl = get_role_profile("tech-lead")
        self.assertIsNotNone(tl)
        self.assertEqual(tl.activation_keywords, ())

    def test_member_roles_have_activation_keywords(self) -> None:
        for role_id, profile in all_role_profiles().items():
            if role_id == "tech-lead":
                continue
            with self.subTest(role=role_id):
                self.assertGreaterEqual(
                    len(profile.activation_keywords),
                    3,
                    f"{role_id}: keep at least 3 activation_keywords so the "
                    "selector has signal — k8s/RAG/UI etc. all live here.",
                )


class GetRoleProfileTests(unittest.TestCase):
    def test_short_role_id_resolves(self) -> None:
        self.assertIsNotNone(get_role_profile("backend-engineer"))

    def test_qualified_role_id_resolves(self) -> None:
        self.assertIsNotNone(get_role_profile("engineering-agent/backend-engineer"))

    def test_unknown_role_returns_none(self) -> None:
        self.assertIsNone(get_role_profile("phantom-role"))
        self.assertIsNone(get_role_profile(""))


class RoleContractProjectionTests(unittest.TestCase):
    def test_contract_carries_contract_fields_only(self) -> None:
        backend = get_role_profile("backend-engineer")
        contract = role_contract_from_profile(backend)
        self.assertIsInstance(contract, RoleContract)
        self.assertEqual(contract.role_id, "backend-engineer")
        self.assertEqual(contract.required_context, backend.required_context)
        self.assertEqual(contract.output_sections, backend.output_sections)
        # activation_keywords / explicit_patterns are selector-only —
        # the contract surface must not leak them into the deliberation
        # input where they could pollute the prompt.
        self.assertFalse(hasattr(contract, "activation_keywords"))


class DomainSignalSanityTests(unittest.TestCase):
    """Spec-required regression: profile keyword banks cover the key
    domain ids so adding e.g. Kubernetes is one profile edit, not a
    selector branch.
    """

    def _kw(self, role_id: str) -> str:
        profile = get_role_profile(role_id)
        return " ".join(profile.activation_keywords).lower()

    def test_devops_covers_kubernetes_and_helm(self) -> None:
        kws = self._kw("devops-engineer")
        for token in ("k8s", "kubernetes", "쿠버네티스", "helm", "ingress"):
            self.assertIn(token, kws, f"devops keywords missing {token}")

    def test_ai_engineer_covers_rag_and_memory(self) -> None:
        kws = self._kw("ai-engineer")
        for token in ("rag", "llm", "memory", "prompt"):
            self.assertIn(token, kws, f"ai-engineer keywords missing {token}")

    def test_qa_covers_regression_and_acceptance(self) -> None:
        kws = self._kw("qa-engineer")
        for token in ("regression", "회귀", "acceptance"):
            self.assertIn(token, kws, f"qa-engineer keywords missing {token}")

    def test_product_designer_covers_ux_and_design(self) -> None:
        kws = self._kw("product-designer")
        for token in ("ux", "디자인", "사용자 흐름"):
            self.assertIn(token, kws, f"product-designer keywords missing {token}")


if __name__ == "__main__":
    unittest.main()
