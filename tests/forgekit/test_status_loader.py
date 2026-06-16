"""forgekit console status shapers (pure)."""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.data.status_loader import (
    summarize_operator_dashboard,
    summarize_text,
)
from forgekit_console.models import LEVEL_WARN


class OperatorDashboardShaperTests(unittest.TestCase):
    def _dash(self, **over):
        base = {
            "provider": {"live_provider_runs": 3, "runs_with_runtime": 5,
                         "fallback_rate_pct": 40.0, "total_cost_usd": 0.01,
                         "rule_first_resolution_rate_pct": 50.0, "live_llm_avoided_rate_pct": 50.0},
            "self_improvement": {"detected": 2, "delegated": 1, "waiting_operator": 1, "blocked": 0},
            "eval_summary": {"variants": ["baseline", "current"]},
            "token_efficiency": {"runs": 2, "saved": 3395, "reduction_pct": 59.1},
            "next_actions": ["respond in #승인-대기"],
        }
        base.update(over)
        return base

    def test_sections_and_next_actions(self) -> None:
        s = summarize_operator_dashboard(self._dash())
        titles = {sec.title for sec in s.sections}
        self.assertEqual(titles, {"provider runtime", "self-improvement", "eval gate", "token efficiency"})
        self.assertEqual(s.next_actions, ("respond in #승인-대기",))

    def test_waiting_operator_raises_warn_alert(self) -> None:
        s = summarize_operator_dashboard(self._dash())
        levels = {a.level for a in s.alerts}
        self.assertIn(LEVEL_WARN, levels)
        self.assertTrue(any("waiting operator" in a.message for a in s.alerts))

    def test_high_fallback_alert(self) -> None:
        d = self._dash(provider={"fallback_rate_pct": 80.0}, self_improvement={})
        s = summarize_operator_dashboard(d)
        self.assertTrue(any("fallback" in a.message for a in s.alerts))

    def test_no_anomaly_yields_info_alert(self) -> None:
        d = self._dash(provider={"fallback_rate_pct": 0.0}, self_improvement={"waiting_operator": 0, "blocked": 0})
        s = summarize_operator_dashboard(d)
        self.assertEqual([a.level for a in s.alerts], ["info"])

    def test_flat_lines_includes_sections_and_actions(self) -> None:
        flat = "\n".join(summarize_operator_dashboard(self._dash()).flat_lines())
        self.assertIn("## provider runtime", flat)
        self.assertIn("## what to do next", flat)


class TextShaperTests(unittest.TestCase):
    def test_truncates_and_flags(self) -> None:
        text = "\n".join(f"line {i}" for i in range(50))
        s = summarize_text("doctor", text, max_lines=10)
        self.assertEqual(len(s.sections[0].lines), 10)
        self.assertTrue(any("truncated" in a.message for a in s.alerts))

    def test_skips_blank_lines(self) -> None:
        s = summarize_text("runtime status", "a\n\n\nb\n")
        self.assertEqual(s.sections[0].lines, ("a", "b"))


if __name__ == "__main__":
    unittest.main()
