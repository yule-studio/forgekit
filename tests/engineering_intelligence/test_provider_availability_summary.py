"""Provider availability summary + routing reason + status bundle."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.engineering_intelligence.provider_registry import (
    ProviderAvailability,
    ProviderAvailabilityRow,
    ProviderAvailabilitySummary,
    default_registry,
)
from yule_orchestrator.agents.engineering_intelligence.provider_routing import (
    RefreshPlanStatus,
    RoutedRefreshCandidate,
    refresh_plan_status,
    route_refresh_plan,
)
from yule_orchestrator.agents.engineering_intelligence.providers import (
    ProviderTransport,
)
from yule_orchestrator.agents.engineering_intelligence.scheduler import (
    compute_refresh_plan,
)


def _now() -> datetime:
    return datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# ProviderAvailabilitySummary
# ---------------------------------------------------------------------------


class AvailabilitySummaryTests(unittest.TestCase):
    def test_summary_has_one_row_per_transport(self) -> None:
        registry = default_registry()
        summary = registry.availability_summary(env={})
        transports = {row.transport for row in summary.rows}
        for t in ProviderTransport:
            self.assertIn(t.value, transports)

    def test_default_env_is_no_live_impl_or_manual_only(self) -> None:
        registry = default_registry()
        summary = registry.availability_summary(env={})
        for row in summary.rows:
            self.assertIn(
                row.availability,
                {
                    ProviderAvailability.NO_LIVE_IMPL.value,
                    ProviderAvailability.MANUAL_ONLY.value,
                },
                f"unexpected availability for {row.transport}",
            )

    def test_missing_env_keys_listed_for_github_api(self) -> None:
        registry = default_registry()
        # Plug a live factory for github_api so availability is gated
        # on the env triple rather than NO_LIVE_IMPL.
        registry.register_live(
            ProviderTransport.GITHUB_API_REPO_ACTIVITY,
            live_factory=lambda env: (lambda spec, *, source: ()),
        )
        summary = registry.availability_summary(env={})
        github = next(
            row
            for row in summary.rows
            if row.transport
            == ProviderTransport.GITHUB_API_REPO_ACTIVITY.value
        )
        self.assertEqual(
            github.availability, ProviderAvailability.MISSING_ENV.value
        )
        self.assertIn("YULE_GITHUB_APP_ID", github.missing_env_keys)

    def test_disabled_by_flag_when_keys_present_but_flag_off(self) -> None:
        registry = default_registry()
        registry.register_live(
            ProviderTransport.GITHUB_API_REPO_ACTIVITY,
            live_factory=lambda env: (lambda spec, *, source: ()),
        )
        env = {
            "YULE_GITHUB_APP_ID": "1",
            "YULE_GITHUB_APP_INSTALLATION_ID": "2",
            "YULE_GITHUB_APP_PRIVATE_KEY_PATH": "/tmp/k.pem",
        }
        summary = registry.availability_summary(env=env)
        github = next(
            row
            for row in summary.rows
            if row.transport
            == ProviderTransport.GITHUB_API_REPO_ACTIVITY.value
        )
        self.assertEqual(
            github.availability,
            ProviderAvailability.DISABLED_BY_FLAG.value,
        )
        self.assertEqual(github.missing_env_keys, ())
        self.assertFalse(github.enable_flag_set)

    def test_available_when_env_and_flag_set(self) -> None:
        registry = default_registry()
        registry.register_live(
            ProviderTransport.RSS,
            live_factory=lambda env: (lambda spec, *, source: ()),
        )
        env = {"YULE_KNOWLEDGE_RSS_LIVE_ENABLED": "true"}
        summary = registry.availability_summary(env=env)
        rss = next(
            row
            for row in summary.rows
            if row.transport == ProviderTransport.RSS.value
        )
        self.assertEqual(
            rss.availability, ProviderAvailability.AVAILABLE.value
        )
        self.assertTrue(rss.enable_flag_set)
        self.assertTrue(rss.has_live_impl)

    def test_by_state_groups_rows_into_buckets(self) -> None:
        registry = default_registry()
        summary = registry.availability_summary(env={})
        buckets = summary.by_state()
        # MANUAL_ONLY must contain exactly one row (the manual transport).
        manual_bucket = buckets.get(ProviderAvailability.MANUAL_ONLY.value, ())
        self.assertEqual(
            [row.transport for row in manual_bucket],
            [ProviderTransport.MANUAL.value],
        )
        # All non-manual transports land under NO_LIVE_IMPL by default.
        no_live_bucket = buckets.get(
            ProviderAvailability.NO_LIVE_IMPL.value, ()
        )
        self.assertEqual(
            len(no_live_bucket), len(ProviderTransport) - 1
        )

    def test_states_count_totals_match_row_count(self) -> None:
        registry = default_registry()
        summary = registry.availability_summary(env={})
        counts = summary.states_count()
        self.assertEqual(sum(counts.values()), len(summary.rows))

    def test_needs_attention_excludes_no_live_impl_and_manual(self) -> None:
        registry = default_registry()
        # Plug live impl for github_api, leave env empty → MISSING_ENV
        registry.register_live(
            ProviderTransport.GITHUB_API_REPO_ACTIVITY,
            live_factory=lambda env: (lambda spec, *, source: ()),
        )
        summary = registry.availability_summary(env={})
        attention = summary.needs_attention()
        # Only github_api appears (MISSING_ENV); RSS/ATOM/etc still
        # NO_LIVE_IMPL and excluded.
        self.assertEqual(
            [row.transport for row in attention],
            [ProviderTransport.GITHUB_API_REPO_ACTIVITY.value],
        )

    def test_payload_round_trip_has_states_count_and_needs_attention(
        self,
    ) -> None:
        registry = default_registry()
        payload = registry.availability_summary(env={}).to_payload()
        self.assertIn("rows", payload)
        self.assertIn("states_count", payload)
        self.assertIn("needs_attention", payload)
        self.assertEqual(
            len(payload["rows"]), len(ProviderTransport)
        )

    def test_row_from_registration_carries_description_and_notes(self) -> None:
        registry = default_registry()
        rss = registry.get(ProviderTransport.RSS)
        row = ProviderAvailabilityRow.from_registration(rss, env={})
        self.assertEqual(row.provider_id, "rss-feed")
        self.assertTrue(row.description)
        self.assertIn("RSS", row.description.upper())


# ---------------------------------------------------------------------------
# Routing reasoning
# ---------------------------------------------------------------------------


class RoutingReasonTests(unittest.TestCase):
    def test_every_routed_candidate_has_transport_and_availability_reasons(
        self,
    ) -> None:
        plan = compute_refresh_plan(
            "backend-engineer", now=_now(), states={}, tick_quota=99
        )
        due, skipped = route_refresh_plan(
            plan, role_id="backend-engineer", env={}
        )
        for cand in due + skipped:
            self.assertTrue(
                cand.transport_reason,
                f"{cand.source.source_id} missing transport_reason",
            )
            self.assertTrue(
                cand.availability_reason,
                f"{cand.source.source_id} missing availability_reason",
            )

    def test_atom_url_heuristic_explained_in_transport_reason(self) -> None:
        plan = compute_refresh_plan(
            "backend-engineer", now=_now(), states={}, tick_quota=99
        )
        due, _ = route_refresh_plan(
            plan, role_id="backend-engineer", env={}
        )
        # Spring blog is registered as RSS-mode but uses an .atom URL —
        # the transport reason must call that out.
        spring = next(
            (c for c in due if c.source.source_id == "spring-blog"), None
        )
        self.assertIsNotNone(spring)
        self.assertIn("atom", spring.transport_reason)

    def test_no_live_impl_explained_in_availability_reason(self) -> None:
        plan = compute_refresh_plan(
            "backend-engineer", now=_now(), states={}, tick_quota=99
        )
        due, _ = route_refresh_plan(
            plan, role_id="backend-engineer", env={}
        )
        rss_or_atom = next(
            (
                c
                for c in due
                if c.transport
                in (ProviderTransport.RSS, ProviderTransport.ATOM)
            ),
            None,
        )
        self.assertIsNotNone(rss_or_atom)
        self.assertIn("no live impl", rss_or_atom.availability_reason)
        self.assertIn("transport=", rss_or_atom.routing_reason)
        self.assertIn("availability=", rss_or_atom.routing_reason)

    def test_missing_env_reason_lists_missing_keys(self) -> None:
        registry = default_registry()
        registry.register_live(
            ProviderTransport.GITHUB_API_REPO_ACTIVITY,
            live_factory=lambda env: (lambda spec, *, source: ()),
        )
        # tech-lead has the adr-github source (collection_mode=github_api,
        # auto_collect=False, review_required=True) — include both gates
        # so the planner surfaces it.
        plan = compute_refresh_plan(
            "tech-lead",
            now=_now(),
            states={},
            tick_quota=99,
            include_review_required=True,
            include_auto_collect_disabled=True,
        )
        due, _ = route_refresh_plan(
            plan, role_id="tech-lead", registry=registry, env={}
        )
        github = next(
            (
                c
                for c in due
                if c.transport
                is ProviderTransport.GITHUB_API_REPO_ACTIVITY
            ),
            None,
        )
        self.assertIsNotNone(
            github, "expected at least one github_api source on tech-lead"
        )
        self.assertIn(
            "missing env keys", github.availability_reason
        )
        self.assertIn(
            "YULE_GITHUB_APP_ID", github.availability_reason
        )

    def test_available_reason_mentions_flag_truthy(self) -> None:
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
        atom = next(
            (c for c in due if c.transport is ProviderTransport.ATOM),
            None,
        )
        self.assertIsNotNone(atom)
        self.assertIn("live", atom.availability_reason)
        self.assertIn(
            "YULE_KNOWLEDGE_ATOM_LIVE_ENABLED", atom.availability_reason
        )

    def test_routing_reason_payload_round_trips(self) -> None:
        plan = compute_refresh_plan(
            "backend-engineer", now=_now(), states={}, tick_quota=99
        )
        due, _ = route_refresh_plan(
            plan, role_id="backend-engineer", env={}
        )
        sample = due[0].to_payload()
        self.assertIn("transport_reason", sample)
        self.assertIn("availability_reason", sample)
        self.assertIn("routing_reason", sample)


# ---------------------------------------------------------------------------
# RefreshPlanStatus bundle
# ---------------------------------------------------------------------------


class RefreshPlanStatusTests(unittest.TestCase):
    def test_status_bundles_routed_candidates_with_summary(self) -> None:
        plan = compute_refresh_plan(
            "backend-engineer", now=_now(), states={}, tick_quota=99
        )
        status = refresh_plan_status(
            plan, role_id="backend-engineer", env={}
        )
        self.assertIsInstance(status, RefreshPlanStatus)
        self.assertGreater(len(status.due), 0)
        self.assertEqual(status.role, "backend-engineer")
        self.assertIsInstance(
            status.availability, ProviderAvailabilitySummary
        )
        # transports_due is sorted unique.
        self.assertEqual(
            list(status.transports_due),
            sorted(set(status.transports_due)),
        )

    def test_status_payload_has_full_round_trip(self) -> None:
        plan = compute_refresh_plan(
            "backend-engineer", now=_now(), states={}, tick_quota=99
        )
        status = refresh_plan_status(
            plan, role_id="backend-engineer", env={}
        )
        payload = status.to_payload()
        self.assertIn("transports_due", payload)
        self.assertIn("availability", payload)
        self.assertIn("rows", payload["availability"])
        self.assertIn("states_count", payload["availability"])

    def test_status_uses_passed_registry_when_provided(self) -> None:
        registry = default_registry()
        registry.register_live(
            ProviderTransport.RSS,
            live_factory=lambda env: (lambda spec, *, source: ()),
        )
        env = {"YULE_KNOWLEDGE_RSS_LIVE_ENABLED": "true"}
        plan = compute_refresh_plan(
            "backend-engineer", now=_now(), states={}, tick_quota=99
        )
        status = refresh_plan_status(
            plan,
            role_id="backend-engineer",
            registry=registry,
            env=env,
        )
        rss_rows = [
            row
            for row in status.availability.rows
            if row.transport == ProviderTransport.RSS.value
        ]
        self.assertEqual(len(rss_rows), 1)
        self.assertEqual(
            rss_rows[0].availability,
            ProviderAvailability.AVAILABLE.value,
        )


if __name__ == "__main__":
    unittest.main()
