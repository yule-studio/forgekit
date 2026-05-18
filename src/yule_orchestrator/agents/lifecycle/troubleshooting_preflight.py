"""Troubleshooting preflight — 작업 시작 전 prior records 조회.

사용자 § G + § H 요구:
* 작업 시작 전에 관련 troubleshooting / mistake 가 있는지 자동 조회해
  preflight checklist 에 surface.
* operator 가 단순히 "실패했다" 가 아니라 "이건 이미 기록된 troubleshooting
  #N 이고 이전 prevention rule 은 X" 를 한 줄에 본다.

기존 :mod:`preflight_judgement` 가 mistake_ledger 기반 verdict (pass/advisory/
warning/block) 를 만든다. 본 모듈은 그 위에 troubleshooting record 의 더
풍부한 정보를 얹는 thin wrapper.

핵심 함수:
* :func:`lookup_relevant_records` — file_paths / signature / capture_reason
  / owner_role 중 하나라도 매치되는 record 들을 가져온다.
* :func:`build_preflight_briefing` — operator 가 즉시 읽는 한 줄 + 체크리스트
  형태 markdown.
* :func:`evaluate_combined_preflight` — preflight_judgement 의 verdict +
  troubleshooting record 정보를 합쳐 *최종 verdict + briefing* 한 객체로.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from .preflight_judgement import (
    PREFLIGHT_ADVISORY,
    PREFLIGHT_BLOCK,
    PREFLIGHT_PASS,
    PREFLIGHT_WARNING,
    PreflightAdvisory,
    PreflightThresholds,
    evaluate_preflight,
    render_preflight_advisory_block,
)
from .troubleshooting_ledger import TroubleshootingLedger
from .troubleshooting_record import (
    CaptureReason,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    TroubleshootingRecord,
    TroubleshootingStatus,
)


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def lookup_relevant_records(
    ledger: TroubleshootingLedger,
    *,
    file_paths: Sequence[str] = (),
    problem_signature: Optional[str] = None,
    capture_reason: Optional[CaptureReason] = None,
    owner_role: Optional[str] = None,
) -> Tuple[TroubleshootingRecord, ...]:
    """ledger 에서 *현재 작업 컨텍스트* 와 관련된 record 들을 찾는다.

    합집합 (OR) 로 매칭:
      - file_paths 가 record.related_files 와 substring 매치
      - problem_signature 가 정확히 같음
      - capture_reason 이 같음
      - owner_role 이 같음

    이미 fixed/superseded 인 record 도 포함한다 — operator 가 "이전에
    fix 가 어떻게 됐는지" 를 봐야 동일한 실수를 피할 수 있음. 단 status 별로
    분류해서 caller 가 시각적으로 구분 가능.
    """

    pool: list[TroubleshootingRecord] = []
    if problem_signature:
        target = ledger.get(problem_signature)
        if target is not None:
            pool.append(target)
    if file_paths:
        for r in ledger.by_files(tuple(file_paths)):
            if r not in pool:
                pool.append(r)
    if capture_reason is not None:
        for r in ledger.by_capture_reason(capture_reason):
            if r not in pool:
                pool.append(r)
    if owner_role:
        for r in ledger.by_owner_role(owner_role):
            if r not in pool:
                pool.append(r)
    return tuple(pool)


# ---------------------------------------------------------------------------
# Briefing renderer
# ---------------------------------------------------------------------------


_SEVERITY_ICON: Mapping[str, str] = {
    SEVERITY_CRITICAL: "🛑",
    SEVERITY_HIGH: "⚠️",
    SEVERITY_MEDIUM: "💡",
    SEVERITY_LOW: "·",
}


@dataclass(frozen=True)
class PreflightBriefing:
    """Preflight 조회 결과의 operator-friendly 묶음.

    ``verdict`` 는 기존 :class:`PreflightAdvisory` 의 verdict 와 동일 (pass/
    advisory/warning/block). ``troubleshooting_records`` 는 mistake ledger
    보다 풍부한 context 를 보여줄 prior record 들.

    ``markdown_block`` 은 한 번에 #봇-상태 / Discord / Obsidian 어디에든
    붙여넣을 수 있는 압축 텍스트.
    """

    verdict: str
    role_id: str
    action: str
    mistake_advisory: Optional[PreflightAdvisory]
    troubleshooting_records: Tuple[TroubleshootingRecord, ...]
    markdown_block: str

    def has_signal(self) -> bool:
        return self.verdict != PREFLIGHT_PASS or bool(self.troubleshooting_records)

    def is_block(self) -> bool:
        return self.verdict == PREFLIGHT_BLOCK


def build_preflight_briefing(
    *,
    role_id: str,
    action: str,
    mistake_advisory: Optional[PreflightAdvisory],
    troubleshooting_records: Sequence[TroubleshootingRecord],
) -> PreflightBriefing:
    """preflight verdict + record 목록 을 한 markdown block 으로."""

    verdict = (
        mistake_advisory.verdict
        if mistake_advisory is not None
        else PREFLIGHT_PASS
    )
    # troubleshooting 자체에 high/critical record 가 있고 occurrence_count 가 2 이상이면
    # verdict 를 한 단계 escalate 한다 — mistake ledger 가 아직 promotion 안 됐어도
    # operator 가 즉시 인지하도록.
    for record in troubleshooting_records:
        if record.is_terminal():
            continue
        if record.severity in (SEVERITY_HIGH, SEVERITY_CRITICAL) and record.occurrence_count >= 2:
            verdict = _escalate_verdict(verdict, target=PREFLIGHT_WARNING)
        if record.severity == SEVERITY_CRITICAL and record.occurrence_count >= 3:
            verdict = _escalate_verdict(verdict, target=PREFLIGHT_BLOCK)

    lines: list[str] = []
    headline = _format_headline(verdict=verdict, role_id=role_id, action=action, record_count=len(troubleshooting_records))
    if headline:
        lines.append(headline)
    if mistake_advisory is not None:
        block = render_preflight_advisory_block(mistake_advisory)
        if block:
            lines.append(block)
    if troubleshooting_records:
        lines.append("**prior troubleshooting records**")
        for record in troubleshooting_records:
            lines.append(_render_record_line(record))

    markdown = "\n".join(lines).strip()
    return PreflightBriefing(
        verdict=verdict,
        role_id=role_id,
        action=action,
        mistake_advisory=mistake_advisory,
        troubleshooting_records=tuple(troubleshooting_records),
        markdown_block=markdown,
    )


def evaluate_combined_preflight(
    *,
    source: Any,
    role_id: str,
    action: str,
    ledger: TroubleshootingLedger,
    file_paths: Sequence[str] = (),
    problem_signature: Optional[str] = None,
    capture_reason: Optional[CaptureReason] = None,
    thresholds: Optional[PreflightThresholds] = None,
) -> PreflightBriefing:
    """mistake-ledger preflight + troubleshooting lookup 을 한 번에.

    동일한 (role, action) 쌍에 대해 어떤 prior 정보가 있는지 운영자에게
    한 markdown block 으로 보여준다.
    """

    mistake_advisory = evaluate_preflight(
        source, role_id=role_id, action=action, thresholds=thresholds
    )
    relevant = lookup_relevant_records(
        ledger,
        file_paths=tuple(file_paths),
        problem_signature=problem_signature,
        capture_reason=capture_reason,
        owner_role=role_id or None,
    )
    return build_preflight_briefing(
        role_id=role_id,
        action=action,
        mistake_advisory=mistake_advisory,
        troubleshooting_records=relevant,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


_VERDICT_RANK: Mapping[str, int] = {
    PREFLIGHT_PASS: 0,
    PREFLIGHT_ADVISORY: 1,
    PREFLIGHT_WARNING: 2,
    PREFLIGHT_BLOCK: 3,
}


def _escalate_verdict(current: str, *, target: str) -> str:
    if _VERDICT_RANK.get(target, 0) > _VERDICT_RANK.get(current, 0):
        return target
    return current


def _format_headline(
    *,
    verdict: str,
    role_id: str,
    action: str,
    record_count: int,
) -> str:
    if verdict == PREFLIGHT_PASS and record_count == 0:
        return ""
    label = {
        PREFLIGHT_PASS: "참고",
        PREFLIGHT_ADVISORY: "advisory",
        PREFLIGHT_WARNING: "warning",
        PREFLIGHT_BLOCK: "block",
    }.get(verdict, verdict)
    role_part = f"`{role_id}`" if role_id else "(role 미지정)"
    action_part = f"`{action}`" if action else "(action 미지정)"
    return (
        f"🛟 troubleshooting preflight — {role_part} 가 {action_part} 진입 전, "
        f"prior records {record_count}건 / verdict={label}"
    )


def _render_record_line(record: TroubleshootingRecord) -> str:
    icon = _SEVERITY_ICON.get(record.severity, "·")
    return (
        f"  - {icon} `{record.problem_signature}` "
        f"({record.occurrence_count}회, status={record.status}) — "
        f"{record.title}"
        + (f"\n      · prevention: {record.prevention_rule}" if record.prevention_rule else "")
    )


__all__ = (
    "PreflightBriefing",
    "build_preflight_briefing",
    "evaluate_combined_preflight",
    "lookup_relevant_records",
)
