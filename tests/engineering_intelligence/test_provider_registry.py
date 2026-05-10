"""Provider registry: auth contract, availability, fake fixture."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.engineering_intelligence.models import (
    EngineeringKnowledgeItem,
    Importance,
    SourceKind,
)
from yule_orchestrator.agents.engineering_intelligence.providers import (
    FakeKnowledgeProvider,
    ProviderTransport,
    StubLiveSourceFetcher,
    provider_spec_for,
)
from yule_orchestrator.agents.engineering_intelligence.provider_registry import (
    KnowledgeProviderRegistration,
    KnowledgeProviderRegistry,
    ProviderAuthRequirement,
    ProviderAvailability,
    default_registry,
)
from yule_orchestrator.agents.engineering_intelligence.source_registry import (
    find_source,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _knowledge_item(source_id: str) -> EngineeringKnowledgeItem:
    return EngineeringKnowledgeItem(
        item_id=f"{source_id}-1",
        topic_key=f"{source_id}-topic",
        title=f"item from {source_id}",
        role="backend-engineer",
        stack_tags=("test",),
        source_name=source_id,
        source_url=f"https://example.com/{source_id}",
        source_kind=SourceKind.RELEASE_NOTES,
        collected_at="2026-05-09T12:00:00Z",
        importance=Importance.MEDIUM,
    )


# ---------------------------------------------------------------------------
# ProviderAuthRequirement
# ---------------------------------------------------------------------------


class AuthRequirementTests(unittest.TestCase):
    def test_no_keys_no_flag_is_always_satisfied(self) -> None:
        req = ProviderAuthRequirement()
        self.assertTrue(req.env_keys_present({}))
        self.assertTrue(req.enable_flag_set({}))

    def test_blank_value_counts_as_missing(self) -> None:
        req = ProviderAuthRequirement(env_keys=("FOO",))
        self.assertFalse(req.env_keys_present({}))
        self.assertFalse(req.env_keys_present({"FOO": ""}))
        self.assertFalse(req.env_keys_present({"FOO": "   "}))
        self.assertTrue(req.env_keys_present({"FOO": "x"}))

    def test_enable_flag_recognises_truthy_strings(self) -> None:
        req = ProviderAuthRequirement(enable_flag="GO")
        self.assertFalse(req.enable_flag_set({}))
        self.assertFalse(req.enable_flag_set({"GO": "false"}))
        self.assertFalse(req.enable_flag_set({"GO": "0"}))
        self.assertFalse(req.enable_flag_set({"GO": "no"}))
        for truthy in ("1", "true", "True", "TRUE", "yes", "on"):
            with self.subTest(value=truthy):
                self.assertTrue(req.enable_flag_set({"GO": truthy}))


# ---------------------------------------------------------------------------
# Registration availability resolution
# ---------------------------------------------------------------------------


class AvailabilityResolutionTests(unittest.TestCase):
    def test_manual_collapses_to_manual_only(self) -> None:
        reg = KnowledgeProviderRegistration(
            provider_id="manual",
            transport=ProviderTransport.MANUAL,
            auth=ProviderAuthRequirement(),
            fake_fetcher=StubLiveSourceFetcher(),
            manual=True,
        )
        # Even with a perfectly-set env, manual stays manual.
        self.assertEqual(
            reg.evaluate_availability({"YULE_GO": "true"}),
            ProviderAvailability.MANUAL_ONLY,
        )

    def test_no_live_impl_short_circuits_before_env(self) -> None:
        # Auth is fully satisfied; missing live factory still wins.
        reg = KnowledgeProviderRegistration(
            provider_id="rss-feed",
            transport=ProviderTransport.RSS,
            auth=ProviderAuthRequirement(
                env_keys=("FOO",), enable_flag="GO"
            ),
            fake_fetcher=StubLiveSourceFetcher(),
            live_factory=None,
        )
        env = {"FOO": "x", "GO": "true"}
        self.assertEqual(
            reg.evaluate_availability(env),
            ProviderAvailability.NO_LIVE_IMPL,
        )

    def test_missing_env_before_disabled_flag(self) -> None:
        # When both env and flag are off, the env reason is more
        # actionable for the operator (you must fill the secret first
        # before flipping the flag is even meaningful).
        live_calls: list = []

        def factory(env):
            return lambda spec, *, source: live_calls.append(spec)

        reg = KnowledgeProviderRegistration(
            provider_id="github-api",
            transport=ProviderTransport.GITHUB_API_REPO_ACTIVITY,
            auth=ProviderAuthRequirement(
                env_keys=("APP_ID",), enable_flag="GO"
            ),
            fake_fetcher=StubLiveSourceFetcher(),
            live_factory=factory,
        )
        self.assertEqual(
            reg.evaluate_availability({}),
            ProviderAvailability.MISSING_ENV,
        )
        self.assertEqual(
            reg.evaluate_availability({"APP_ID": "x"}),
            ProviderAvailability.DISABLED_BY_FLAG,
        )
        self.assertEqual(
            reg.evaluate_availability(
                {"APP_ID": "x", "GO": "true"}
            ),
            ProviderAvailability.AVAILABLE,
        )

    def test_select_fetcher_respects_availability(self) -> None:
        live_seen: list = []

        def factory(env):
            def fetch(spec, *, source):
                live_seen.append((source.source_id, env.get("FLAG")))
                return ()

            return fetch

        fake = StubLiveSourceFetcher()
        reg = KnowledgeProviderRegistration(
            provider_id="rss",
            transport=ProviderTransport.RSS,
            auth=ProviderAuthRequirement(enable_flag="FLAG"),
            fake_fetcher=fake,
            live_factory=factory,
        )

        # Disabled → fake.
        f1 = reg.select_fetcher({})
        self.assertIs(f1, fake)
        # Enabled → live.
        f2 = reg.select_fetcher({"FLAG": "true"})
        self.assertIsNot(f2, fake)


# ---------------------------------------------------------------------------
# default_registry seed
# ---------------------------------------------------------------------------


class DefaultRegistrySeedTests(unittest.TestCase):
    def test_every_transport_has_a_registration(self) -> None:
        reg = default_registry()
        registered = {r.transport for r in reg.iter_registrations()}
        for transport in ProviderTransport:
            self.assertIn(
                transport, registered, f"missing registration: {transport}"
            )

    def test_default_registry_is_no_live_impl_everywhere(self) -> None:
        reg = default_registry()
        report = dict(reg.availability_report({}))
        # Manual is manual_only by design; everything else is no_live_impl
        # until the live PR plugs a factory in.
        self.assertEqual(report[ProviderTransport.MANUAL.value], "manual_only")
        for transport in ProviderTransport:
            if transport is ProviderTransport.MANUAL:
                continue
            with self.subTest(transport=transport):
                self.assertEqual(
                    report[transport.value], "no_live_impl"
                )

    def test_github_api_requires_app_env_triple(self) -> None:
        reg = default_registry()
        github = reg.get(ProviderTransport.GITHUB_API_REPO_ACTIVITY)
        self.assertIn("YULE_GITHUB_APP_ID", github.auth.env_keys)
        self.assertIn(
            "YULE_GITHUB_APP_INSTALLATION_ID", github.auth.env_keys
        )
        self.assertIn(
            "YULE_GITHUB_APP_PRIVATE_KEY_PATH", github.auth.env_keys
        )

    def test_public_feeds_have_only_enable_flag_no_env_keys(self) -> None:
        reg = default_registry()
        for transport in (
            ProviderTransport.RSS,
            ProviderTransport.ATOM,
            ProviderTransport.GITHUB_RELEASES_ATOM,
            ProviderTransport.SITEMAP,
            ProviderTransport.HTML_LIST,
            ProviderTransport.HTML_DETAIL,
        ):
            with self.subTest(transport=transport):
                row = reg.get(transport)
                self.assertEqual(row.auth.env_keys, ())
                self.assertTrue(row.auth.enable_flag)
                self.assertTrue(
                    row.auth.enable_flag.startswith("YULE_KNOWLEDGE_")
                )

    def test_manual_registration_has_no_enable_flag(self) -> None:
        reg = default_registry()
        manual = reg.get(ProviderTransport.MANUAL)
        self.assertTrue(manual.manual)
        self.assertIsNone(manual.auth.enable_flag)
        self.assertFalse(manual.has_live_impl())

    def test_register_live_attaches_factory(self) -> None:
        reg = default_registry()

        seen: list = []

        def factory(env):
            return lambda spec, *, source: (seen.append(spec.transport) or ())

        reg.register_live(
            ProviderTransport.RSS,
            live_factory=factory,
        )
        # With env / flag set, RSS becomes available.
        env = {"YULE_KNOWLEDGE_RSS_LIVE_ENABLED": "true"}
        self.assertEqual(
            reg.evaluate(ProviderTransport.RSS, env=env),
            ProviderAvailability.AVAILABLE,
        )
        # Without the flag, falls back to fake.
        self.assertEqual(
            reg.evaluate(ProviderTransport.RSS, env={}),
            ProviderAvailability.DISABLED_BY_FLAG,
        )

    def test_register_live_refuses_manual_transport(self) -> None:
        reg = default_registry()
        with self.assertRaises(ValueError):
            reg.register_live(
                ProviderTransport.MANUAL,
                live_factory=lambda env: (lambda spec, *, source: ()),
            )

    def test_register_live_unknown_transport_raises_keyerror(self) -> None:
        reg = KnowledgeProviderRegistry()  # empty registry
        with self.assertRaises(KeyError):
            reg.register_live(
                ProviderTransport.RSS,
                live_factory=lambda env: (lambda spec, *, source: ()),
            )


# ---------------------------------------------------------------------------
# FakeKnowledgeProvider
# ---------------------------------------------------------------------------


class FakeKnowledgeProviderTests(unittest.TestCase):
    def test_returns_fixture_items_keyed_on_source_id(self) -> None:
        items = (_knowledge_item("foo"), _knowledge_item("foo"))
        fake = FakeKnowledgeProvider({"foo": items})
        spring = find_source("backend-engineer", "spring-blog")
        assert spring is not None
        # Source id "spring-blog" has no fixture → empty.
        self.assertEqual(fake(provider_spec_for(spring), source=spring), ())
        # Source whose source_id matches the fixture key → returns items.
        # We synthesise one by editing source_id via a copy.
        from dataclasses import replace as _replace
        fake_source = _replace(spring, source_id="foo")
        produced = fake(provider_spec_for(fake_source), source=fake_source)
        self.assertEqual(produced, items)

    def test_records_calls_with_transport(self) -> None:
        fake = FakeKnowledgeProvider()
        spring = find_source("backend-engineer", "spring-blog")
        assert spring is not None
        fake(provider_spec_for(spring), source=spring)
        self.assertEqual(
            fake.calls,
            [("spring-blog", ProviderTransport.ATOM)],
        )

    def test_with_fixture_can_be_chained_after_construction(self) -> None:
        fake = FakeKnowledgeProvider().with_fixture(
            "x", (_knowledge_item("x"),)
        )
        self.assertEqual(len(fake._payload["x"]), 1)


# ---------------------------------------------------------------------------
# select_fetcher_for + route_refresh_plan
# ---------------------------------------------------------------------------


class SelectFetcherForSourceTests(unittest.TestCase):
    def test_returns_spec_fetcher_and_availability_in_one_call(self) -> None:
        reg = default_registry()
        spring = find_source("backend-engineer", "spring-blog")
        assert spring is not None
        spec, fetcher, availability = reg.select_fetcher_for(spring, env={})
        self.assertEqual(spec.transport, ProviderTransport.ATOM)
        self.assertEqual(availability, ProviderAvailability.NO_LIVE_IMPL)
        # fetcher fall-back is the registry's fake.
        self.assertIs(
            fetcher, reg.get(ProviderTransport.ATOM).fake_fetcher
        )

    def test_routes_owasp_to_manual_only(self) -> None:
        reg = default_registry()
        owasp = find_source("backend-engineer", "owasp-top-10")
        assert owasp is not None
        _, _, availability = reg.select_fetcher_for(owasp, env={})
        self.assertEqual(availability, ProviderAvailability.MANUAL_ONLY)


if __name__ == "__main__":
    unittest.main()
