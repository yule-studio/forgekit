"""Role-runner input compaction hot-path (D) — flag gate + protected preservation."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.standalone_runners import (
    ENV_RUNNER_INPUT_COMPACTION,
    _runner_input_compaction_enabled,
    _slim_runner_input,
)


def _decisions(n=14):
    out = []
    for i in range(n):
        kind = "decision" if i == 2 else "take"
        # each summary ~ 120+ tokens so the channel crosses the 1200-token threshold
        out.append({"role": f"r{i}", "kind": kind, "summary": "긴 의견 본문 분석 " * 60, "entry_id": f"a{i}"})
    return tuple(out)


class FlagTests(unittest.TestCase):
    def test_flag_default_off(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(ENV_RUNNER_INPUT_COMPACTION, None)
            self.assertFalse(_runner_input_compaction_enabled())

    def test_flag_on(self) -> None:
        with patch.dict(os.environ, {ENV_RUNNER_INPUT_COMPACTION: "true"}):
            self.assertTrue(_runner_input_compaction_enabled())


class SlimTests(unittest.TestCase):
    def test_long_decisions_folded_and_saved(self) -> None:
        src = {"title": "T", "summary": "긴 요약 " * 60, "sources": [f"u{i}" for i in range(12)]}
        new_src, new_prev, eff = _slim_runner_input(src, _decisions())
        self.assertTrue(eff.get("compaction_applied"))
        self.assertGreater(eff.get("previous_decisions_saved", 0), 0)
        self.assertGreater(eff.get("source_context_saved", 0), 0)

    def test_protected_recent_and_decision_preserved(self) -> None:
        new_src, new_prev, eff = _slim_runner_input({}, _decisions())
        # recent 4 verbatim
        for d in new_prev[-4:]:
            self.assertNotIn("folded", d)
        # kind=decision (index 2) preserved verbatim
        decision_entries = [d for d in new_prev if d.get("kind") == "decision"]
        self.assertTrue(decision_entries)
        self.assertNotIn("folded", decision_entries[0])

    def test_slim_never_raises_on_bad_input(self) -> None:
        # defensive: odd shapes degrade gracefully
        new_src, new_prev, eff = _slim_runner_input(None, ())
        self.assertEqual(new_prev, ())


if __name__ == "__main__":
    unittest.main()
