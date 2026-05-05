"""Production-path regression for sufficiency-driven collector loop.

Locks down that ``auto_collect_or_request_more_input`` actually consults
``score_research_sufficiency`` and issues additional provider queries
when the first pass is under-covered. All tests use a fake collector
so we never touch the network.
"""

from __future__ import annotations

import unittest
from datetime import datetime
from typing import List, Sequence
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.research_collector import (
    CollectionMode,
    CollectorConfig,
    CollectorQuery,
    NoOpCollector,
    ResearchCollector,
    auto_collect_or_request_more_input,
)
from yule_orchestrator.agents.research_pack import (
    ResearchSource,
    SourceType,
)


def _result_source(
    *,
    url: str,
    title: str,
    source_type: SourceType = SourceType.WEB_RESULT,
    why: str = "test",
    role: str = "engineering-agent/tech-lead",
) -> ResearchSource:
    return ResearchSource(
        source_type=source_type,
        source_url=url,
        title=title,
        summary=None,
        collected_by_role=role,
        why_relevant=why,
        confidence="medium",
        collected_at=datetime.utcnow(),
        extra={"provider": "fake-test", "domain": "test", "query": "<test>"},
    )


class _RecordingCollector(ResearchCollector):
    """Collector that records every search call and returns scripted hits.

    ``script`` maps query string → tuple of ResearchSource. Queries not
    in the script return ``()`` so unscripted role rotations don't
    silently inflate coverage.
    """

    name = "recording"

    def __init__(self, script: dict | None = None, default: Sequence | None = None) -> None:
        self.script = dict(script or {})
        self.default = tuple(default or ())
        self.calls: List[CollectorQuery] = []

    def search(self, query: CollectorQuery):
        self.calls.append(query)
        for needle, sources in self.script.items():
            if needle in query.query:
                return tuple(sources)
        return tuple(self.default)


def _cfg(*, max_provider_calls: int = 5, max_results_per_role: int = 5):
    return CollectorConfig(
        enabled=True,
        provider="mock",
        max_results=5,
        api_key=None,
        max_provider_calls=max_provider_calls,
        max_results_per_role=max_results_per_role,
    )


