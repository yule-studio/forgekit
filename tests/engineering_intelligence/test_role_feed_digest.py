"""Operator-facing role feed digest — axis groups, required coverage, headline."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.engineering_intelligence.models import (
    SourceAxis,
    SourceTier,
)
from yule_orchestrator.agents.engineering_intelligence.source_registry import (
    SUPPORTED_ROLES,
    RoleAxisGroup,
    RoleFeedDigest,
    RoleFeedEntry,
    multi_role_feed_digest,
    required_axes_for_role,
    role_feed_digest,
)


class RoleFeedDigestTests(unittest.TestCase):
    def test_unknown_role_raises(self) -> None:
        with self.assertRaises(KeyError):
            role_feed_digest("not-a-role")

    def test_required_axes_listed_first(self) -> None:
        digest = role_feed_digest("backend-engineer")
        required = required_axes_for_role("backend-engineer")
        head = tuple(g.axis for g in digest.axes[: len(required)])
        self.assertEqual(head, required)
        for group in digest.axes[: len(required)]:
            self.assertTrue(group.is_required)
        for group in digest.axes[len(required) :]:
            self.assertFalse(group.is_required)

    def test_every_required_axis_has_at_least_one_feed(self) -> None:
        # Mirrors the contract floor: a role's required axes should be
        # seeded. If a role drops a required axis without adding a feed
        # the digest tells the operator immediately.
        for role in SUPPORTED_ROLES:
            digest = role_feed_digest(role)
            self.assertEqual(
                digest.missing_required_axes,
                (),
                f"role {role} has missing required axes: "
                f"{digest.missing_required_axes}",
            )

    def test_feed_count_matches_role_sources(self) -> None:
        from yule_orchestrator.agents.engineering_intelligence.source_registry import (
            role_sources,
        )

        for role in SUPPORTED_ROLES:
            digest = role_feed_digest(role)
            self.assertEqual(
                digest.total_feeds,
                len(role_sources(role)),
                f"feed count mismatch for {role}",
            )

    def test_auto_collect_feed_count_matches_filter(self) -> None:
        from yule_orchestrator.agents.engineering_intelligence.source_registry import (
            auto_collectable_sources,
        )

        digest = role_feed_digest("backend-engineer")
        self.assertEqual(
            digest.auto_collect_feed_count,
            len(auto_collectable_sources("backend-engineer")),
        )

    def test_axis_group_orders_tier_1_first(self) -> None:
        digest = role_feed_digest("backend-engineer")
        for group in digest.axes:
            tiers = [feed.tier for feed in group.feeds]
            for prev, curr in zip(tiers, tiers[1:]):
                # Tier_1 < tier_2 < tier_3 < tier_4 — prioritise_sources
                # ensures monotonic non-decreasing tier order inside a group.
                self.assertLessEqual(_tier_rank(prev), _tier_rank(curr))

    def test_review_required_count_matches_feed_attribute(self) -> None:
        # Tech-lead has at least one MANUAL/review-required source
        # (ISO/IEC 25010). Make sure the count reflects it.
        digest = role_feed_digest("tech-lead")
        review_in_groups = sum(
            group.review_required_count for group in digest.axes
        )
        self.assertGreaterEqual(review_in_groups, 1)

    def test_unclassified_feeds_bucket_empty_for_seeded_roles(self) -> None:
        # Seeded sources all carry at least one axis tag — this guards
        # against a future edit accidentally dropping axes from a row.
        for role in SUPPORTED_ROLES:
            digest = role_feed_digest(role)
            self.assertEqual(
                digest.unclassified_feeds,
                (),
                f"unclassified feeds for {role}: "
                f"{[f.source_id for f in digest.unclassified_feeds]}",
            )

    def test_headline_mentions_role_total_and_axis_count(self) -> None:
        digest = role_feed_digest("backend-engineer")
        head = digest.headline()
        self.assertIn("backend-engineer", head)
        self.assertIn(f"{digest.total_feeds} feeds", head)
        self.assertIn(f"{len(digest.axes)} axes", head)

    def test_payload_round_trips_axis_and_feed_metadata(self) -> None:
        digest = role_feed_digest("frontend-engineer")
        payload = digest.to_payload()
        self.assertEqual(payload["role"], "frontend-engineer")
        self.assertIn("headline", payload)
        self.assertIn("axes", payload)
        self.assertEqual(
            len(payload["axes"]), len(digest.axes)
        )
        first_group = payload["axes"][0]
        self.assertIn("axis", first_group)
        self.assertIn("feeds", first_group)
        self.assertIn("auto_collect_count", first_group)
        if first_group["feeds"]:
            feed_payload = first_group["feeds"][0]
            for key in (
                "source_id",
                "name",
                "base_url",
                "source_kind",
                "collection_mode",
                "tier",
                "auto_collect",
                "review_required",
                "refresh_interval_minutes",
                "axes",
                "role_tags",
            ):
                self.assertIn(key, feed_payload)

    def test_feed_entry_refresh_interval_falls_back_to_kind_default(self) -> None:
        # Most seeded entries leave refresh_interval_minutes=0; the
        # digest should always surface the effective default per kind.
        digest = role_feed_digest("devops-engineer")
        for group in digest.axes:
            for feed in group.feeds:
                self.assertGreater(feed.refresh_interval_minutes, 0)


class MultiRoleDigestTests(unittest.TestCase):
    def test_default_covers_supported_roles(self) -> None:
        digests = multi_role_feed_digest()
        self.assertEqual(
            tuple(d.role for d in digests),
            SUPPORTED_ROLES,
        )

    def test_custom_subset(self) -> None:
        digests = multi_role_feed_digest(("qa-engineer", "ai-engineer"))
        self.assertEqual(
            tuple(d.role for d in digests),
            ("qa-engineer", "ai-engineer"),
        )


def _tier_rank(tier: SourceTier) -> int:
    order = {
        SourceTier.TIER_1: 0,
        SourceTier.TIER_2: 1,
        SourceTier.TIER_3: 2,
        SourceTier.TIER_4: 3,
    }
    return order.get(tier, 99)


if __name__ == "__main__":
    unittest.main()
