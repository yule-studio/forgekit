"""Tests for `governance/code_audit_signals.py` — audit → signals +
mistake ledger seed."""

from __future__ import annotations

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


def test_audit_to_signals_emits_large_file_signal_when_violations_present() -> None:
    audit = FileSizeAudit(
        rows=(),
        violations=(
            FileSizeRow(
                path="src/yule_orchestrator/foo.py",
                loc=1500,
                verdict=VERDICT_SPLIT_NOW,
                reason="1500 LOC + 4 responsibilities",
                responsibilities=("intake", "routing", "formatting", "external_integration"),
            ),
        ),
    )

    signals = audit_to_signals(file_size_audit=audit)

    [sig] = signals
    assert sig.signal == SIGNAL_ARCHITECTURE_LARGE_FILE
    assert sig.severity == "high"
    assert sig.evidence["total_count"] == 1
    assert sig.evidence["violations"][0]["path"] == "src/yule_orchestrator/foo.py"


def test_audit_to_signals_emits_mixed_responsibilities_when_pending_files_have_3_plus() -> None:
    audit = FileSizeAudit(
        rows=(),
        split_pending=(
            FileSizeRow(
                path="src/yule_orchestrator/big.py",
                loc=1200,
                verdict=VERDICT_SPLIT_PENDING,
                reason="deadline 2099-01-01",
                responsibilities=("intake", "routing", "formatting", "external_integration"),
            ),
        ),
    )

    signals = audit_to_signals(file_size_audit=audit)

    assert any(s.signal == SIGNAL_ARCHITECTURE_MIXED_RESPONSIBILITIES for s in signals)


def test_audit_to_signals_emits_missing_worker_wiring_when_unmapped() -> None:
    report = MissingWiringReport(
        unmapped_job_types=("orphan_job_type",),
        mapped_job_types=("coding_execute",),
    )

    signals = audit_to_signals(missing_wiring=report)

    [sig] = signals
    assert sig.signal == SIGNAL_RUNTIME_MISSING_WORKER_WIRING
    assert sig.severity == "high"
    assert sig.evidence["unmapped_job_types"] == ["orphan_job_type"]


def test_audit_to_signals_emits_recovery_gap_signal_when_uncovered() -> None:
    report = RecoveryGapReport(
        uncovered_reasons=("orphan_reason",),
        covered_reasons=(),
    )

    signals = audit_to_signals(recovery_gap=report)

    [sig] = signals
    assert sig.signal == SIGNAL_RUNTIME_RETRYABLE_WITHOUT_RECOVERY
    assert sig.evidence["uncovered_reasons"] == ["orphan_reason"]


def test_audit_to_signals_returns_empty_when_everything_clean() -> None:
    signals = audit_to_signals(
        file_size_audit=FileSizeAudit(rows=()),
        missing_wiring=MissingWiringReport(
            unmapped_job_types=(), mapped_job_types=()
        ),
        recovery_gap=RecoveryGapReport(
            uncovered_reasons=(), covered_reasons=()
        ),
    )
    assert signals == ()


def test_audit_to_signals_sorts_high_severity_first() -> None:
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

    assert signals[0].severity == "high"
    assert signals[-1].severity in ("medium", "low")


def test_record_governance_mistakes_writes_records_with_governance_role() -> None:
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

    assert len(records) == 1
    record = records[0]
    assert record.role == GOVERNANCE_LEDGER_ROLE
    assert record.pattern == SIGNAL_ARCHITECTURE_LARGE_FILE
    assert record.signature == "src/yule_orchestrator/foo.py"
    assert record.blocker_level == BlockerLevel.WARNING


def test_record_governance_mistakes_escalates_on_repeat() -> None:
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

    assert first[0].occurrences == 1
    assert second[0].occurrences == 2


def test_record_governance_mistakes_records_missing_wiring_as_block_level() -> None:
    ledger = MistakeLedger(database_path=":memory:")
    report = MissingWiringReport(
        unmapped_job_types=("orphan",),
        mapped_job_types=(),
    )

    [record] = record_governance_mistakes(ledger=ledger, missing_wiring=report)

    assert record.pattern == SIGNAL_RUNTIME_MISSING_WORKER_WIRING
    assert record.signature == "orphan"
    assert record.blocker_level == BlockerLevel.BLOCK


def test_record_governance_mistakes_records_recovery_gap() -> None:
    ledger = MistakeLedger(database_path=":memory:")
    report = RecoveryGapReport(
        uncovered_reasons=("uncovered_reason",), covered_reasons=()
    )

    [record] = record_governance_mistakes(ledger=ledger, recovery_gap=report)

    assert record.pattern == SIGNAL_RUNTIME_RETRYABLE_WITHOUT_RECOVERY
    assert record.signature == "uncovered_reason"
