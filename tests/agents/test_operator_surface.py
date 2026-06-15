"""Operator dashboard surface (WT4 integration).

Pins: dashboard field presence (the operator contract), and the rule-derived
"what to do next" logic — waiting proposals, high provider fallback, missing
eval evidence, all-clear.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.harness.operator_surface import (
    compose_dashboard,
    derive_next_actions,
    render_dashboard_markdown,
)


class FieldPresenceTests(unittest.TestCase):
    def test_dashboard_sections_present(self) -> None:
        dash = compose_dashboard()
        d = dash.to_dict()
        self.assertEqual(
            set(d.keys()),
            {"provider", "self_improvement", "eval_summary", "token_efficiency", "next_actions"},
        )
        for key in ("live_provider_runs", "fallback_rate_pct", "rule_first_resolution_rate_pct"):
            self.assertIn(key, d["provider"])
        for key in ("detected", "delegated", "waiting_operator", "blocked"):
            self.assertIn(key, d["self_improvement"])

    def test_render_contains_all_sections(self) -> None:
        md = render_dashboard_markdown(compose_dashboard())
        for header in ("Provider runtime", "Self-improvement loop", "Eval gate",
                       "Token efficiency", "What to do next"):
            self.assertIn(header, md)


class NextActionTests(unittest.TestCase):
    def test_waiting_operator_is_top_action(self) -> None:
        actions = derive_next_actions(
            usage=None,
            eval_comparison={"comparison": []},
            self_improvement={"waiting_operator": 2, "blocked": 0},
        )
        self.assertIn("WAITING operator", actions[0])

    def test_high_fallback_and_failure_hints(self) -> None:
        usage = {
            "receipts_with_provider_runtime": 4,
            "provider_fallback_rate_pct": 75.0,
            "provider_failure_distribution": {"cli_not_found": 1, "endpoint_unreachable": 1},
        }
        actions = derive_next_actions(
            usage=usage, eval_comparison={"comparison": []}, self_improvement=None
        )
        joined = "\n".join(actions)
        self.assertIn("fallback rate 75.0%", joined)
        self.assertIn("CLI is missing", joined)
        self.assertIn("endpoint is unreachable", joined)

    def test_missing_eval_evidence_hint(self) -> None:
        actions = derive_next_actions(usage=None, eval_comparison=None, self_improvement=None)
        self.assertTrue(any("yule harness eval" in a for a in actions))

    def test_all_clear(self) -> None:
        actions = derive_next_actions(
            usage={"receipts_with_provider_runtime": 2, "provider_fallback_rate_pct": 0.0,
                   "provider_failure_distribution": {}},
            eval_comparison={"comparison": [{"variant": "current"}]},
            self_improvement={"waiting_operator": 0, "blocked": 0},
        )
        self.assertEqual(actions, ("all clear — no operator action required.",))

    def test_eval_block_reflects_availability(self) -> None:
        dash = compose_dashboard(eval_comparison={"schema_version": "1.0", "comparison": [
            {"variant": "current", "success_rate_pct": 100.0, "total_cost_usd": 0.01,
             "avg_latency_ms": 600.0, "rule_first_ratio_pct": 50.0}]})
        self.assertTrue(dash.eval_summary["available"])
        self.assertIn("current", dash.eval_summary["variants"])


if __name__ == "__main__":
    unittest.main()
