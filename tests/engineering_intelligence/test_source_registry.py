"""Source registry — per-role coverage, common-core merge, tier ordering."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.engineering_intelligence.models import (
    SourceAxis,
    SourceKind,
    SourceTier,
    default_refresh_interval_for_kind,
)
from yule_engineering.agents.engineering_intelligence.source_registry import (
    COMMON_CORE_SOURCES,
    SUPPORTED_ROLES,
    auto_collectable_sources,
    axes_for_role,
    axis_hints_for_task_type,
    daily_limit_for_role,
    find_source,
    prioritise_sources,
    required_axes_for_role,
    role_axis_coverage_report,
    role_sources,
    sources_for_axis,
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


class AxisCoverageTests(unittest.TestCase):
    """Each role must cover the axes named by master plan §9.1."""

    def test_every_role_meets_required_axes(self) -> None:
        for role in SUPPORTED_ROLES:
            covered = set(axes_for_role(role))
            for axis in required_axes_for_role(role):
                self.assertIn(
                    axis,
                    covered,
                    msg=f"role={role} missing required axis={axis.value}",
                )

    def test_axis_coverage_report_counts_match(self) -> None:
        # Report must list every axis a role's entries mention, with at
        # least 1 source per axis (we only seed used axes).
        for role in SUPPORTED_ROLES:
            report = role_axis_coverage_report(role)
            for axis, count in report.items():
                self.assertGreater(count, 0, msg=f"role={role} axis={axis} count")
            self.assertEqual(set(report.keys()), set(axes_for_role(role)))

    def test_required_axes_unknown_role_raises(self) -> None:
        with self.assertRaises(KeyError):
            required_axes_for_role("data-engineer")

    def test_security_axis_present_via_common_core(self) -> None:
        # NIST CVE seed (security_advisory) tags SECURITY axis and
        # merges into every role — every role must therefore cover
        # SECURITY through that route.
        for role in SUPPORTED_ROLES:
            self.assertIn(
                SourceAxis.SECURITY,
                axes_for_role(role),
                msg=f"role={role} missing SECURITY axis",
            )

    def test_design_system_axis_for_designer_only_among_design_seeds(self) -> None:
        # Sanity: ai-engineer / qa-engineer should NOT have DESIGN_SYSTEM
        # in their axis surface (no seeded design source).
        self.assertNotIn(SourceAxis.DESIGN_SYSTEM, axes_for_role("ai-engineer"))
        self.assertNotIn(SourceAxis.DESIGN_SYSTEM, axes_for_role("qa-engineer"))
        self.assertIn(SourceAxis.DESIGN_SYSTEM, axes_for_role("product-designer"))

    def test_sources_for_axis_returns_only_matching(self) -> None:
        for source in sources_for_axis(
            "backend-engineer", SourceAxis.API_SCHEMA_AUTH
        ):
            self.assertIn(SourceAxis.API_SCHEMA_AUTH, source.axes)
        # Empty list when role registry has no source for the axis.
        self.assertEqual(
            sources_for_axis("ai-engineer", SourceAxis.DESIGN_SYSTEM),
            (),
        )


class RefreshIntervalTests(unittest.TestCase):
    def test_default_intervals_per_source_kind(self) -> None:
        # Spot-check: security advisories are most aggressive; standards
        # the slowest. These are operational defaults — if you change
        # them, update the policy doc too.
        self.assertEqual(
            default_refresh_interval_for_kind(SourceKind.SECURITY_ADVISORY),
            30,
        )
        self.assertEqual(
            default_refresh_interval_for_kind(SourceKind.RELEASE_NOTES),
            60,
        )
        self.assertEqual(
            default_refresh_interval_for_kind(SourceKind.DOCS),
            1440,
        )
        self.assertEqual(
            default_refresh_interval_for_kind(SourceKind.STANDARD),
            10080,
        )

    def test_effective_interval_uses_entry_override_when_set(self) -> None:
        # Pick any seeded source — it currently has interval=0 so we
        # exercise the kind fallback.
        s = find_source("backend-engineer", "spring-blog")
        assert s is not None
        # default — interval not overridden, falls back to kind default
        self.assertEqual(
            s.effective_refresh_interval_minutes(),
            default_refresh_interval_for_kind(s.source_kind),
        )

    def test_to_payload_includes_axes_and_interval(self) -> None:
        s = find_source("backend-engineer", "spring-blog")
        assert s is not None
        payload = s.to_payload()
        self.assertIn("axes", payload)
        self.assertIn("refresh_interval_minutes", payload)
        # Override-less entry surfaces the resolved (effective) interval.
        self.assertEqual(
            payload["refresh_interval_minutes"],
            s.effective_refresh_interval_minutes(),
        )


class TaskTypeAxisHintsTests(unittest.TestCase):
    def test_backend_feature_hints_api_schema_axis(self) -> None:
        hints = axis_hints_for_task_type("backend-feature")
        self.assertIn(SourceAxis.API_SCHEMA_AUTH, hints)
        self.assertIn(SourceAxis.OFFICIAL_DOCS, hints)

    def test_qa_test_hints_regression_axis(self) -> None:
        hints = axis_hints_for_task_type("qa-test")
        self.assertIn(SourceAxis.REGRESSION_TEST_PLAN, hints)

    def test_unknown_task_type_returns_empty_tuple(self) -> None:
        self.assertEqual(axis_hints_for_task_type("mystery"), ())
        self.assertEqual(axis_hints_for_task_type(None), ())

    def test_landing_page_hints_design_axis(self) -> None:
        hints = axis_hints_for_task_type("landing-page")
        self.assertIn(SourceAxis.DESIGN_SYSTEM, hints)


if __name__ == "__main__":
    unittest.main()
