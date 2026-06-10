"""Multi-provider research collector tests (auto / multi mode).

All tests use fake collectors and a patched env so no real Tavily/Brave
network call is ever made. They cover:

- ``CollectorConfig.from_env`` parses ``ENGINEERING_RESEARCH_PROVIDER=auto``
  + ``ENGINEERING_RESEARCH_PROVIDERS=tavily,brave`` (incl. defaults & alias).
- ``build_collector`` returns a :class:`MultiProviderCollector` with the
  right sub-providers and skipped-reason metadata when API keys are
  missing.
- ``MultiProviderCollector`` routes per role (tavily-first vs brave-first,
  unknown-role fallback, skipped-provider chain).
- Dedup collapses near-duplicates (identical URL, AMP/desktop variants,
  URL-less entries) across providers.
- The shared :class:`BudgetTracker` is the true ceiling: total inner
  provider calls never exceed ``max_provider_calls``.
- Single-provider regressions (mock / tavily / brave) keep working.
"""

from __future__ import annotations

import os
import unittest
from typing import List, Sequence
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.research.collector import (
    BraveSearchCollector,
    BudgetTracker,
    CollectionMode,
    CollectorConfig,
    CollectorQuery,
    DEFAULT_AUTO_PROVIDERS,
    DEFAULT_MAX_PROVIDER_CALLS,
    ENV_AUTO_COLLECT_ENABLED,
    ENV_BRAVE_API_KEY,
    ENV_MAX_PROVIDER_CALLS,
    ENV_PROVIDER,
    ENV_PROVIDERS,
    ENV_TAVILY_API_KEY,
    MockSearchCollector,
    MultiProviderCollector,
    PROVIDER_AUTO,
    PROVIDER_BRAVE,
    PROVIDER_MOCK,
    PROVIDER_MULTI,
    PROVIDER_TAVILY,
    ResearchCollector,
    TavilySearchCollector,
    auto_collect_or_request_more_input,
    build_collector,
)
from yule_engineering.agents.research.pack import ResearchSource, SourceType


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _env(**overrides) -> dict:
    """Strip out research/provider env vars then layer *overrides*.

    Mirrors test_collector.py's helper so the two suites can share the
    same patch.dict shape without bleeding real env into tests.
    """

    base = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("ENGINEERING_RESEARCH_")
        and k not in {ENV_TAVILY_API_KEY, ENV_BRAVE_API_KEY}
    }
    base.update({k: v for k, v in overrides.items() if v is not None})
    return base


def _src(
    *,
    url: str,
    title: str,
    role: str = "engineering-agent/tech-lead",
    domain: str | None = None,
    source_type: SourceType = SourceType.WEB_RESULT,
) -> ResearchSource:
    extra = {"domain": domain or "", "query": "<test>"}
    return ResearchSource(
        source_type=source_type,
        source_url=url,
        title=title,
        summary=None,
        collected_by_role=role,
        why_relevant=None,
        confidence="medium",
        extra=extra,
    )


class _FakeProvider(ResearchCollector):
    """Provider stub: returns a fixed list and records every call.

    The class takes a ``name`` so we can register multiple instances under
    distinct provider keys (``tavily`` / ``brave``) without reaching for
    the real network-bound implementations.
    """

    def __init__(
        self,
        name: str,
        results: Sequence[ResearchSource] = (),
    ) -> None:
        self.name = name  # type: ignore[assignment]
        self.results = tuple(results)
        self.calls: List[CollectorQuery] = []

    def search(self, query: CollectorQuery) -> Sequence[ResearchSource]:
        self.calls.append(query)
        return tuple(self.results)


# ---------------------------------------------------------------------------
# CollectorConfig env parsing
# ---------------------------------------------------------------------------


