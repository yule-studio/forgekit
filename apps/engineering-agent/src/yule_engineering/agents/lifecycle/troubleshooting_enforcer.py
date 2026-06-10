"""Mandatory capture enforcer — `troubleshooting` 가 운영 메모리가 되도록 강제.

사용자 §A + §B + §I 항목을 코드로 강제하는 layer.

핵심 패턴 2 종:

1. **Capture context manager** — `with mandatory_capture(...) as guard:` 블록
   안에서 troubleshooting 이 일어났는데 caller 가 ``guard.record(...)`` 를
   부르지 않은 채 블록을 빠져나가면 :class:`MissingTroubleshootingRecord`
   audit violation 이 발생. operator 가 즉시 보이도록 violations counter 가
   ledger 에 stamp 된다.

2. **Silent-correction helper** — fallback success / retry success /
   live-smoke-fail-then-fix / Claude Code 의 첫 fix 가 불충분해 두 번째 fix 가
   필요했던 경우. caller 가 명시적으로 ``record_silent_correction(...)`` 을
   호출해 § I 의 "조용히 넘어가지 않음" 규칙을 만족.

3. **Claude Code / Codex 경로 helper** — `record_claude_correction(...)` /
   ``record_codex_correction(...)`` 가 LLM 가 작업 중 실수 / 반복 수정한
   사실을 같은 ledger 에 push. § B 의 *Claude Code 도 같은 ledger* 정책.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (
    Any,
    Callable,
    Generator,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from .troubleshooting_ledger import CaptureOutcome, TroubleshootingLedger
from .troubleshooting_record import (
    CaptureReason,
    DETECTED_BY_CLAUDE_CODE,
    DETECTED_BY_CODEX,
    SEVERITY_HIGH,
    SEVERITY_MEDIUM,
    TroubleshootingStatus,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Violations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TroubleshootingViolation:
    """Capture 가 누락된 사건.

    runtime status / agent-ops audit / 운영-리서치 thread 에 한 줄로
    표면화된다. counter 가 누적되면 self-improvement loop 가 follow-up
    제안을 생성 (또 다른 troubleshooting record).
    """

    violation_id: str
    capture_reason: str
    detected_by: str
    scope: str
    rationale: str
    recorded_at: str


@dataclass
class EnforcementJournal:
    """In-process violation 누적기.

    `MissingTroubleshootingRecord` 가 raise 되지 않게 — 대신 audit row 로
    남긴다. Hard fail 보다 audit 로 표면화하는 게 운영-리서치 관점에서
    안전 (자체 개선 loop 가 다시 거기서 잡으니까).
    """

    violations: List[TroubleshootingViolation] = field(default_factory=list)

    def record(
        self,
        *,
        capture_reason: str,
        detected_by: str,
        scope: str,
        rationale: str,
    ) -> TroubleshootingViolation:
        violation = TroubleshootingViolation(
            violation_id=f"ts-violation-{int(_utc_now().timestamp() * 1000):013d}",
            capture_reason=capture_reason,
            detected_by=detected_by,
            scope=scope,
            rationale=rationale,
            recorded_at=_utc_now().isoformat(),
        )
        self.violations.append(violation)
        return violation

    def recent(self, *, limit: int = 10) -> Tuple[TroubleshootingViolation, ...]:
        return tuple(self.violations[-max(0, int(limit)):])

    def clear(self) -> None:
        self.violations.clear()


# ---------------------------------------------------------------------------
# Mandatory capture context manager
# ---------------------------------------------------------------------------


@dataclass
class _CaptureGuard:
    """Inside `with mandatory_capture(...)` block 에서 caller 가 받는 guard.

    호출 패턴:

        with mandatory_capture(
            ledger,
            capture_reason=CaptureReason.LIVE_SMOKE_FAILURE,
            detected_by="runtime/gateway",
            scope="approval_reply_router",
        ) as guard:
            ...
            if failure_observed:
                guard.record(
                    title="...",
                    symptom="...",
                    ...
                )
                # 또는 :meth:`mark_silent_correction` 으로 자동 capture.

    block 을 빠져나갈 때 captured 가 False 면 enforcement journal 에
    violation 이 append.
    """

    ledger: TroubleshootingLedger
    enforcement_journal: EnforcementJournal
    capture_reason: CaptureReason
    detected_by: str
    scope: str
    owner_role: str
    required: bool
    captured_outcomes: List[CaptureOutcome] = field(default_factory=list)
    explicit_skip: bool = False
    explicit_skip_reason: str = ""

    @property
    def captured(self) -> bool:
        return len(self.captured_outcomes) > 0

    def record(
        self,
        *,
        title: str,
        symptom: str,
        severity: str = SEVERITY_MEDIUM,
        **kwargs: Any,
    ) -> CaptureOutcome:
        """ledger.capture(...) 위임 + guard 가 본 사건을 인지했음을 표시."""

        outcome = self.ledger.capture(
            title=title,
            capture_reason=self.capture_reason,
            detected_by=self.detected_by,
            owner_role=kwargs.pop("owner_role", self.owner_role),
            scope=kwargs.pop("scope", self.scope),
            symptom=symptom,
            severity=severity,
            **kwargs,
        )
        self.captured_outcomes.append(outcome)
        return outcome

    def mark_silent_correction(
        self,
        *,
        title: str,
        symptom: str,
        attempted_fix: str = "",
        final_fix: str = "",
        prevention_rule: str = "",
        evidence: str = "",
        severity: str = SEVERITY_MEDIUM,
        **kwargs: Any,
    ) -> CaptureOutcome:
        """fallback / retry / 후속 commit 으로 *조용히 회복된* 경우 capture.

        사용자 § I 의 "no silent correction" 정책의 1급 helper. 호출하면
        ``status=mitigated`` 로 ledger 에 들어가 'fixed' 와 구분된다 — operator
        는 "회복은 됐지만 enforcement 자체가 들어간 건 아니야" 를 한 눈에 본다.
        """

        return self.record(
            title=title,
            symptom=symptom,
            severity=severity,
            attempted_fix=attempted_fix,
            final_fix=final_fix,
            prevention_rule=prevention_rule,
            exact_evidence=evidence,
            status=TroubleshootingStatus.MITIGATED,
            followup_required=not bool(prevention_rule.strip()),
            **kwargs,
        )

    def skip(self, *, reason: str) -> None:
        """이 블록 안에선 troubleshooting 발생하지 않음을 명시.

        normal happy-path 가 spam 을 만들지 않도록 — explicit skip 은
        violation 으로 카운트되지 않는다. § A 의 *정확한 trigger* 가
        실제로 발생했을 때만 record 가 강제.
        """

        self.explicit_skip = True
        self.explicit_skip_reason = reason


@contextmanager
def mandatory_capture(
    ledger: TroubleshootingLedger,
    enforcement_journal: EnforcementJournal,
    *,
    capture_reason: CaptureReason,
    detected_by: str,
    scope: str,
    owner_role: str = "",
    required: bool = True,
) -> Generator[_CaptureGuard, None, None]:
    """Context manager — 사건이 *발생한 경우* 반드시 ``record(...)`` 를
    호출해야 함을 강제한다.

    parameters:
        ledger: fan-out 대상.
        enforcement_journal: violation 누적기.
        capture_reason: A/B/I 항목 enum.
        detected_by: 누가 감지했는지 (runtime/gateway, tooling/claude-code …).
        scope: 영향 범위. signature derivation 의 일부.
        owner_role: ledger.capture 의 default owner_role.
        required: False 면 guard 가 block 끝나도 violation 미발생 —
                  *해당 trigger 가 실제로 발생할지 불확실한* 경로용.

    blocking 동작: violation 은 raise 가 아니라 journal 에 append.
    """

    guard = _CaptureGuard(
        ledger=ledger,
        enforcement_journal=enforcement_journal,
        capture_reason=capture_reason,
        detected_by=detected_by,
        scope=scope,
        owner_role=owner_role,
        required=required,
    )
    try:
        yield guard
    finally:
        if required and not guard.captured and not guard.explicit_skip:
            enforcement_journal.record(
                capture_reason=capture_reason.value,
                detected_by=detected_by,
                scope=scope,
                rationale=(
                    "mandatory_capture block 종료 시 troubleshooting record 누락 — "
                    "사용자 §A/§B/§I 정책 위반. enforcement journal 에 violation 추가."
                ),
            )


# ---------------------------------------------------------------------------
# Claude Code / Codex helpers — §B 항목
# ---------------------------------------------------------------------------


def record_claude_correction(
    ledger: TroubleshootingLedger,
    *,
    title: str,
    symptom: str,
    attempted_fix: str,
    final_fix: str,
    scope: str = "tooling/claude-code",
    prevention_rule: str = "",
    related_files: Iterable[str] = (),
    related_session_ids: Iterable[str] = (),
    related_prs: Iterable[str] = (),
    severity: str = SEVERITY_MEDIUM,
    reason: CaptureReason = CaptureReason.CLAUDE_INSUFFICIENT_FIX_FOLLOWUP,
) -> CaptureOutcome:
    """Claude Code 작업 중 *잘못 판단했다가 다시 고친 사실* 을 ledger 에 push.

    예:
      - 첫 fix 가 회귀 일부만 잡고 다음 commit 에서 보강
      - test green 인데 live 에서 실패해 후속 commit
      - large file rule 을 *문서상 알면서도* 실제 implementation 에서 놓침

    호출은 *수정이 끝난 직후* — guard 안에서 부르거나 직접 부르거나 OK.
    """

    return ledger.capture(
        title=title,
        capture_reason=reason,
        detected_by=DETECTED_BY_CLAUDE_CODE,
        owner_role="claude-code",
        scope=scope,
        symptom=symptom,
        severity=severity,
        attempted_fix=attempted_fix,
        final_fix=final_fix,
        prevention_rule=prevention_rule,
        related_files=related_files,
        related_session_ids=related_session_ids,
        related_prs=related_prs,
        status=TroubleshootingStatus.MITIGATED if final_fix else TroubleshootingStatus.OPEN,
        followup_required=not bool(prevention_rule.strip()),
    )


def record_codex_correction(
    ledger: TroubleshootingLedger,
    *,
    title: str,
    symptom: str,
    attempted_fix: str,
    final_fix: str,
    scope: str = "tooling/codex",
    prevention_rule: str = "",
    related_files: Iterable[str] = (),
    related_session_ids: Iterable[str] = (),
    related_prs: Iterable[str] = (),
    severity: str = SEVERITY_MEDIUM,
    reason: CaptureReason = CaptureReason.CLAUDE_INSUFFICIENT_FIX_FOLLOWUP,
) -> CaptureOutcome:
    """Codex executor 가 같은 종류의 구조 실수를 반복한 경우."""

    return ledger.capture(
        title=title,
        capture_reason=reason,
        detected_by=DETECTED_BY_CODEX,
        owner_role="codex",
        scope=scope,
        symptom=symptom,
        severity=severity,
        attempted_fix=attempted_fix,
        final_fix=final_fix,
        prevention_rule=prevention_rule,
        related_files=related_files,
        related_session_ids=related_session_ids,
        related_prs=related_prs,
        status=TroubleshootingStatus.MITIGATED if final_fix else TroubleshootingStatus.OPEN,
        followup_required=not bool(prevention_rule.strip()),
    )


# ---------------------------------------------------------------------------
# Module-level helpers — silent correction without context manager
# ---------------------------------------------------------------------------


def record_silent_correction(
    ledger: TroubleshootingLedger,
    *,
    capture_reason: CaptureReason,
    title: str,
    symptom: str,
    detected_by: str,
    scope: str,
    owner_role: str,
    attempted_fix: str = "",
    final_fix: str = "",
    prevention_rule: str = "",
    severity: str = SEVERITY_MEDIUM,
    related_files: Iterable[str] = (),
    related_session_ids: Iterable[str] = (),
    related_job_ids: Iterable[str] = (),
) -> CaptureOutcome:
    """Context manager 없이 직접 fallback / retry 성공을 ledger 에 push.

    runtime worker / dispatcher / self-improvement loop 가 fallback 으로
    회복했을 때 즉시 호출해 §I 정책을 만족.
    """

    return ledger.capture(
        title=title,
        capture_reason=capture_reason,
        detected_by=detected_by,
        owner_role=owner_role,
        scope=scope,
        symptom=symptom,
        severity=severity,
        attempted_fix=attempted_fix,
        final_fix=final_fix,
        prevention_rule=prevention_rule,
        related_files=related_files,
        related_session_ids=related_session_ids,
        related_job_ids=related_job_ids,
        status=TroubleshootingStatus.MITIGATED,
        followup_required=not bool(prevention_rule.strip()),
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc).replace(microsecond=0)


__all__ = (
    "EnforcementJournal",
    "TroubleshootingViolation",
    "mandatory_capture",
    "record_claude_correction",
    "record_codex_correction",
    "record_silent_correction",
)
