"""Quantitative eval gate (WT3).

Pins: metrics-schema stability (the CI/operator contract), determinism,
baseline-vs-current routing correctness, the generic dimension plumbing (so a
future design-adherence axis drops in without a schema change), and the
multi-variant comparison shape.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.harness.eval_harness import (
    DEFAULT_TASK_SET,
    EVAL_SCHEMA_VERSION,
    compare_variants,
    render_comparison_markdown,
    run_eval,
)


_REPORT_KEYS = {
    "schema_version", "variant", "n_tasks", "success_rate_pct",
    "total_input_tokens", "total_output_tokens", "total_cost_usd",
    "avg_latency_ms", "rule_first_ratio_pct", "llm_used_runs",
    "provider_breakdown", "dimension_scores", "results",
}
_RESULT_KEYS = {
    "task_id", "capability_class", "resolution_mode", "rule_first", "llm_used",
    "provider", "input_tokens", "output_tokens", "cost_usd", "latency_ms",
    "success", "dimensions",
}


class SchemaStabilityTests(unittest.TestCase):
    def test_report_schema_keys(self) -> None:
        d = run_eval("current").to_dict()
        self.assertEqual(set(d.keys()), _REPORT_KEYS)
        self.assertEqual(d["schema_version"], EVAL_SCHEMA_VERSION)

    def test_result_schema_keys(self) -> None:
        d = run_eval("current").to_dict()["results"][0]
        self.assertEqual(set(d.keys()), _RESULT_KEYS)

    def test_schema_version_pinned(self) -> None:
        # bump deliberately — this guards accidental shape drift.
        self.assertEqual(EVAL_SCHEMA_VERSION, "1.0")


class DeterminismTests(unittest.TestCase):
    def test_same_inputs_same_metrics(self) -> None:
        a = run_eval("current").to_dict()
        b = run_eval("current").to_dict()
        self.assertEqual(a, b)


class RoutingCorrectnessTests(unittest.TestCase):
    def test_baseline_worse_than_current(self) -> None:
        baseline = run_eval("baseline")
        current = run_eval("current")
        # minimization OFF mis-routes the rule-first tasks → lower correctness,
        # higher cost, more live-LLM runs.
        self.assertLess(baseline.success_rate_pct, current.success_rate_pct)
        self.assertEqual(current.success_rate_pct, 100.0)
        self.assertGreater(baseline.total_cost_usd, current.total_cost_usd)
        self.assertGreater(baseline.llm_used_runs, current.llm_used_runs)
        self.assertEqual(current.rule_first_ratio_pct, 50.0)

    def test_cheap_variant_zero_cost(self) -> None:
        cheap = run_eval("cheap_llm")
        self.assertEqual(cheap.total_cost_usd, 0.0)
        self.assertEqual(cheap.success_rate_pct, 100.0)


class DimensionPluggabilityTests(unittest.TestCase):
    def test_custom_dimension_aggregated_generically(self) -> None:
        # A future "design_adherence"-style scorer drops in with no schema change.
        def constant_scorer(task, fields):
            return 0.5

        from yule_engineering.agents.harness.eval_harness import DEFAULT_DIMENSIONS

        dims = dict(DEFAULT_DIMENSIONS)
        dims["design_adherence"] = constant_scorer
        report = run_eval("current", dimensions=dims)
        self.assertIn("design_adherence", report.dimension_scores)
        self.assertEqual(report.dimension_scores["design_adherence"], 0.5)
        # built-in dimension still present
        self.assertEqual(report.dimension_scores["routing_correctness"], 1.0)


class ComparisonTests(unittest.TestCase):
    def test_compare_three_axes(self) -> None:
        comp = compare_variants()
        names = [row["variant"] for row in comp["comparison"]]
        self.assertEqual(names, ["baseline", "current", "cheap_llm"])
        for row in comp["comparison"]:
            for key in ("success_rate_pct", "total_cost_usd", "avg_latency_ms",
                        "rule_first_ratio_pct", "llm_used_runs", "total_tokens"):
                self.assertIn(key, row)
        md = render_comparison_markdown(comp)
        self.assertIn("variant comparison", md)
        self.assertIn("rule-first", md)

    def test_task_set_is_fixed(self) -> None:
        self.assertEqual(len(DEFAULT_TASK_SET), 8)
        self.assertEqual(DEFAULT_TASK_SET[0].task_id, "t01-classify")


if __name__ == "__main__":
    unittest.main()
