"""Tests for `governance/code_audit_signals.py` — audit → signals +
mistake ledger seed.

본 모듈도 **stdlib only** — CI 가 ``python3 -m unittest discover`` 라서
third-party pytest 없이 import 가능해야 한다.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.governance.code_audit import (
    FileSizeAudit,
    FileSizeRow,
    MissingWiringReport,
    RecoveryGapReport,
    VERDICT_SPLIT_NOW,
    VERDICT_SPLIT_PENDING,
)
from yule_orchestrator.agents.governance.code_audit_signals import (
    GOVERNANCE_LEDGER_ROLE,
    SIGNAL_ARCHITECTURE_LARGE_FILE,
    SIGNAL_ARCHITECTURE_MIXED_RESPONSIBILITIES,
    SIGNAL_RUNTIME_MISSING_WORKER_WIRING,
    SIGNAL_RUNTIME_RETRYABLE_WITHOUT_RECOVERY,
    audit_to_signals,
    record_governance_mistakes,
)
from yule_orchestrator.agents.learning.mistake_ledger import (
    BlockerLevel,
    MistakeLedger,
)


# ---------------------------------------------------------------------------
# audit → signals
# ---------------------------------------------------------------------------


class AuditToSignalsTests(unittest.TestCase):
    def test_emits_large_file_signal_when_violations_present(self) -> None:
        audit = FileSizeAudit(
            rows=(),
            violations=(
                FileSizeRow(
                    path="src/yule_orchestrator/foo.py",
                    loc=1500,
                    verdict=VERDICT_SPLIT_NOW,
                    reason="1500 LOC + 4 responsibilities",
                    responsibilities=(
                        "intake",
                        "routing",
                        "formatting",
                        "external_integration",
                    ),
                ),
            ),
        )

        signals = audit_to_signals(file_size_audit=audit)

        self.assertEqual(len(signals), 1)
        sig = signals[0]
        self.assertEqual(sig.signal, SIGNAL_ARCHITECTURE_LARGE_FILE)
        self.assertEqual(sig.severity, "high")
        self.assertEqual(sig.evidence["total_count"], 1)
        self.assertEqual(
            sig.evidence["violations"][0]["path"],
            "src/yule_orchestrator/foo.py",
        )

    def test_emits_mixed_responsibilities_when_pending_files_have_3_plus(self) -> None:
        audit = FileSizeAudit(
            rows=(),
            split_pending=(
                FileSizeRow(
                    path="src/yule_orchestrator/big.py",
                    loc=1200,
                    verdict=VERDICT_SPLIT_PENDING,
                    reason="deadline 2099-01-01",
                    responsibilities=(
                        "intake",
                        "routing",
                        "formatting",
                        "external_integration",
                    ),
                ),
            ),
        )

        signals = audit_to_signals(file_size_audit=audit)

        self.assertTrue(
            any(
                s.signal == SIGNAL_ARCHITECTURE_MIXED_RESPONSIBILITIES
                for s in signals
            )
        )

    def test_emits_missing_worker_wiring_when_unmapped(self) -> None:
        report = MissingWiringReport(
            unmapped_job_types=("orphan_job_type",),
            mapped_job_types=("coding_execute",),
        )

        signals = audit_to_signals(missing_wiring=report)

        self.assertEqual(len(signals), 1)
        sig = signals[0]
        self.assertEqual(sig.signal, SIGNAL_RUNTIME_MISSING_WORKER_WIRING)
        self.assertEqual(sig.severity, "high")
        self.assertEqual(
            sig.evidence["unmapped_job_types"], ["orphan_job_type"]
        )

    def test_emits_recovery_gap_signal_when_uncovered(self) -> None:
        report = RecoveryGapReport(
            uncovered_reasons=("orphan_reason",),
            covered_reasons=(),
        )

        signals = audit_to_signals(recovery_gap=report)

        self.assertEqual(len(signals), 1)
        sig = signals[0]
        self.assertEqual(
            sig.signal, SIGNAL_RUNTIME_RETRYABLE_WITHOUT_RECOVERY
        )
        self.assertEqual(sig.evidence["uncovered_reasons"], ["orphan_reason"])

    def test_returns_empty_when_everything_clean(self) -> None:
        signals = audit_to_signals(
            file_size_audit=FileSizeAudit(rows=()),
            missing_wiring=MissingWiringReport(
                unmapped_job_types=(), mapped_job_types=()
            ),
            recovery_gap=RecoveryGapReport(
                uncovered_reasons=(), covered_reasons=()
            ),
        )
        self.assertEqual(signals, ())

    def test_sorts_high_severity_first(self) -> None:
        file_audit = FileSizeAudit(
            rows=(),
            violations=(
                FileSizeRow(
                    path="src/yule_orchestrator/foo.py",
                    loc=1500,
                    verdict=VERDICT_SPLIT_NOW,
                    reason="...",
                    responsibilities=("a", "b"),
                ),
            ),
        )

        signals = audit_to_signals(
            file_size_audit=file_audit,
            duplicate_intake_paths=[{"path": "/foo", "kind": "slash"}],
        )

        self.assertEqual(signals[0].severity, "high")
        self.assertIn(signals[-1].severity, ("medium", "low"))


# ---------------------------------------------------------------------------
# record_governance_mistakes
# ---------------------------------------------------------------------------


class RecordGovernanceMistakesTests(unittest.TestCase):
    def test_writes_records_with_governance_role(self) -> None:
        ledger = MistakeLedger(database_path=":memory:")
        audit = FileSizeAudit(
            rows=(),
            violations=(
                FileSizeRow(
                    path="src/yule_orchestrator/foo.py",
                    loc=1500,
                    verdict=VERDICT_SPLIT_NOW,
                    reason="...",
                    responsibilities=("intake", "routing"),
                ),
            ),
        )

        records = record_governance_mistakes(ledger=ledger, file_size_audit=audit)

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.role, GOVERNANCE_LEDGER_ROLE)
        self.assertEqual(record.pattern, SIGNAL_ARCHITECTURE_LARGE_FILE)
        self.assertEqual(record.signature, "src/yule_orchestrator/foo.py")
        self.assertEqual(record.blocker_level, BlockerLevel.WARNING)

    def test_escalates_on_repeat(self) -> None:
        ledger = MistakeLedger(database_path=":memory:")
        audit = FileSizeAudit(
            rows=(),
            violations=(
                FileSizeRow(
                    path="src/yule_orchestrator/foo.py",
                    loc=1500,
                    verdict=VERDICT_SPLIT_NOW,
                    reason="...",
                    responsibilities=("intake", "routing"),
                ),
            ),
        )

        first = record_governance_mistakes(ledger=ledger, file_size_audit=audit)
        second = record_governance_mistakes(ledger=ledger, file_size_audit=audit)

        self.assertEqual(first[0].occurrences, 1)
        self.assertEqual(second[0].occurrences, 2)

    def test_records_missing_wiring_as_block_level(self) -> None:
        ledger = MistakeLedger(database_path=":memory:")
        report = MissingWiringReport(
            unmapped_job_types=("orphan",),
            mapped_job_types=(),
        )

        records = record_governance_mistakes(ledger=ledger, missing_wiring=report)
        self.assertEqual(len(records), 1)
        record = records[0]

        self.assertEqual(record.pattern, SIGNAL_RUNTIME_MISSING_WORKER_WIRING)
        self.assertEqual(record.signature, "orphan")
        self.assertEqual(record.blocker_level, BlockerLevel.BLOCK)

    def test_records_recovery_gap(self) -> None:
        ledger = MistakeLedger(database_path=":memory:")
        report = RecoveryGapReport(
            uncovered_reasons=("uncovered_reason",), covered_reasons=()
        )

        records = record_governance_mistakes(ledger=ledger, recovery_gap=report)
        self.assertEqual(len(records), 1)
        record = records[0]

        self.assertEqual(record.pattern, SIGNAL_RUNTIME_RETRYABLE_WITHOUT_RECOVERY)
        self.assertEqual(record.signature, "uncovered_reason")


if __name__ == "__main__":
    unittest.main()
