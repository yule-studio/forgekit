"""compact→vault protocol — checkpoints, receipt, /clear guard (issue #185, item F)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.harness.compaction_protocol import (
    Checkpoint,
    ClearBlockedError,
    compaction_candidates,
    evaluate_clear,
    require_clear_allowed,
    run_compaction_to_vault,
)
from yule_engineering.agents.harness.context_compaction import CompactionTurn


def _turns():
    # enough middle 'take' turns that some fold past the head/tail protection
    middle = [
        CompactionTurn(i, f"role{i}", "take", "x" * 200, audit_id=f"a{i}")
        for i in range(1, 9)
    ]
    return [
        CompactionTurn(0, "user", "prompt", "원문 요청: 결제 연동"),
        *middle,
        CompactionTurn(9, "tech-lead", "synthesis", "합의: 1차 스코프"),
    ]


class CheckpointTests(unittest.TestCase):
    def test_always_offer_checkpoint_yields_candidate(self) -> None:
        cands = compaction_candidates([Checkpoint.BIG_IMPL_DONE], enabled=False)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].checkpoint, Checkpoint.BIG_IMPL_DONE)
        self.assertFalse(cands[0].auto)  # flag off → explicit/operator only

    def test_context_threshold_respects_ratio(self) -> None:
        below = compaction_candidates(
            [Checkpoint.CONTEXT_THRESHOLD], context_ratio=0.3, enabled=True
        )
        self.assertEqual(below, [])
        above = compaction_candidates(
            [Checkpoint.CONTEXT_THRESHOLD], context_ratio=0.6, enabled=True
        )
        self.assertEqual(len(above), 1)
        self.assertTrue(above[0].auto)

    def test_dedup_checkpoints(self) -> None:
        cands = compaction_candidates(
            [Checkpoint.SESSION_END, Checkpoint.SESSION_END], enabled=True
        )
        self.assertEqual(len(cands), 1)


class RunCompactionTests(unittest.TestCase):
    def test_writes_note_and_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note, receipt = run_compaction_to_vault(
                _turns(),
                session_id="sess-f",
                vault_root=Path(tmp),
                project="yule-studio-agent",
                checkpoint=Checkpoint.PRE_VERIFICATION,
                original_prompt="원문 요청: 결제 연동",
            )
            self.assertIsNotNone(note)
            self.assertEqual(receipt.status, "written")
            self.assertFalse(receipt.committed)  # never commits (L3 gate)
            self.assertEqual(receipt.checkpoint, "pre_verification")
            self.assertGreater(receipt.saved_tokens, 0)
            self.assertEqual(receipt.token_source, "estimate")
            self.assertTrue((Path(tmp) / receipt.task_log_note_path).is_file())

    def test_live_compact_boundary_overrides_tokens(self) -> None:
        class _Boundary:
            parsed = True
            pre_tokens = 9000
            post_tokens = 1200
            warning = None

        with tempfile.TemporaryDirectory() as tmp:
            _note, receipt = run_compaction_to_vault(
                _turns(),
                session_id="sess-live",
                vault_root=Path(tmp),
                project="yule-studio-agent",
                compact_boundary=_Boundary(),
            )
            self.assertEqual(receipt.token_source, "live_compact_boundary")
            self.assertEqual(receipt.pre_tokens, 9000)
            self.assertEqual(receipt.saved_tokens, 7800)

    def test_unparsed_boundary_falls_back_to_estimate(self) -> None:
        class _Boundary:
            parsed = False
            pre_tokens = None
            post_tokens = None
            warning = "no compact_boundary event"

        with tempfile.TemporaryDirectory() as tmp:
            _note, receipt = run_compaction_to_vault(
                _turns(),
                session_id="sess-fb",
                vault_root=Path(tmp),
                project="yule-studio-agent",
                compact_boundary=_Boundary(),
            )
            self.assertEqual(receipt.token_source, "estimate")
            self.assertTrue(any("live /compact token capture unavailable" in w for w in receipt.warnings))

    def test_empty_turns_is_not_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note, receipt = run_compaction_to_vault(
                [], session_id="empty", vault_root=Path(tmp), project="yule-studio-agent"
            )
            self.assertIsNone(note)
            self.assertEqual(receipt.status, "not_run")
            self.assertTrue(receipt.warnings)


class ClearGuardTests(unittest.TestCase):
    def _receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            _note, receipt = run_compaction_to_vault(
                _turns(),
                session_id="sess-f",
                vault_root=Path(tmp),
                project="yule-studio-agent",
            )
            return receipt

    def test_clear_blocked_without_receipt(self) -> None:
        decision = evaluate_clear(None, session_id="sess-f")
        self.assertFalse(decision.allowed)

    def test_clear_allowed_after_vault_record(self) -> None:
        decision = evaluate_clear(self._receipt(), session_id="sess-f")
        self.assertTrue(decision.allowed)

    def test_clear_blocked_on_session_mismatch(self) -> None:
        decision = evaluate_clear(self._receipt(), session_id="other")
        self.assertFalse(decision.allowed)

    def test_require_clear_raises_when_blocked(self) -> None:
        with self.assertRaises(ClearBlockedError):
            require_clear_allowed(None, session_id="sess-f")


if __name__ == "__main__":
    unittest.main()