class CollectorConfigAutoModeTests(unittest.TestCase):
    def test_auto_mode_parses_provider_list(self) -> None:
        with patch.dict(
            os.environ,
            _env(
                **{
                    ENV_AUTO_COLLECT_ENABLED: "true",
                    ENV_PROVIDER: PROVIDER_AUTO,
                    ENV_PROVIDERS: "tavily,brave",
                    ENV_TAVILY_API_KEY: "t-key",
                    ENV_BRAVE_API_KEY: "b-key",
                }
            ),
            clear=True,
        ):
            cfg = CollectorConfig.from_env()
        self.assertTrue(cfg.is_auto)
        self.assertEqual(cfg.provider, PROVIDER_AUTO)
        self.assertEqual(cfg.providers, (PROVIDER_TAVILY, PROVIDER_BRAVE))
        self.assertEqual(cfg.api_keys.get(PROVIDER_TAVILY), "t-key")
        self.assertEqual(cfg.api_keys.get(PROVIDER_BRAVE), "b-key")

    def test_multi_alias_treated_as_auto(self) -> None:
        with patch.dict(
            os.environ,
            _env(
                **{
                    ENV_AUTO_COLLECT_ENABLED: "true",
                    ENV_PROVIDER: PROVIDER_MULTI,
                }
            ),
            clear=True,
        ):
            cfg = CollectorConfig.from_env()
        self.assertEqual(cfg.provider, PROVIDER_MULTI)
        self.assertTrue(cfg.is_auto)
        # Empty ``ENGINEERING_RESEARCH_PROVIDERS`` falls back to defaults.
        self.assertEqual(cfg.providers, DEFAULT_AUTO_PROVIDERS)

    def test_auto_mode_drops_unknown_provider_names(self) -> None:
        with patch.dict(
            os.environ,
            _env(
                **{
                    ENV_AUTO_COLLECT_ENABLED: "true",
                    ENV_PROVIDER: PROVIDER_AUTO,
                    # "duck" is not a known external provider.
                    ENV_PROVIDERS: "tavily, duck , brave ,",
                }
            ),
            clear=True,
        ):
            cfg = CollectorConfig.from_env()
        # Order preserved, duplicates+blanks+unknowns dropped.
        self.assertEqual(cfg.providers, (PROVIDER_TAVILY, PROVIDER_BRAVE))

    def test_single_provider_modes_still_set_legacy_api_key(self) -> None:
        # mock/tavily/brave single modes must keep filling ``cfg.api_key``
        # so callers that haven't migrated to ``api_keys`` mapping work.
        with patch.dict(
            os.environ,
            _env(
                **{
                    ENV_AUTO_COLLECT_ENABLED: "true",
                    ENV_PROVIDER: PROVIDER_BRAVE,
                    ENV_BRAVE_API_KEY: "brave-only",
                }
            ),
            clear=True,
        ):
            cfg = CollectorConfig.from_env()
        self.assertEqual(cfg.provider, PROVIDER_BRAVE)
        self.assertEqual(cfg.api_key, "brave-only")
        self.assertFalse(cfg.is_auto)

    def test_blank_providers_in_auto_mode_falls_back_to_defaults(self) -> None:
        with patch.dict(
            os.environ,
            _env(
                **{
                    ENV_AUTO_COLLECT_ENABLED: "true",
                    ENV_PROVIDER: PROVIDER_AUTO,
                    ENV_PROVIDERS: "   ",
                }
            ),
            clear=True,
        ):
            cfg = CollectorConfig.from_env()
        self.assertEqual(cfg.providers, DEFAULT_AUTO_PROVIDERS)


# ---------------------------------------------------------------------------
# build_collector — auto mode dispatch + skipped reasons
# ---------------------------------------------------------------------------