class SufficiencyLoopTests(unittest.TestCase):
    def test_sufficiency_score_attached_to_outcome(self) -> None:
        collector = _RecordingCollector(
            default=(_result_source(url="https://x", title="x"),)
        )
        outcome = auto_collect_or_request_more_input(
            role="engineering-agent/tech-lead",
            prompt="새 작업 정리",
            task_type="landing-page",
            config=_cfg(),
            collector=collector,
        )
        self.assertEqual(outcome.mode, CollectionMode.AUTO_COLLECTED)
        self.assertIsNotNone(outcome.sufficiency)
        self.assertGreaterEqual(outcome.iterations, 1)

    def test_loop_issues_additional_queries_when_initial_pass_is_thin(self) -> None:
        # First-pass query (tech-lead) returns one URL; backend-engineer's
        # boost terms ("official docs", "API") trigger a different scripted
        # branch so the loop visibly grows coverage.
        collector = _RecordingCollector(
            default=(_result_source(url="https://a", title="A"),),
            script={
                "official docs": (
                    _result_source(
                        url="https://docs", title="docs",
                        source_type=SourceType.OFFICIAL_DOCS,
                    ),
                ),
                "regression": (
                    _result_source(
                        url="https://qa-issue", title="qa",
                        source_type=SourceType.GITHUB_ISSUE,
                    ),
                ),
            },
        )
        outcome = auto_collect_or_request_more_input(
            role="engineering-agent/tech-lead",
            prompt="새 작업 정리",
            task_type="landing-page",
            config=_cfg(max_provider_calls=5),
            collector=collector,
        )
        self.assertEqual(outcome.mode, CollectionMode.AUTO_COLLECTED)
        self.assertGreater(len(collector.calls), 1, "expected multi-query loop")
        self.assertGreaterEqual(outcome.iterations, 2)
        urls = {s.source_url for s in outcome.pack.sources if s.source_url}
        self.assertIn("https://a", urls)
        # At least one of the follow-up scripted URLs must show up.
        self.assertTrue(urls & {"https://docs", "https://qa-issue"})

    def test_dedupes_identical_urls_across_iterations(self) -> None:
        # Provider returns the SAME URL on every query — the dedupe
        # guard must keep the pack at one copy and the loop must stop
        # within the configured provider-call budget instead of looping
        # forever.
        same_hit = _result_source(url="https://only", title="only one")
        collector = _RecordingCollector(default=(same_hit,))
        budget_cap = 6
        outcome = auto_collect_or_request_more_input(
            role="engineering-agent/tech-lead",
            prompt="새 작업 정리",
            config=_cfg(max_provider_calls=budget_cap),
            collector=collector,
        )
        urls = [
            s.source_url for s in outcome.pack.sources if s.source_url
        ]
        self.assertEqual(urls.count("https://only"), 1)
        # Loop must stop strictly within the budget — the dedupe guard
        # plus role-rotation visited set guarantee finite termination.
        self.assertLessEqual(len(collector.calls), budget_cap)

    def test_respects_max_provider_calls_budget(self) -> None:
        collector = _RecordingCollector(
            default=(_result_source(url="https://x", title="x"),),
            script={
                "ai-engineer": (
                    _result_source(url="https://b", title="b"),
                ),
                "backend-engineer": (
                    _result_source(url="https://c", title="c"),
                ),
                "product-designer": (
                    _result_source(url="https://d", title="d"),
                ),
            },
        )
        outcome = auto_collect_or_request_more_input(
            role="engineering-agent/tech-lead",
            prompt="x",
            config=_cfg(max_provider_calls=2),
            collector=collector,
        )
        self.assertLessEqual(len(collector.calls), 2)
        self.assertEqual(outcome.iterations, len(collector.calls))

    def test_under_covered_roles_surface_when_partial(self) -> None:
        # One thin URL → no required source_types → still partial.
        collector = _RecordingCollector(
            default=(_result_source(url="https://only", title="only"),)
        )
        outcome = auto_collect_or_request_more_input(
            role="engineering-agent/tech-lead",
            prompt="x",
            config=_cfg(max_provider_calls=2),
            collector=collector,
        )
        self.assertIsNotNone(outcome.sufficiency)
        self.assertFalse(outcome.sufficiency.sufficient)
        self.assertTrue(outcome.sufficiency.notes)

    def test_calls_score_research_sufficiency(self) -> None:
        """Wiring guard: the loop must consult score_research_sufficiency."""

        from yule_orchestrator.agents import research_sufficiency

        collector = _RecordingCollector(
            default=(_result_source(url="https://x", title="x"),)
        )
        with patch.object(
            research_sufficiency,
            "score_research_sufficiency",
            wraps=research_sufficiency.score_research_sufficiency,
        ) as score_spy:
            auto_collect_or_request_more_input(
                role="engineering-agent/tech-lead",
                prompt="x",
                config=_cfg(),
                collector=collector,
            )
        score_spy.assert_called()

    def test_legacy_callers_keep_working_when_disabled(self) -> None:
        # auto_collect=False (NoOpCollector) → no loop, no provider calls,
        # behaves exactly like the legacy NEEDS_USER_INPUT branch.
        cfg = CollectorConfig(
            enabled=False, provider="mock", max_results=5, api_key=None
        )
        outcome = auto_collect_or_request_more_input(
            role="engineering-agent/backend-engineer",
            prompt="users API",
            task_type="backend-feature",
            config=cfg,
            collector=NoOpCollector(),
        )
        self.assertEqual(outcome.mode, CollectionMode.NEEDS_USER_INPUT)
        self.assertEqual(outcome.iterations, 1)


if __name__ == "__main__":
    unittest.main()
