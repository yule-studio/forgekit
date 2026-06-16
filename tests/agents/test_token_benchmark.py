"""Token-efficiency benchmark — deterministic scenarios + delta (token_benchmark)."""

from __future__ import annotations

import json
import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.harness import token_benchmark as tb


class DeterminismTests(unittest.TestCase):
    def test_token_metrics_reproducible(self) -> None:
        # token metrics (not wall_time) must be identical across runs
        a = tb.run_benchmark("after", generated_at="x")
        b = tb.run_benchmark("after", generated_at="x")
        for sa, sb in zip(a.scenarios, b.scenarios):
            self.assertEqual(sa.input_tokens_est, sb.input_tokens_est)
            self.assertEqual(sa.saved_tokens_by_compaction, sb.saved_tokens_by_compaction)


class ReductionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = tb.run_benchmark("baseline", generated_at="x")
        self.after = tb.run_benchmark("after", generated_at="x")
        self.delta = tb.compute_delta(self.base, self.after)

    def test_every_scenario_not_worse(self) -> None:
        by = {s.scenario: s for s in self.after.scenarios}
        for b in self.base.scenarios:
            a = by[b.scenario]
            self.assertLessEqual(a.input_tokens_est, b.input_tokens_est, b.scenario)

    def test_total_input_reduced(self) -> None:
        self.assertLess(self.after.total_input_tokens, self.base.total_input_tokens)
        self.assertGreater(self.delta["totals"]["input_tokens_saved"], 0)
        self.assertGreater(self.delta["totals"]["input_reduction_pct"], 0)

    def test_recall_and_context_strongly_reduced(self) -> None:
        rows = {r["scenario"]: r for r in self.delta["scenarios"]}
        self.assertGreater(rows["recall"]["input_reduction_pct"], 20)
        self.assertGreater(rows["context"]["input_reduction_pct"], 20)

    def test_min_metric_fields_present(self) -> None:
        d = self.after.scenarios[0].to_dict()
        for field in (
            "loaded_docs_count", "loaded_policies_count", "input_tokens_est",
            "output_tokens_est", "previous_decisions_size", "source_context_size",
            "retrieved_artifacts_count", "saved_tokens_by_compaction",
            "selected_runner", "wall_time_ms", "warnings_count",
        ):
            self.assertIn(field, d)


class RenderTests(unittest.TestCase):
    def test_markdown_and_json(self) -> None:
        base = tb.run_benchmark("baseline", generated_at="x")
        after = tb.run_benchmark("after", generated_at="x")
        md = tb.render_report_markdown(after)
        self.assertIn("Token efficiency benchmark", md)
        delta_md = tb.render_delta_markdown(tb.compute_delta(base, after))
        self.assertIn("baseline vs after", delta_md)
        payload = json.loads(json.dumps(after.to_dict()))  # round-trips
        self.assertEqual(payload["estimator"], "chars/4 (ceil)")


if __name__ == "__main__":
    unittest.main()
