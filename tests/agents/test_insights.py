"""Token-efficiency insights aggregation (Phase 4)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.harness.insights import (
    aggregate_delta_dicts,
    aggregate_receipts,
    render_markdown,
    scan_token_efficiency_evidence,
)


def _delta(slug, before, after):
    return {
        "slug": slug,
        "totals": {
            "input_tokens_before": before,
            "input_tokens_after": after,
            "input_tokens_saved": before - after,
            "input_reduction_pct": round((before - after) / before * 100, 1),
        },
    }


class AggregateTests(unittest.TestCase):
    def test_sums_and_pct(self) -> None:
        ins = aggregate_delta_dicts([_delta("a", 1000, 400), _delta("b", 500, 250)])
        self.assertEqual(ins.runs, 2)
        self.assertEqual(ins.input_before, 1500)
        self.assertEqual(ins.input_after, 650)
        self.assertEqual(ins.saved, 850)
        self.assertEqual(ins.reduction_pct, round(850 / 1500 * 100, 1))

    def test_missing_totals_warns(self) -> None:
        ins = aggregate_delta_dicts([{"slug": "bad"}])
        self.assertEqual(ins.runs, 0)
        self.assertTrue(ins.warnings)

    def test_render(self) -> None:
        md = render_markdown(aggregate_delta_dicts([_delta("x", 100, 40)]))
        self.assertIn("Token efficiency insights", md)
        self.assertIn("−60", md)


class ScanTests(unittest.TestCase):
    def test_scans_delta_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for slug, b, a in [("r1", 1000, 400), ("r2", 800, 300)]:
                d = root / f"2026-06-15-{slug}"
                d.mkdir()
                (d / "delta.json").write_text(json.dumps(_delta(slug, b, a)), encoding="utf-8")
            ins = scan_token_efficiency_evidence(root)
            self.assertEqual(ins.runs, 2)
            self.assertEqual(ins.saved, 600 + 500)

    def test_missing_dir(self) -> None:
        ins = scan_token_efficiency_evidence(Path("/nonexistent/runs/xyz"))
        self.assertEqual(ins.runs, 0)
        self.assertTrue(ins.warnings)


class ReceiptAggregateTests(unittest.TestCase):
    def test_rolls_up_token_efficiency(self) -> None:
        receipts = [
            {"token_efficiency": {"previous_decisions_saved": 100, "source_context_saved": 20, "compaction_applied": True}},
            {"token_efficiency": {"previous_decisions_saved": 0, "source_context_saved": 30, "compaction_applied": False}},
            {"no": "te"},
        ]
        agg = aggregate_receipts(receipts)
        self.assertEqual(agg["receipts_with_token_efficiency"], 2)
        self.assertEqual(agg["previous_decisions_saved"], 100)
        self.assertEqual(agg["source_context_saved"], 50)
        self.assertEqual(agg["compaction_applied_runs"], 1)
        self.assertEqual(agg["total_saved"], 150)

    def test_llm_usage_rollup(self) -> None:
        from yule_engineering.agents.harness.insights import render_usage_markdown

        receipts = [
            {"optimization": {"resolution_mode": "rule_first", "llm_used": False, "bypassed_live_llm": True, "selected_provider": "deterministic"}},
            {"optimization": {"resolution_mode": "rule_first", "llm_used": False, "bypassed_live_llm": True, "selected_provider": "deterministic"}},
            {"optimization": {"resolution_mode": "llm_required", "llm_used": True, "bypassed_live_llm": False, "selected_provider": "claude"}},
        ]
        agg = aggregate_receipts(receipts)
        self.assertEqual(agg["receipts_with_optimization"], 3)
        self.assertEqual(agg["rule_resolved_runs"], 2)
        self.assertEqual(agg["llm_used_runs"], 1)
        self.assertEqual(agg["llm_bypassed_runs"], 2)
        self.assertEqual(agg["resolution_mode_distribution"], {"rule_first": 2, "llm_required": 1})
        self.assertEqual(agg["provider_usage"], {"deterministic": 2, "claude": 1})
        self.assertAlmostEqual(agg["live_llm_avoided_rate_pct"], round(2 / 3 * 100, 1))
        md = render_usage_markdown(agg)
        self.assertIn("live LLM avoided", md)
        self.assertIn("resolution_mode distribution", md)

    def test_provider_runtime_rollup(self) -> None:
        from yule_engineering.agents.harness.insights import render_usage_markdown

        receipts = [
            {"provider_runtime": {
                "selected_provider": "claude", "live": True, "used_fallback": False,
                "elapsed_ms": 1000.0, "total_tokens": 150,
                "cost": {"total_cost_usd": 0.005}, "fallback_from": []}},
            {"provider_runtime": {
                "selected_provider": "deterministic", "live": False, "used_fallback": True,
                "elapsed_ms": 200.0, "total_tokens": 10,
                "cost": {"total_cost_usd": 0.0},
                "fallback_from": [{"provider": "claude", "failure_class": "submit_error"}]}},
        ]
        agg = aggregate_receipts(receipts)
        self.assertEqual(agg["receipts_with_provider_runtime"], 2)
        self.assertEqual(agg["live_provider_runs"], 1)
        self.assertEqual(agg["provider_fallback_runs"], 1)
        self.assertEqual(agg["provider_fallback_rate_pct"], 50.0)
        self.assertAlmostEqual(agg["total_cost_usd"], 0.005)
        self.assertEqual(agg["avg_latency_ms"], 600.0)
        self.assertEqual(agg["provider_failure_distribution"], {"submit_error": 1})
        md = render_usage_markdown(agg)
        self.assertIn("provider runtime", md)
        self.assertIn("fallback rate", md)


if __name__ == "__main__":
    unittest.main()
