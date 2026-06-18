"""Per-provider usage breakdown (WT3) — provider/model/mode + live vs estimate.

Proves the breakdown aggregates per dimension, keeps live and estimate separate (never
summed into one number), and surfaces honestly (empty → honest, not faked).
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.usage import breakdown


_ROWS = [
    {"provider": "ollama", "model": "gemma3", "mode": "interactive",
     "input_tokens": 10, "output_tokens": 20, "total_tokens": 30, "usage_basis": "live"},
    {"provider": "ollama", "model": "gemma3", "mode": "interactive",
     "input_tokens": 5, "output_tokens": 5, "total_tokens": 10, "usage_basis": "estimate"},
    {"provider": "gemini", "model": "gemini-2.5", "mode": "research",
     "input_tokens": 40, "output_tokens": 60, "total_tokens": 100, "usage_basis": "live"},
]


class BreakdownTests(unittest.TestCase):
    def test_by_provider_separates_basis(self) -> None:
        by = {k.key: k for k in breakdown.breakdown_by(_ROWS, "provider")}
        self.assertEqual(by["ollama"].total_tokens, 40)
        self.assertEqual(by["ollama"].live_tokens, 30)        # live and estimate NOT summed
        self.assertEqual(by["ollama"].estimate_tokens, 10)
        self.assertEqual(by["ollama"].basis_label, "live+estimate")
        self.assertEqual(by["gemini"].live_tokens, 100)
        self.assertEqual(by["gemini"].basis_label, "live")

    def test_by_model_and_mode(self) -> None:
        models = {k.key for k in breakdown.breakdown_by(_ROWS, "model")}
        modes = {k.key for k in breakdown.breakdown_by(_ROWS, "mode")}
        self.assertEqual(models, {"gemma3", "gemini-2.5"})
        self.assertEqual(modes, {"interactive", "research"})

    def test_in_out_tracked(self) -> None:
        by = {k.key: k for k in breakdown.breakdown_by(_ROWS, "provider")}
        self.assertEqual((by["ollama"].input_tokens, by["ollama"].output_tokens), (15, 25))

    def test_render_lines_honest(self) -> None:
        lines = "\n".join(breakdown.render_lines(_ROWS))
        self.assertIn("by provider", lines)
        self.assertIn("live", lines)
        self.assertIn("est", lines)
        self.assertIn("ollama", lines)

    def test_empty_is_honest(self) -> None:
        self.assertIn("기록 없음", "\n".join(breakdown.render_lines([])))


if __name__ == "__main__":
    unittest.main()