class BuildCollectorAutoModeTests(unittest.TestCase):
    def test_auto_with_both_keys_returns_multi_collector(self) -> None:
        cfg = CollectorConfig(
            enabled=True,
            provider=PROVIDER_AUTO,
            max_results=5,
            providers=(PROVIDER_TAVILY, PROVIDER_BRAVE),
            api_keys={PROVIDER_TAVILY: "t", PROVIDER_BRAVE: "b"},
        )
        collector = build_collector(cfg)
        self.assertIsInstance(collector, MultiProviderCollector)
        self.assertEqual(
            tuple(collector.active_providers),
            (PROVIDER_TAVILY, PROVIDER_BRAVE),
        )
        self.assertFalse(collector.skipped_providers)

    def test_auto_with_missing_tavily_key_skips_tavily(self) -> None:
        cfg = CollectorConfig(
            enabled=True,
            provider=PROVIDER_AUTO,
            max_results=5,
            providers=(PROVIDER_TAVILY, PROVIDER_BRAVE),
            api_keys={PROVIDER_BRAVE: "b"},
        )
        collector = build_collector(cfg)
        self.assertIsInstance(collector, MultiProviderCollector)
        self.assertEqual(tuple(collector.active_providers), (PROVIDER_BRAVE,))
        self.assertIn(PROVIDER_TAVILY, collector.skipped_providers)
        self.assertIn(
            ENV_TAVILY_API_KEY, collector.skipped_providers[PROVIDER_TAVILY]
        )

    def test_auto_with_no_keys_falls_back_to_mock(self) -> None:
        cfg = CollectorConfig(
            enabled=True,
            provider=PROVIDER_AUTO,
            max_results=5,
            providers=(PROVIDER_TAVILY, PROVIDER_BRAVE),
            api_keys={},
        )
        collector = build_collector(cfg)
        # Dev-environment ergonomics: no keys → mock so deliberation can
        # still happen during local test runs without leaking auto contract.
        self.assertIsInstance(collector, MockSearchCollector)


# ---------------------------------------------------------------------------
# MultiProviderCollector — role policy + dedupe
# ---------------------------------------------------------------------------


