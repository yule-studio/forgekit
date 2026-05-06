"""Task-aware research budget policy tests (Part B).

Verifies that ``decide_budget`` classifies prompts into the right tier
and that ``auto_collect_or_request_more_input`` surfaces the budget
metadata + stop reason on its outcome.
"""

from __future__ import annotations

import unittest
from datetime import datetime
from typing import List, Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.research_budget import (
    TIER_DEEP,
    TIER_LARGE,
    TIER_MEDIUM,
    TIER_SMALL,
    decide_budget,
)
from yule_orchestrator.agents.research_collector import (
    CollectionMode,
    CollectorConfig,
    CollectorQuery,
    ResearchCollector,
    auto_collect_or_request_more_input,
)
from yule_orchestrator.agents.research_pack import ResearchSource, SourceType


def _result(url: str, *, source_type=SourceType.WEB_RESULT, role="engineering-agent/tech-lead"):
    return ResearchSource(
        source_type=source_type,
        source_url=url,
        title=url,
        summary=None,
        collected_by_role=role,
        why_relevant="test",
        confidence="medium",
        collected_at=datetime.utcnow(),
        extra={"provider": "fake-test", "domain": "test", "query": "<test>"},
    )


class _ScriptedCollector(ResearchCollector):
    name = "scripted"

    def __init__(self, default: Sequence | None = None, script: dict | None = None) -> None:
        self.default = tuple(default or ())
        self.script = dict(script or {})
        self.calls: List[CollectorQuery] = []

    def search(self, query: CollectorQuery):
        self.calls.append(query)
        for needle, sources in self.script.items():
            if needle in query.query:
                return tuple(sources)
        return tuple(self.default)


class TierClassificationTests(unittest.TestCase):
    def test_quick_fix_keyword_stays_small(self) -> None:
        policy = decide_budget(prompt="hero copy 오타 버그 수정", task_type=None)
        self.assertEqual(policy.tier, TIER_SMALL)

    def test_default_falls_to_medium(self) -> None:
        policy = decide_budget(prompt="hero copy 정리", task_type=None)
        self.assertEqual(policy.tier, TIER_MEDIUM)

    def test_architecture_keyword_escalates_to_large(self) -> None:
        policy = decide_budget(
            prompt="multi-agent 시스템의 architecture 설계 검토", task_type=None
        )
        self.assertEqual(policy.tier, TIER_LARGE)

    def test_rag_keyword_escalates_to_large(self) -> None:
        policy = decide_budget(prompt="RAG 메모리 구조 정리", task_type=None)
        self.assertEqual(policy.tier, TIER_LARGE)

    def test_deep_research_keyword_escalates_to_deep(self) -> None:
        policy = decide_budget(
            prompt="deep dive into agent runtime — 깊게 조사해줘", task_type=None
        )
        self.assertEqual(policy.tier, TIER_DEEP)

    def test_platform_infra_task_type_escalates_to_large(self) -> None:
        # No small/large/deep keywords in the prompt itself, so the
        # task_type alone must drive the tier choice.
        policy = decide_budget(prompt="새 작업 정리", task_type="platform-infra")
        self.assertEqual(policy.tier, TIER_LARGE)

    def test_hard_caps_clamp_recommendations(self) -> None:
        # Even when tier picks 28 calls, an env-driven hard cap of 6
        # must clamp the policy down to 6 — never escalates beyond
        # operator cost gate.
        policy = decide_budget(
            prompt="multi-agent infra 깊게 검토",
            task_type=None,
            hard_cap_provider_calls=6,
            hard_cap_results_per_role=2,
        )
        self.assertLessEqual(policy.max_provider_calls, 6)
        self.assertLessEqual(policy.max_results_per_role, 2)

    def test_role_targets_scale_with_tier(self) -> None:
        small = decide_budget(prompt="버그 수정", task_type=None)
        large = decide_budget(prompt="architecture 설계", task_type=None)
        deep = decide_budget(prompt="deep research multi-agent", task_type=None)
        # Larger tier ≥ smaller tier role minimums for tech-lead.
        self.assertLessEqual(small.role_target("tech-lead"), large.role_target("tech-lead"))
        self.assertLessEqual(large.role_target("tech-lead"), deep.role_target("tech-lead"))

    def test_devops_engineer_has_target(self) -> None:
        policy = decide_budget(prompt="rollout deploy 정리", task_type=None)
        self.assertGreater(policy.role_target("devops-engineer"), 0)


