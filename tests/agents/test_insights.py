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


if __name__ == "__main__":
    unittest.main()
