"""Source registry — per-role coverage, common-core merge, tier ordering."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.engineering_intelligence.models import (
    SourceKind,
    SourceTier,
)
from yule_orchestrator.agents.engineering_intelligence.source_registry import (
    COMMON_CORE_SOURCES,
    SUPPORTED_ROLES,
    auto_collectable_sources,
    daily_limit_for_role,
    find_source,
    prioritise_sources,
    role_sources,
)


class CoverageTests(unittest.TestCase):
    def test_seven_supported_roles(self) -> None:
        self.assertEqual(
            set(SUPPORTED_ROLES),
            {
                "tech-lead",
                "backend-engineer",
                "frontend-engineer",
                "devops-engineer",
                "qa-engineer",
                "ai-engineer",
                "product-designer",
            },
        )

    def test_each_role_has_at_least_five_sources(self) -> None:
        for role in SUPPORTED_ROLES:
            sources = role_sources(role)
            self.assertGreaterEqual(
                len(sources),
                5,
                msg=f"role={role} must seed at least 5 sources, got {len(sources)}",
            )

    def test_unknown_role_raises(self) -> None:
        with self.assertRaises(KeyError):
            role_sources("data-engineer")

    def test_each_role_has_at_least_one_official_source(self) -> None:
        official_kinds = {SourceKind.STANDARD, SourceKind.DOCS, SourceKind.RELEASE_NOTES}
        for role in SUPPORTED_ROLES:
            sources = role_sources(role)
            kinds = {s.source_kind for s in sources}
            self.assertTrue(
                kinds & official_kinds,
                msg=f"role={role} should have at least one official source kind",
            )


class CommonCoreMergeTests(unittest.TestCase):
    def test_common_core_present_for_every_role_when_role_in_role_tags(self) -> None:
        # NIST CVE feed lists every role — must be merged into all 7.
        for role in SUPPORTED_ROLES:
            sources = role_sources(role)
            ids = {s.source_id for s in sources}
            self.assertIn(
                "cve-nvd",
                ids,
                msg=f"common core cve-nvd must merge into role={role}",
            )

    def test_common_core_skipped_when_role_not_in_role_tags(self) -> None:
        # github-engineering common core does NOT list product-designer
        # in role_tags — verify the merge respects that.
        ids = {s.source_id for s in role_sources("product-designer")}
        self.assertNotIn("github-engineering", ids)

    def test_no_duplicate_source_ids_after_merge(self) -> None:
        for role in SUPPORTED_ROLES:
            ids = [s.source_id for s in role_sources(role)]
            self.assertEqual(
                len(ids),
                len(set(ids)),
                msg=f"role={role} merged sources have duplicate source_ids",
            )


class AutoCollectableTests(unittest.TestCase):
    def test_review_required_filtered_out(self) -> None:
        for role in SUPPORTED_ROLES:
            for entry in auto_collectable_sources(role):
                self.assertFalse(
                    entry.review_required,
                    msg=f"auto_collectable_sources must skip review_required for {role}",
                )
                self.assertTrue(entry.auto_collect)

    def test_owasp_top10_is_review_required(self) -> None:
        # Pin: OWASP Top 10 (manual / standard) must NOT auto-collect.
        owasp = find_source("backend-engineer", "owasp-top-10")
        self.assertIsNotNone(owasp)
        self.assertTrue(owasp.review_required)
        self.assertFalse(owasp.auto_collect)


class PrioritisationTests(unittest.TestCase):
    def test_tier_1_official_wins_over_tier_4_community(self) -> None:
        sources = role_sources("backend-engineer")
        ordered = prioritise_sources(sources)
        self.assertGreater(
            len(ordered), 0, msg="prioritise_sources should preserve members"
        )
        # First entry must be Tier 1 or Tier 2 — never Tier 4.
        self.assertIn(
            ordered[0].tier,
            (SourceTier.TIER_1, SourceTier.TIER_2),
        )

    def test_prioritisation_is_deterministic(self) -> None:
        sources = role_sources("frontend-engineer")
        a = prioritise_sources(sources)
        b = prioritise_sources(sources)
        self.assertEqual(
            [e.source_id for e in a],
            [e.source_id for e in b],
        )


class DailyLimitTests(unittest.TestCase):
    def test_daily_limit_is_five_for_every_role(self) -> None:
        for role in SUPPORTED_ROLES:
            self.assertEqual(daily_limit_for_role(role), 5)

    def test_daily_limit_unknown_role_raises(self) -> None:
        with self.assertRaises(KeyError):
            daily_limit_for_role("not-a-role")


if __name__ == "__main__":
    unittest.main()
