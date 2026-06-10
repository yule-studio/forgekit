"""Map ``code_audit`` results → self-improvement signals + mistake
ledger records. P0-T governance enforcement seed.

본 모듈은 `code_audit.py` 의 pure 결과를 운영 측 surface 에 연결한다:

  * ``audit_to_signals`` — `FileSizeAudit` / `MissingWiringReport` /
    `RecoveryGapReport` 를 `SelfImprovementSignal` 튜플로 변환.
    supervisor watch loop 가 detect_fn 에서 이 시그널을 emit 하면 기존
    self-improvement pipeline (problem ledger → triage → proposal →
    dispatch) 가 그대로 동작한다.
  * ``record_governance_mistakes`` — 같은 audit 결과를 `MistakeLedger`
    에 기록. role 은 항상 ``governance``, pattern 은 키 namespace
    (``architecture:large_file_rule`` 등), signature 는 파일 path 또는
    job_type. 반복 위반은 자동으로 occurrences 증가 → blocker_level
    escalation.

설계 원칙:
- pure-ish: caller 가 audit 객체 + ledger handle 을 주입. 자체 IO 없음.
- 모든 signal 은 evidence dict 에 LOC / 책임 signal / pending 상태 등
  구체 자료를 담아 operator 가 한 줄로 무엇이 잘못됐는지 확인 가능.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, List, Mapping, Optional, Tuple

from ..lifecycle.self_improvement import (
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    SelfImprovementSignal,
)
from .code_audit import (
    FileSizeAudit,
    MissingWiringReport,
    RecoveryGapReport,
)


# ---------------------------------------------------------------------------
# Signal id / mistake ledger key namespaces (P0-T)
# ---------------------------------------------------------------------------


SIGNAL_ARCHITECTURE_LARGE_FILE: str = "architecture:large_file_rule"
SIGNAL_ARCHITECTURE_MIXED_RESPONSIBILITIES: str = "architecture:mixed_responsibilities"
SIGNAL_RUNTIME_MISSING_WORKER_WIRING: str = "runtime:missing_worker_wiring"
SIGNAL_RUNTIME_RETRYABLE_WITHOUT_RECOVERY: str = "runtime:retryable_without_recovery"
SIGNAL_RUNTIME_DUPLICATE_INTAKE_PATH_DIVERGENCE: str = (
    "runtime:duplicate_intake_path_divergence"
)
SIGNAL_RUNTIME_POLICY_NOT_ENFORCED: str = "runtime:policy_not_enforced"


# Mistake ledger role for every governance-recorded mistake. Stable so
# the preflight gate can filter ``role='governance'`` to surface
# architectural debt next to runtime debt.
GOVERNANCE_LEDGER_ROLE: str = "governance"


# ---------------------------------------------------------------------------
# audit → signals
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def audit_to_signals(
    *,
    file_size_audit: Optional[FileSizeAudit] = None,
    missing_wiring: Optional[MissingWiringReport] = None,
    recovery_gap: Optional[RecoveryGapReport] = None,
    duplicate_intake_paths: Iterable[Mapping[str, Any]] = (),
    now_iso: Optional[str] = None,
) -> Tuple[SelfImprovementSignal, ...]:
    """Build a sorted tuple of governance signals from audit results."""

    detected_at = now_iso or _utc_now_iso()
    signals: List[SelfImprovementSignal] = []

    if file_size_audit is not None:
        if file_size_audit.violations:
            signals.append(
                SelfImprovementSignal(
                    signal=SIGNAL_ARCHITECTURE_LARGE_FILE,
                    severity=SEVERITY_HIGH,
                    summary=(
                        f"split_now 위반 {len(file_size_audit.violations)} 건 — "
                        "1000줄 + 책임 ≥ 2 + allowlist / pending 없음. 본 PR 에서 분리 필수."
                    ),
                    evidence={
                        "violations": [
                            {
                                "path": row.path,
                                "loc": row.loc,
                                "responsibilities": list(row.responsibilities),
                                "reason": row.reason,
                            }
                            for row in file_size_audit.violations[:10]
                        ],
                        "total_count": len(file_size_audit.violations),
                    },
                    detected_at=detected_at,
                )
            )
        mixed = [
            row
            for row in file_size_audit.split_pending
            if len(row.responsibilities) >= 3
        ]
        if mixed:
            signals.append(
                SelfImprovementSignal(
                    signal=SIGNAL_ARCHITECTURE_MIXED_RESPONSIBILITIES,
                    severity=SEVERITY_MEDIUM,
                    summary=(
                        f"책임 혼합 (≥ 3 종) split_pending {len(mixed)} 건 — "
                        "분리 axes 약속이 있어도 deadline 내 처리 필요."
                    ),
                    evidence={
                        "files": [
                            {
                                "path": row.path,
                                "loc": row.loc,
                                "responsibilities": list(row.responsibilities),
                            }
                            for row in mixed[:10]
                        ],
                    },
                    detected_at=detected_at,
                )
            )

    if missing_wiring is not None and missing_wiring.is_blocking():
        signals.append(
            SelfImprovementSignal(
                signal=SIGNAL_RUNTIME_MISSING_WORKER_WIRING,
                severity=SEVERITY_HIGH,
                summary=(
                    f"JOB_TYPE 상수 {len(missing_wiring.unmapped_job_types)} 종 "
                    "이 ServiceKind 매핑 없음 — queue 에 enqueue 되지만 consumer 없음."
                ),
                evidence={
                    "unmapped_job_types": list(missing_wiring.unmapped_job_types),
                    "mapped_job_types": list(missing_wiring.mapped_job_types),
                },
                detected_at=detected_at,
            )
        )

    if recovery_gap is not None and recovery_gap.is_blocking():
        signals.append(
            SelfImprovementSignal(
                signal=SIGNAL_RUNTIME_RETRYABLE_WITHOUT_RECOVERY,
                severity=SEVERITY_HIGH,
                summary=(
                    f"failed_retryable reason {len(recovery_gap.uncovered_reasons)} 종 "
                    "이 startup-recovery hook 없음 — stranded rows 발생 가능."
                ),
                evidence={
                    "uncovered_reasons": list(recovery_gap.uncovered_reasons),
                    "covered_reasons": list(recovery_gap.covered_reasons),
                },
                detected_at=detected_at,
            )
        )

    dup_paths = list(duplicate_intake_paths or ())
    if dup_paths:
        signals.append(
            SelfImprovementSignal(
                signal=SIGNAL_RUNTIME_DUPLICATE_INTAKE_PATH_DIVERGENCE,
                severity=SEVERITY_MEDIUM,
                summary=(
                    f"intake 진입 경로 {len(dup_paths)} 곳 — 동일 입력이 다른 "
                    "분류/큐로 가는 divergence 가능."
                ),
                evidence={"paths": dup_paths},
                detected_at=detected_at,
            )
        )

    severity_rank = {SEVERITY_HIGH: 0, SEVERITY_MEDIUM: 1, SEVERITY_LOW: 2}
    signals.sort(key=lambda s: (severity_rank.get(s.severity, 3), s.signal))
    return tuple(signals)


# ---------------------------------------------------------------------------
# audit → mistake ledger
# ---------------------------------------------------------------------------


def record_governance_mistakes(
    *,
    ledger: Any,  # MistakeLedger — typed loose to avoid circular import
    file_size_audit: Optional[FileSizeAudit] = None,
    missing_wiring: Optional[MissingWiringReport] = None,
    recovery_gap: Optional[RecoveryGapReport] = None,
    when: Optional[str] = None,
) -> Tuple[Any, ...]:
    """Persist each audit violation as a mistake record so repeats
    escalate the blocker level automatically.

    ledger.record_mistake 의 단순 wrapper — 본 함수는 어떤 (pattern,
    signature) tuple 들이 governance domain 에 들어가야 하는지 한
    곳에서 결정한다.
    """

    from ..learning.mistake_ledger import BlockerLevel  # local import

    records: List[Any] = []
    if file_size_audit is not None:
        for row in file_size_audit.violations:
            records.append(
                ledger.record_mistake(
                    role=GOVERNANCE_LEDGER_ROLE,
                    pattern=SIGNAL_ARCHITECTURE_LARGE_FILE,
                    signature=row.path,
                    when=when,
                    blocker_level=BlockerLevel.WARNING,
                )
            )
        for row in file_size_audit.split_pending:
            if len(row.responsibilities) >= 3:
                records.append(
                    ledger.record_mistake(
                        role=GOVERNANCE_LEDGER_ROLE,
                        pattern=SIGNAL_ARCHITECTURE_MIXED_RESPONSIBILITIES,
                        signature=row.path,
                        when=when,
                        blocker_level=BlockerLevel.ADVISORY,
                    )
                )

    if missing_wiring is not None:
        for job_type in missing_wiring.unmapped_job_types:
            records.append(
                ledger.record_mistake(
                    role=GOVERNANCE_LEDGER_ROLE,
                    pattern=SIGNAL_RUNTIME_MISSING_WORKER_WIRING,
                    signature=job_type,
                    when=when,
                    blocker_level=BlockerLevel.BLOCK,
                )
            )

    if recovery_gap is not None:
        for reason in recovery_gap.uncovered_reasons:
            records.append(
                ledger.record_mistake(
                    role=GOVERNANCE_LEDGER_ROLE,
                    pattern=SIGNAL_RUNTIME_RETRYABLE_WITHOUT_RECOVERY,
                    signature=reason,
                    when=when,
                    blocker_level=BlockerLevel.WARNING,
                )
            )

    return tuple(records)


__all__ = (
    "GOVERNANCE_LEDGER_ROLE",
    "SIGNAL_ARCHITECTURE_LARGE_FILE",
    "SIGNAL_ARCHITECTURE_MIXED_RESPONSIBILITIES",
    "SIGNAL_RUNTIME_DUPLICATE_INTAKE_PATH_DIVERGENCE",
    "SIGNAL_RUNTIME_MISSING_WORKER_WIRING",
    "SIGNAL_RUNTIME_POLICY_NOT_ENFORCED",
    "SIGNAL_RUNTIME_RETRYABLE_WITHOUT_RECOVERY",
    "audit_to_signals",
    "record_governance_mistakes",
)