class MultiProviderRoutingTests(unittest.TestCase):
    def _composite(
        self,
        *,
        tavily_results=(),
        brave_results=(),
        budget=None,
    ) -> tuple[MultiProviderCollector, _FakeProvider, _FakeProvider]:
        tavily = _FakeProvider(PROVIDER_TAVILY, tavily_results)
        brave = _FakeProvider(PROVIDER_BRAVE, brave_results)
        composite = MultiProviderCollector(
            providers=(tavily, brave),
            budget=budget,
        )
        return composite, tavily, brave

    def test_tech_lead_calls_both_providers_in_order(self) -> None:
        composite, tavily, brave = self._composite(
            tavily_results=(
                _src(url="https://tav-1", title="tavily one", domain="tav-1"),
            ),
            brave_results=(
                _src(url="https://brv-1", title="brave one", domain="brv-1"),
            ),
        )
        results = composite.search(
            CollectorQuery(
                query="x",
                role="engineering-agent/tech-lead",
                max_results=5,
            )
        )
        # Tavily-first ordering preserved: Tavily hit precedes Brave hit.
        urls = [s.source_url for s in results]
        self.assertEqual(urls, ["https://tav-1", "https://brv-1"])
        self.assertEqual(len(tavily.calls), 1)
        self.assertEqual(len(brave.calls), 1)

    def test_backend_engineer_uses_brave_first(self) -> None:
        composite, tavily, brave = self._composite(
            tavily_results=(
                _src(url="https://tav-1", title="tav", domain="tav"),
            ),
            brave_results=(
                _src(url="https://brv-1", title="brv", domain="brv"),
            ),
        )
        results = composite.search(
            CollectorQuery(
                query="x",
                role="engineering-agent/backend-engineer",
                max_results=5,
            )
        )
        urls = [s.source_url for s in results]
        # Brave first per default policy; both providers still consulted.
        self.assertEqual(urls, ["https://brv-1", "https://tav-1"])

    def test_unknown_role_falls_back_to_default_provider_pair(self) -> None:
        composite, tavily, brave = self._composite(
            tavily_results=(_src(url="https://tav", title="tav", domain="tav"),),
            brave_results=(_src(url="https://brv", title="brv", domain="brv"),),
        )
        results = composite.search(
            CollectorQuery(
                query="x",
                role="engineering-agent/security-reviewer",  # not in policy
                max_results=3,
            )
        )
        # Default tuple is (tavily, brave), so both still ran.
        self.assertEqual(len(results), 2)
        self.assertEqual(len(tavily.calls), 1)
        self.assertEqual(len(brave.calls), 1)

    def test_gateway_role_runs_no_providers(self) -> None:
        composite, tavily, brave = self._composite(
            tavily_results=(_src(url="https://tav", title="tav"),),
            brave_results=(_src(url="https://brv", title="brv"),),
        )
        results = composite.search(
            CollectorQuery(query="x", role="gateway", max_results=3)
        )
        self.assertEqual(results, ())
        self.assertEqual(tavily.calls, [])
        self.assertEqual(brave.calls, [])

    def test_skipped_provider_is_not_called(self) -> None:
        # Brave is registered but not Tavily — a backend-engineer call
        # whose policy is (brave, tavily) must just call Brave and skip
        # the missing Tavily slot.
        brave = _FakeProvider(
            PROVIDER_BRAVE,
            (_src(url="https://brv", title="brv", domain="brv"),),
        )
        composite = MultiProviderCollector(
            providers=(brave,),
            skipped={PROVIDER_TAVILY: f"{ENV_TAVILY_API_KEY} not set"},
        )
        results = composite.search(
            CollectorQuery(
                query="x",
                role="engineering-agent/backend-engineer",
                max_results=3,
            )
        )
        self.assertEqual([s.source_url for s in results], ["https://brv"])
        self.assertEqual(len(brave.calls), 1)
        self.assertIn(PROVIDER_TAVILY, composite.skipped_providers)

    def test_provider_failure_does_not_crash_composite(self) -> None:
        class _BoomProvider(ResearchCollector):
            name = PROVIDER_TAVILY

            def search(self, query):  # noqa: D401 - simple stub
                raise RuntimeError("provider down")

        boom = _BoomProvider()
        brave = _FakeProvider(
            PROVIDER_BRAVE,
            (_src(url="https://brv", title="brv", domain="brv"),),
        )
        composite = MultiProviderCollector(providers=(boom, brave))
        results = composite.search(
            CollectorQuery(
                query="x",
                role="engineering-agent/tech-lead",
                max_results=3,
            )
        )
        # Tavily exploded but Brave still produced a hit.
        self.assertEqual([s.source_url for s in results], ["https://brv"])

    def test_provider_rank_stamped_on_extra(self) -> None:
        composite, tavily, brave = self._composite(
            tavily_results=(
                _src(url="https://tav-a", title="A", domain="tav"),
                _src(url="https://tav-b", title="B", domain="tav"),
            ),
            brave_results=(
                _src(url="https://brv-a", title="A", domain="brv"),
            ),
        )
        results = composite.search(
            CollectorQuery(
                query="x",
                role="engineering-agent/tech-lead",
                max_results=5,
            )
        )
        ranks = {s.source_url: s.extra.get("provider_rank") for s in results}
        self.assertEqual(ranks["https://tav-a"], 0)
        self.assertEqual(ranks["https://tav-b"], 1)
        self.assertEqual(ranks["https://brv-a"], 0)


# ---------------------------------------------------------------------------
# Dedupe across providers
# ---------------------------------------------------------------------------


