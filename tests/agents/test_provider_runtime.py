"""Provider-runtime telemetry + cost proxy (WT1 live-provider hardening).

Pins: failure taxonomy stability, live-vs-fallback classification, usage/cost
population (live + estimate basis), graceful fallback recording, and the
cost-model pricing table.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.harness.cost_model import estimate_cost, estimate_tokens_from_text
from yule_engineering.agents.harness.provider_runtime import (
    FAIL_BLOCKED_GRANT,
    FAIL_CLI_MISSING,
    FAIL_ENDPOINT_UNREACHABLE,
    FAIL_SUBMIT_ERROR,
    build_provider_runtime,
    classify_failure,
)


class CostModelTests(unittest.TestCase):
    def test_table_price_and_model_tier(self) -> None:
        c = estimate_cost("claude", input_tokens=1000, output_tokens=1000, model="claude-opus")
        self.assertEqual(c.basis, "table")
        self.assertAlmostEqual(c.input_cost_usd, 0.015)
        self.assertAlmostEqual(c.output_cost_usd, 0.075)
        self.assertAlmostEqual(c.total_cost_usd, 0.09)

    def test_local_provider_zero_cost(self) -> None:
        c = estimate_cost("ollama", input_tokens=10000, output_tokens=10000)
        self.assertEqual(c.total_cost_usd, 0.0)
        self.assertEqual(c.basis, "local")

    def test_unknown_provider_fallback_basis(self) -> None:
        c = estimate_cost("mystery", input_tokens=1000, output_tokens=0)
        self.assertEqual(c.basis, "fallback")
        self.assertGreater(c.total_cost_usd, 0.0)

    def test_token_estimate_deterministic(self) -> None:
        self.assertEqual(estimate_tokens_from_text("a" * 400), 100)
        self.assertEqual(estimate_tokens_from_text(""), 0)


class FailureTaxonomyTests(unittest.TestCase):
    def test_markers(self) -> None:
        self.assertEqual(classify_failure("unavailable", "CLI not found on PATH"), FAIL_CLI_MISSING)
        self.assertEqual(classify_failure("unavailable", "endpoint unreachable"), FAIL_ENDPOINT_UNREACHABLE)
        self.assertEqual(classify_failure("blocked", "grant blocked capability"), FAIL_BLOCKED_GRANT)
        self.assertEqual(classify_failure("error", "submit timeout"), FAIL_SUBMIT_ERROR)


class ProviderRuntimeTests(unittest.TestCase):
    def test_live_with_estimate_usage(self) -> None:
        pr = build_provider_runtime(
            selected_provider="claude",
            used_fallback=False,
            metrics={"live": True, "elapsed_ms": 900.0},
            prompt_text="x" * 400,
            output_text="y" * 200,
        )
        self.assertTrue(pr.live)
        self.assertFalse(pr.used_fallback)
        self.assertEqual(pr.usage_basis, "estimate")
        self.assertEqual(pr.input_tokens, 100)
        self.assertEqual(pr.output_tokens, 50)
        self.assertEqual(pr.elapsed_ms, 900.0)
        self.assertGreater(pr.cost.total_cost_usd, 0.0)

    def test_live_counts_from_metrics(self) -> None:
        pr = build_provider_runtime(
            selected_provider="claude",
            used_fallback=False,
            metrics={"live": True, "input_tokens": 1234, "output_tokens": 567},
        )
        self.assertEqual(pr.usage_basis, "live")
        self.assertEqual(pr.input_tokens, 1234)
        self.assertEqual(pr.output_tokens, 567)

    def test_deterministic_not_live_even_if_metrics_claim(self) -> None:
        pr = build_provider_runtime(
            selected_provider="deterministic",
            used_fallback=True,
            metrics={"live": True},  # must be ignored for non-LLM provider
        )
        self.assertFalse(pr.live)
        self.assertEqual(pr.cost.total_cost_usd, 0.0)

    def test_graceful_fallback_chain_recorded(self) -> None:
        # live providers unavailable → land on deterministic, with the failed
        # candidates classified in fallback_from (the WT1 graceful-fallback pin).
        pr = build_provider_runtime(
            selected_provider="deterministic",
            used_fallback=True,
            metrics={},
            attempts=[
                {"provider": "claude", "status": "error", "detail": "submit timeout"},
                {"provider": "codex", "status": "unavailable", "detail": "CLI not found on PATH"},
                {"provider": "deterministic", "status": "fallback", "detail": ""},
            ],
            prompt_text="hello",
            output_text="[role] deterministic fallback take",
        )
        self.assertTrue(pr.used_fallback)
        self.assertFalse(pr.live)
        classes = {s.provider: s.failure_class for s in pr.fallback_from}
        self.assertEqual(classes, {"claude": FAIL_SUBMIT_ERROR, "codex": FAIL_CLI_MISSING})
        # the winner is not listed as a fallback step
        self.assertNotIn("deterministic", classes)


if __name__ == "__main__":
    unittest.main()
