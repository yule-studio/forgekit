"""Token usage ledger (WT2) — JSONL SSoT, rollups, budget, reports. Pure → CI.

Proves: an event appends one JSONL row, rollups aggregate by provider/mode and keep
live vs estimate SEPARATE (never mixed), budget thresholds (70/85/100) cross honestly,
and txt/md/json reports regenerate from the ledger.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import usage as U


class LedgerTests(unittest.TestCase):
    def _path(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        return d / "usage.jsonl"

    def test_append_and_read_roundtrip(self) -> None:
        p = self._path()
        ev = U.UsageEvent(ts="2026-06-18T10:00:00", mode="Interactive", provider="ollama",
                          model="gemma3", total_tokens=12, usage_basis=U.BASIS_ESTIMATE)
        self.assertTrue(U.append_event(ev, path=p))
        rows = U.read_events(path=p)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["provider"], "ollama")
        self.assertEqual(rows[0]["usage_basis"], "estimate")

    def test_day_filter(self) -> None:
        p = self._path()
        U.append_event(U.UsageEvent(ts="2026-06-18T01:00:00", total_tokens=5), path=p)
        U.append_event(U.UsageEvent(ts="2026-06-17T01:00:00", total_tokens=9), path=p)
        self.assertEqual(len(U.read_events(path=p, day="2026-06-18")), 1)


class RollupTests(unittest.TestCase):
    _ROWS = [
        {"provider": "ollama", "mode": "Interactive", "total_tokens": 10, "usage_basis": "estimate"},
        {"provider": "ollama", "mode": "Cost-save", "total_tokens": 20, "usage_basis": "estimate"},
        {"provider": "gemini", "mode": "Research", "total_tokens": 30, "usage_basis": "live"},
        {"provider": "ollama", "mode": "Interactive", "total_tokens": 5, "usage_basis": "estimate",
         "throttled": True},
    ]

    def test_aggregates_and_keeps_basis_separate(self) -> None:
        r = U.rollup(self._ROWS)
        self.assertEqual(r.total_tokens, 65)
        self.assertEqual(r.by_provider["ollama"], 35)
        self.assertEqual(r.by_mode["Interactive"], 15)
        # live and estimate are NOT mixed into one number
        self.assertEqual(r.live_tokens, 30)
        self.assertEqual(r.estimate_tokens, 35)
        self.assertEqual(r.throttled, 1)

    def test_top_by_tokens(self) -> None:
        top = U.top_by_tokens(self._ROWS, limit=1)
        self.assertEqual(top[0]["total_tokens"], 30)


class BudgetTests(unittest.TestCase):
    def test_threshold_crossing(self) -> None:
        self.assertEqual(U.evaluate_budget(0, 1000).crossed, ())
        self.assertEqual(U.evaluate_budget(750, 1000).crossed, (0.70,))
        self.assertEqual(U.evaluate_budget(900, 1000).crossed, (0.70, 0.85))
        over = U.evaluate_budget(1100, 1000)
        self.assertTrue(over.over)
        self.assertIn(1.00, over.crossed)

    def test_unbounded_budget_no_crossing(self) -> None:
        self.assertEqual(U.evaluate_budget(9999, 0).crossed, ())

    def test_budget_from_config(self) -> None:
        self.assertEqual(U.budget_from_config({"daily_token_budget": 5000}), 5000)
        self.assertEqual(U.budget_from_config({}), 0)

    def test_alert_message_actionable(self) -> None:
        msg = U.alert_message(U.evaluate_budget(1100, 1000))
        self.assertIn("초과", msg)
        self.assertIn("cost-save", msg)


class ReportTests(unittest.TestCase):
    def test_reports_regenerate_from_rollup(self) -> None:
        r = U.rollup(RollupTests._ROWS)
        txt, md, js = U.to_txt(r), U.to_md(r), U.to_json(r)
        self.assertIn("total tokens", txt)
        self.assertIn("live/estimate", md)
        self.assertEqual(json.loads(js)["total_tokens"], 65)

    def test_write_reports(self) -> None:
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        written = U.write_reports(U.rollup(RollupTests._ROWS), d)
        self.assertEqual(len(written), 3)
        self.assertTrue(all(p.exists() for p in written))


if __name__ == "__main__":
    unittest.main()
