"""Live /compact canary — estimate vs live, graceful fallback (token-eff Phase 2)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.harness.compact_canary import (
    canary_enabled,
    run_compact_canary,
)
from yule_engineering.agents.harness.context_compaction import CompactionTurn


def _turns():
    middle = [CompactionTurn(i, f"r{i}", "take", "x" * 200, audit_id=f"a{i}") for i in range(1, 9)]
    return [CompactionTurn(0, "user", "prompt", "원문")] + middle + [
        CompactionTurn(9, "tl", "synthesis", "합의")
    ]


class _Boundary:
    def __init__(self, pre, post, warning=None):
        self.pre_tokens = pre
        self.post_tokens = post
        self.warning = warning

    @property
    def parsed(self):
        return self.pre_tokens is not None and self.post_tokens is not None


class FlagTests(unittest.TestCase):
    def test_default_off(self) -> None:
        self.assertFalse(canary_enabled({}))
        self.assertTrue(canary_enabled({"YULE_COMPACT_LIVE_CANARY_ENABLED": "true"}))


class EstimateModeTests(unittest.TestCase):
    def test_estimate_mode_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note, receipt, report = run_compact_canary(
                _turns(), session_id="s1", vault_root=Path(tmp),
                project="yule-studio-agent", enabled=False,
            )
            self.assertEqual(report.mode, "estimate")
            self.assertEqual(receipt.token_source, "estimate")
            self.assertIsNone(report.live_pre)
            self.assertIsNone(report.estimate_error_pct)
            self.assertTrue((Path(tmp) / receipt.task_log_note_path).is_file())

    def test_enabled_but_no_compact_fn_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _n, receipt, report = run_compact_canary(
                _turns(), session_id="s1", vault_root=Path(tmp),
                project="yule-studio-agent", enabled=True, compact_fn=None,
            )
            self.assertEqual(report.mode, "estimate")
            self.assertTrue(any("no compact_fn" in w for w in report.warnings))


class LiveModeTests(unittest.TestCase):
    def test_live_tokens_authoritative_and_error_computed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _n, receipt, report = run_compact_canary(
                _turns(), session_id="s1", vault_root=Path(tmp),
                project="yule-studio-agent", enabled=True,
                compact_fn=lambda focus: _Boundary(9000, 1200),
            )
            self.assertEqual(report.mode, "live")
            self.assertEqual(receipt.token_source, "live_compact_boundary")
            self.assertEqual(report.live_pre, 9000)
            self.assertEqual(receipt.pre_tokens, 9000)
            self.assertIsNotNone(report.estimate_error_pct)
            # estimate is still reported alongside live
            self.assertGreater(report.estimate_pre, 0)

    def test_unparsed_boundary_degrades_to_estimate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _n, receipt, report = run_compact_canary(
                _turns(), session_id="s1", vault_root=Path(tmp),
                project="yule-studio-agent", enabled=True,
                compact_fn=lambda focus: _Boundary(None, None, warning="no boundary event"),
            )
            self.assertEqual(report.mode, "estimate")
            self.assertEqual(receipt.token_source, "estimate")

    def test_compact_fn_raise_is_graceful(self) -> None:
        def _boom(focus):
            raise RuntimeError("cli exploded")

        with tempfile.TemporaryDirectory() as tmp:
            _n, receipt, report = run_compact_canary(
                _turns(), session_id="s1", vault_root=Path(tmp),
                project="yule-studio-agent", enabled=True, compact_fn=_boom,
            )
            self.assertEqual(report.mode, "estimate")
            self.assertTrue(any("raised" in w for w in report.warnings))

    def test_render_and_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _n, _r, report = run_compact_canary(
                _turns(), session_id="s1", vault_root=Path(tmp),
                project="yule-studio-agent", enabled=True,
                compact_fn=lambda focus: _Boundary(5000, 1000),
            )
            text = report.render()
            self.assertIn("live mode", text)
            self.assertIn("estimate error vs live", text)
            self.assertEqual(report.to_dict()["mode"], "live")


if __name__ == "__main__":
    unittest.main()