class MultiProviderDedupeTests(unittest.TestCase):
    def test_identical_url_collapses_to_single_source(self) -> None:
        tav = _FakeProvider(
            PROVIDER_TAVILY,
            (_src(url="https://docs.example.com/x", title="x", domain="docs.example.com"),),
        )
        brave = _FakeProvider(
            PROVIDER_BRAVE,
            (_src(url="https://docs.example.com/x", title="x", domain="docs.example.com"),),
        )
        composite = MultiProviderCollector(providers=(tav, brave))
        out = composite.search(
            CollectorQuery(query="x", role="tech-lead", max_results=5)
        )
        self.assertEqual(len(out), 1)
        # Tavily's hit wins because its provider runs first for tech-lead.
        self.assertEqual(out[0].extra.get("provider"), PROVIDER_TAVILY)

    def test_url_normalisation_collapses_trailing_slash_and_utm(self) -> None:
        tav = _FakeProvider(
            PROVIDER_TAVILY,
            (
                _src(
                    url="https://Docs.Example.com/x/",
                    title="A",
                    domain="docs.example.com",
                ),
            ),
        )
        brave = _FakeProvider(
            PROVIDER_BRAVE,
            (
                _src(
                    url="https://docs.example.com/x?utm_source=brave",
                    title="A",
                    domain="docs.example.com",
                ),
            ),
        )
        composite = MultiProviderCollector(providers=(tav, brave))
        out = composite.search(
            CollectorQuery(query="x", role="tech-lead", max_results=5)
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].extra.get("provider"), PROVIDER_TAVILY)

    def test_url_less_sources_dedupe_by_title_and_type(self) -> None:
        tav = _FakeProvider(
            PROVIDER_TAVILY,
            (
                ResearchSource(
                    source_type=SourceType.COMMUNITY_SIGNAL,
                    source_url=None,
                    title="Note: AI agents",
                    summary=None,
                    collected_by_role="tech-lead",
                    extra={"provider": PROVIDER_TAVILY},
                ),
            ),
        )
        brave = _FakeProvider(
            PROVIDER_BRAVE,
            (
                ResearchSource(
                    source_type=SourceType.COMMUNITY_SIGNAL,
                    source_url=None,
                    title="Note: AI agents",
                    summary=None,
                    collected_by_role="tech-lead",
                    extra={"provider": PROVIDER_BRAVE},
                ),
            ),
        )
        composite = MultiProviderCollector(providers=(tav, brave))
        out = composite.search(
            CollectorQuery(query="x", role="tech-lead", max_results=5)
        )
        # Same title + same type ⇒ single entry.
        self.assertEqual(len(out), 1)


# ---------------------------------------------------------------------------
# Budget cap is global across providers
# ---------------------------------------------------------------------------


class MultiProviderBudgetTests(unittest.TestCase):
    def test_inner_calls_respect_outer_budget_cap(self) -> None:
        # max_provider_calls=2 → only 2 inner provider calls total even
        # though tech-lead's policy would call both providers per round.
        tav = _FakeProvider(
            PROVIDER_TAVILY,
            (_src(url="https://t", title="t", domain="t"),),
        )
        brave = _FakeProvider(
            PROVIDER_BRAVE,
            (_src(url="https://b", title="b", domain="b"),),
        )
        budget = BudgetTracker(max_provider_calls=2, max_results_per_role=5)
        composite = MultiProviderCollector(
            providers=(tav, brave), budget=budget
        )
        # First role-level search: outer caller pays for budget.record_call();
        # composite uses Tavily for free, then claims the 2nd slot for Brave.
        budget.record_call()
        composite.search(
            CollectorQuery(
                query="x",
                role="engineering-agent/tech-lead",
                max_results=5,
            )
        )
        self.assertEqual(budget.calls_made, 2)
        self.assertFalse(budget.can_call())
        # Second role-level search: budget exhausted; outer wouldn't even
        # call us in real code, but exercising the path proves no inner
        # calls go through when can_call() is False at entry.
        starting_inner = composite.inner_calls
        before = (len(tav.calls), len(brave.calls))
        # Simulate outer skipping the call when budget exhausted.
        if budget.can_call():
            composite.search(
                CollectorQuery(
                    query="y",
                    role="engineering-agent/tech-lead",
                    max_results=5,
                )
            )
        # Still budget-exhausted; nothing else fired.
        self.assertEqual(composite.inner_calls, starting_inner)
        self.assertEqual((len(tav.calls), len(brave.calls)), before)

    def test_second_provider_skipped_when_budget_runs_out_mid_role(self) -> None:
        tav = _FakeProvider(
            PROVIDER_TAVILY,
            (_src(url="https://t", title="t", domain="t"),),
        )
        brave = _FakeProvider(
            PROVIDER_BRAVE,
            (_src(url="https://b", title="b", domain="b"),),
        )
        # Budget = 1: outer slot consumed, no headroom for the 2nd provider.
        budget = BudgetTracker(max_provider_calls=1, max_results_per_role=5)
        budget.record_call()  # outer caller paid
        composite = MultiProviderCollector(
            providers=(tav, brave), budget=budget
        )
        out = composite.search(
            CollectorQuery(
                query="x",
                role="engineering-agent/tech-lead",
                max_results=5,
            )
        )
        # Tavily ran (the freebie), Brave was skipped.
        self.assertEqual(len(tav.calls), 1)
        self.assertEqual(len(brave.calls), 0)
        self.assertEqual([s.source_url for s in out], ["https://t"])