class CollectorOutcomeMetadataTests(unittest.TestCase):
    def _cfg(self, *, max_provider_calls=20, max_results_per_role=8):
        return CollectorConfig(
            enabled=True,
            provider="mock",
            max_results=5,
            api_key=None,
            max_provider_calls=max_provider_calls,
            max_results_per_role=max_results_per_role,
        )

    def test_outcome_carries_budget_tier_and_role_targets(self) -> None:
        collector = _ScriptedCollector(default=(_result(url="https://a"),))
        outcome = auto_collect_or_request_more_input(
            role="engineering-agent/tech-lead",
            prompt="새 작업 정리",
            task_type="landing-page",
            config=self._cfg(),
            collector=collector,
        )
        self.assertEqual(outcome.mode, CollectionMode.AUTO_COLLECTED)
        self.assertIn(outcome.budget_tier, (TIER_SMALL, TIER_MEDIUM, TIER_LARGE, TIER_DEEP))
        self.assertGreater(outcome.max_provider_calls, 0)
        self.assertGreater(outcome.max_results_per_role, 0)
        self.assertTrue(outcome.role_targets)
        # Roles in the policy's role_targets should map to non-zero
        # min_sources values.
        for role, min_sources in outcome.role_targets:
            self.assertIsInstance(role, str)
            self.assertGreaterEqual(min_sources, 1)

    def test_architecture_prompt_yields_large_tier(self) -> None:
        collector = _ScriptedCollector(default=(_result(url="https://a"),))
        outcome = auto_collect_or_request_more_input(
            role="engineering-agent/tech-lead",
            prompt="multi-agent 시스템의 architecture 설계와 RAG 메모리 검토",
            task_type=None,
            config=self._cfg(max_provider_calls=20, max_results_per_role=8),
            collector=collector,
        )
        self.assertEqual(outcome.budget_tier, TIER_LARGE)

    def test_quick_fix_prompt_yields_small_tier(self) -> None:
        collector = _ScriptedCollector(default=(_result(url="https://a"),))
        outcome = auto_collect_or_request_more_input(
            role="engineering-agent/tech-lead",
            prompt="hero h1 오타 버그 수정",
            task_type=None,
            config=self._cfg(),
            collector=collector,
        )
        self.assertEqual(outcome.budget_tier, TIER_SMALL)

    def test_stop_reason_set_when_loop_exits(self) -> None:
        collector = _ScriptedCollector(default=(_result(url="https://a"),))
        outcome = auto_collect_or_request_more_input(
            role="engineering-agent/tech-lead",
            prompt="아키텍처 검토",
            config=self._cfg(),
            collector=collector,
        )
        self.assertIn(
            outcome.stop_reason,
            {"sufficient", "budget_exhausted", "no_progress", "role_rotation_exhausted",
             "no_initial_provider_hit"},
            f"unexpected stop_reason: {outcome.stop_reason!r}",
        )

    def test_under_covered_roles_surface_when_thin(self) -> None:
        # Single URL → most role targets will not be met.
        collector = _ScriptedCollector(default=(_result(url="https://only"),))
        outcome = auto_collect_or_request_more_input(
            role="engineering-agent/tech-lead",
            prompt="아키텍처 검토",
            config=self._cfg(max_provider_calls=2),
            collector=collector,
        )
        self.assertTrue(outcome.under_covered_roles)

    def test_hard_cap_respected_in_outcome(self) -> None:
        # Architecture prompt would normally request large tier (16 calls)
        # but env hard cap of 3 must bring max_provider_calls down to 3.
        collector = _ScriptedCollector(default=(_result(url="https://a"),))
        outcome = auto_collect_or_request_more_input(
            role="engineering-agent/tech-lead",
            prompt="multi-agent architecture 설계",
            config=self._cfg(max_provider_calls=3, max_results_per_role=2),
            collector=collector,
        )
        self.assertLessEqual(outcome.max_provider_calls, 3)
        self.assertLessEqual(len(collector.calls), 3)


if __name__ == "__main__":
    unittest.main()
