"""Refresh-plan → provider routing + axis priority + tick selection."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.engineering_intelligence.models import (
    SourceAxis,
)
from yule_engineering.agents.engineering_intelligence.providers import (
    ProviderTransport,
    provider_spec_for,
)
from yule_engineering.agents.engineering_intelligence.provider_registry import (
    ProviderAvailability,
    default_registry,
)
from yule_engineering.agents.engineering_intelligence.provider_routing import (
    RoutedRefreshCandidate,
    axis_priority_order,
    route_refresh_plan,
    select_routed_due,
)
from yule_engineering.agents.engineering_intelligence.scheduler import (
    compute_refresh_plan,
)
from yule_engineering.agents.engineering_intelligence.source_registry import (
    SUPPORTED_ROLES,
    role_sources,
)


def _now() -> datetime:
    # Pinned anchor — keeps "never_attempted" classification stable.
    return datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# route_refresh_plan
# ---------------------------------------------------------------------------


class RouteRefreshPlanTests(unittest.TestCase):
    def test_due_candidates_carry_transport_axes_and_availability(self) -> None:
        plan = compute_refresh_plan(
            "backend-engineer",
            now=_now(),
            states={},
            tick_quota=99,  # don't truncate — we want every source
        )
        due, skipped = route_refresh_plan(
            plan, role_id="backend-engineer", env={}
        )
        self.assertGreater(len(due), 0)
        for cand in due:
            self.assertIsInstance(cand, RoutedRefreshCandidate)
            self.assertEqual(cand.decision, "due")
            self.assertTrue(cand.axes)
            self.assertEqual(
                cand.transport, provider_spec_for(cand.source).transport
            )
            # No live impl wired → fakes everywhere.
            self.assertIn(
                cand.availability,
                {
                    ProviderAvailability.NO_LIVE_IMPL,
                    ProviderAvailability.MANUAL_ONLY,
                },
            )
        # Skipped entries (auto_collect=False / review_required) annotated too.
        skipped_ids = {c.source.source_id for c in skipped}
        # OWASP Top 10 is auto_collect=False on backend → ends up in skipped.
        self.assertIn("owasp-top-10", skipped_ids)

    def test_skipped_candidate_carries_manual_only_availability(self) -> None:
        plan = compute_refresh_plan(
            "backend-engineer",
            now=_now(),
            states={},
            tick_quota=99,
            include_auto_collect_disabled=True,
            include_review_required=True,
        )
        # When both gates opted-in, OWASP appears in due and routing
        # surfaces its MANUAL transport + MANUAL_ONLY availability so
        # the runner knows there is no live call to make.
        due, _ = route_refresh_plan(
            plan, role_id="backend-engineer", env={}
        )
        owasp = next(
            (c for c in due if c.source.source_id == "owasp-top-10"),
            None,
        )
        self.assertIsNotNone(owasp)
        self.assertEqual(owasp.transport, ProviderTransport.MANUAL)
        self.assertEqual(
            owasp.availability, ProviderAvailability.MANUAL_ONLY
        )

    def test_routing_with_partial_live_factory_marks_available(self) -> None:
        registry = default_registry()
        registry.register_live(
            ProviderTransport.ATOM,
            live_factory=lambda env: (lambda spec, *, source: ()),
        )
        env = {"YULE_KNOWLEDGE_ATOM_LIVE_ENABLED": "true"}

        plan = compute_refresh_plan(
            "backend-engineer", now=_now(), states={}, tick_quota=99
        )
        due, _ = route_refresh_plan(
            plan,
            role_id="backend-engineer",
            registry=registry,
            env=env,
        )
        atom_candidates = [
            c for c in due if c.transport is ProviderTransport.ATOM
        ]
        self.assertTrue(atom_candidates, "expected at least one atom source")
        for c in atom_candidates:
            self.assertEqual(c.availability, ProviderAvailability.AVAILABLE)
        # Non-atom sources still no_live_impl (env flag only enables atom).
        non_atom = [
            c for c in due if c.transport is not ProviderTransport.ATOM
        ]
        for c in non_atom:
            self.assertNotEqual(c.availability, ProviderAvailability.AVAILABLE)

    def test_payload_round_trip_contains_axes_and_auth_keys(self) -> None:
        plan = compute_refresh_plan(
            "backend-engineer", now=_now(), states={}, tick_quota=99
        )
        due, _ = route_refresh_plan(
            plan, role_id="backend-engineer", env={}
        )
        sample = due[0].to_payload()
        self.assertIn("transport", sample)
        self.assertIn("provider_id", sample)
        self.assertIn("availability", sample)
        self.assertIn("axes", sample)
        self.assertIn("auth", sample)
        self.assertIn("env_keys", sample["auth"])
        self.assertIn("enable_flag", sample["auth"])


# ---------------------------------------------------------------------------
# axis_priority_order
# ---------------------------------------------------------------------------


class AxisPriorityOrderTests(unittest.TestCase):
    def test_overdue_axis_candidates_rank_first(self) -> None:
        plan = compute_refresh_plan(
            "backend-engineer", now=_now(), states={}, tick_quota=99
        )
        due, _ = route_refresh_plan(
            plan, role_id="backend-engineer", env={}
        )
        ordered = axis_priority_order(
            due, overdue_axes=(SourceAxis.SECURITY.value,)
        )
        # Every leading candidate (until the first non-security one) must
        # carry SECURITY in its axes.
        leading_security = []
        for c in ordered:
            if SourceAxis.SECURITY in c.axes:
                leading_security.append(c)
            else:
                break
        self.assertTrue(leading_security)
        self.assertIn(SourceAxis.SECURITY, leading_security[0].axes)

    def test_available_provider_beats_fake_within_same_axis_bucket(self) -> None:
        registry = default_registry()
        # Make ATOM live; RSS stays no-live-impl.
        registry.register_live(
            ProviderTransport.ATOM,
            live_factory=lambda env: (lambda spec, *, source: ()),
        )
        env = {"YULE_KNOWLEDGE_ATOM_LIVE_ENABLED": "true"}

        plan = compute_refresh_plan(
            "backend-engineer", now=_now(), states={}, tick_quota=99
        )
        due, _ = route_refresh_plan(
            plan,
            role_id="backend-engineer",
            registry=registry,
            env=env,
        )
        ordered = axis_priority_order(due, overdue_axes=())
        # The leading candidates should include AVAILABLE atom sources.
        leading_avail = {
            ordered[i].availability for i in range(min(2, len(ordered)))
        }
        self.assertIn(ProviderAvailability.AVAILABLE, leading_avail)

    def test_tier_breaks_ties_when_no_overdue_and_same_availability(self) -> None:
        # All due candidates here have NO_LIVE_IMPL availability, so the
        # tier_rank tie-breaker decides ordering. Tier 1 sources should
        # precede Tier 2 / 3.
        plan = compute_refresh_plan(
            "backend-engineer", now=_now(), states={}, tick_quota=99
        )
        due, _ = route_refresh_plan(
            plan, role_id="backend-engineer", env={}
        )
        ordered = axis_priority_order(due, overdue_axes=())
        # Within the same availability bucket the order must be tier-monotonic.
        same_bucket = [
            c for c in ordered
            if c.availability == ProviderAvailability.NO_LIVE_IMPL
        ]
        tiers = [c.source.tier.value for c in same_bucket]
        # tier_1 < tier_2 < tier_3 alphabetically (the enum values are
        # "tier_1_official_docs", "tier_2_official_release", ...) so a
        # non-decreasing sequence verifies Tier 1 wins ties.
        self.assertEqual(sorted(tiers), tiers)

    def test_deterministic_for_fixed_inputs(self) -> None:
        plan = compute_refresh_plan(
            "backend-engineer", now=_now(), states={}, tick_quota=99
        )
        due, _ = route_refresh_plan(
            plan, role_id="backend-engineer", env={}
        )
        a = axis_priority_order(due, overdue_axes=(SourceAxis.SECURITY.value,))
        b = axis_priority_order(due, overdue_axes=(SourceAxis.SECURITY.value,))
        self.assertEqual(
            [c.source.source_id for c in a],
            [c.source.source_id for c in b],
        )


# ---------------------------------------------------------------------------
# select_routed_due — plan + route + axis priority + tick quota in one
# ---------------------------------------------------------------------------


class SelectRoutedDueTests(unittest.TestCase):
    def test_quota_truncates_to_axis_priority_head(self) -> None:
        # Disable planner cap (tick_quota=-1) so axis priority decides
        # the head; then take just 1 candidate. Backend-engineer only
        # carries one SECURITY-tagged auto-collectable source (the
        # cve-nvd common-core feed), so quota=1 verifies the overdue
        # axis bucket fills the slot.
        plan = compute_refresh_plan(
            "backend-engineer",
            now=_now(),
            states={},
            tick_quota=-1,
        )
        selected, deferred = select_routed_due(
            plan,
            role_id="backend-engineer",
            env={},
            overdue_axes=(SourceAxis.SECURITY.value,),
            tick_quota=1,
        )
        self.assertEqual(len(selected), 1)
        self.assertIn(SourceAxis.SECURITY, selected[0].axes)
        # The rest are deferred — none lost.
        self.assertEqual(
            len(selected) + len(deferred),
            len(plan.due),
        )

    def test_no_quota_keeps_full_ordered_list(self) -> None:
        plan = compute_refresh_plan(
            "backend-engineer", now=_now(), states={}, tick_quota=99
        )
        selected, deferred = select_routed_due(
            plan, role_id="backend-engineer", env={}, tick_quota=None
        )
        self.assertEqual(deferred, ())
        self.assertEqual(len(selected), len(plan.due))


# ---------------------------------------------------------------------------
# Cross-role: every role's plan resolves to routed candidates without raising
# ---------------------------------------------------------------------------


class CrossRoleRoutingTests(unittest.TestCase):
    def test_every_role_has_routable_candidates(self) -> None:
        for role in SUPPORTED_ROLES:
            with self.subTest(role=role):
                plan = compute_refresh_plan(
                    role,
                    now=_now(),
                    states={},
                    tick_quota=99,
                    include_review_required=True,
                    include_auto_collect_disabled=True,
                )
                due, skipped = route_refresh_plan(
                    plan, role_id=role, env={}
                )
                # Total candidates equal the role's full source list.
                expected = len(role_sources(role))
                self.assertEqual(len(due) + len(skipped), expected)
                for cand in due + skipped:
                    self.assertTrue(cand.spec.endpoint)
                    self.assertTrue(cand.provider.provider_id)
                    self.assertIn(
                        cand.availability,
                        set(ProviderAvailability),
                    )


if __name__ == "__main__":
    unittest.main()