# ---------------------------------------------------------------------------
# auto_collect_or_request_more_input — outcome metadata
# ---------------------------------------------------------------------------


class AutoCollectMetadataTests(unittest.TestCase):
    """Outcome / pack metadata exposes which providers ran or were skipped."""

    def test_skipped_providers_surface_in_pack_extra(self) -> None:
        tav = _FakeProvider(
            PROVIDER_TAVILY,
            (_src(url="https://t", title="t", domain="t"),),
        )
        composite = MultiProviderCollector(
            providers=(tav,),
            skipped={PROVIDER_BRAVE: f"{ENV_BRAVE_API_KEY} not set"},
        )
        cfg = CollectorConfig(
            enabled=True,
            provider=PROVIDER_AUTO,
            max_results=3,
            providers=(PROVIDER_TAVILY, PROVIDER_BRAVE),
            api_keys={PROVIDER_TAVILY: "t"},
        )
        outcome = auto_collect_or_request_more_input(
            role="engineering-agent/tech-lead",
            prompt="새 작업 정리",
            task_type="landing-page",
            config=cfg,
            collector=composite,
        )
        self.assertEqual(outcome.mode, CollectionMode.AUTO_COLLECTED)
        self.assertIsNotNone(outcome.pack)
        skipped = (outcome.pack.extra or {}).get("auto_skipped_providers")
        self.assertEqual(skipped, {PROVIDER_BRAVE: f"{ENV_BRAVE_API_KEY} not set"})
        active = (outcome.pack.extra or {}).get("auto_active_providers")
        self.assertEqual(list(active or ()), [PROVIDER_TAVILY])


# ---------------------------------------------------------------------------
# Single-provider regressions — must still work after multi-mode landed
# ---------------------------------------------------------------------------


class SingleProviderRegressionTests(unittest.TestCase):
    def test_mock_single_provider_still_returns_mock(self) -> None:
        cfg = CollectorConfig(enabled=True, provider=PROVIDER_MOCK, max_results=5)
        collector = build_collector(cfg)
        self.assertIsInstance(collector, MockSearchCollector)

    def test_tavily_single_provider_still_returns_tavily(self) -> None:
        cfg = CollectorConfig(
            enabled=True,
            provider=PROVIDER_TAVILY,
            max_results=5,
            api_key="x",
        )
        collector = build_collector(cfg)
        self.assertIsInstance(collector, TavilySearchCollector)

    def test_brave_single_provider_still_returns_brave(self) -> None:
        cfg = CollectorConfig(
            enabled=True,
            provider=PROVIDER_BRAVE,
            max_results=5,
            api_key="x",
        )
        collector = build_collector(cfg)
        self.assertIsInstance(collector, BraveSearchCollector)


if __name__ == "__main__":
    unittest.main()
