"""Structured troubleshooting record — mandatory operational memory.

**Troubleshooting 은 회고 문서가 아니라 운영 기억이다.** 실패 / 우회 / 재시도 /
잘못된 가정 / dead path / 회귀 / 라이브 스모크 막힘이 일어났는데 그 기록이
대화창에만 남고 시스템에는 안 남는 상태를 금지하기 위한 1급 스키마.

이 모듈은 *데이터 모델 + 렌더링* 만 책임진다. 영속화 / 표면 fan-out /
mistake ledger 자동 승격은 :mod:`troubleshooting_ledger` 가 맡고, 강제
capture 는 :mod:`troubleshooting_enforcer` 가 맡는다.

스키마 (사용자 §D 요구):

* ``title`` / ``problem_signature`` / ``detected_at`` / ``detected_by``
* ``owner_role`` / ``scope`` / ``severity``
* ``symptom`` / ``exact_evidence`` / ``reproduction_steps``
* ``root_cause_hypothesis`` / ``confirmed_root_cause``
* ``attempted_fix`` / ``final_fix`` / ``prevention_rule``
* ``related_session_ids`` / ``related_job_ids`` / ``related_prs`` /
  ``related_files``
* ``followup_required`` / ``status``

노트 품질 요구 (사용자 §E) 는 ``render_troubleshooting_note`` 가 강제 — 8
섹션 모두 헤더가 빠지지 않게 템플릿이 보장.

캡처 사유 (사용자 §A + §B + §I) 는 :class:`CaptureReason` 로 enum 화해서
preflight / mistake ledger / runtime status 가 같은 토큰으로 grep 가능하게
한다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Capture reasons (mandatory triggers)
# ---------------------------------------------------------------------------


class CaptureReason(str, Enum):
    """사용자 §A + §B + §I 항목을 코드화.

    runtime agent / Claude Code / Codex executor 모두 같은 vocabulary 를
    쓰도록 enum 으로 박는다. 새 trigger 가 필요하면 enum 에 추가 + 본 모듈
    docstring 의 §A 표에 1줄 추가.
    """

    # §A
    LIVE_SMOKE_FAILURE = "live_smoke_failure"
    QUEUE_STUCK = "queue_stuck"
    APPROVAL_REPLY_MISMATCH = "approval_reply_mismatch"
    NO_REPO = "no_repo"
    NO_WRITER = "no_writer"
    NO_PLAN = "no_plan"
    NO_CONTINUATION = "no_continuation"
    WRONG_CLASSIFICATION = "wrong_classification"
    DUPLICATE_INTAKE = "duplicate_intake"
    DUPLICATE_WORK_ORDER = "duplicate_work_order"
    DUPLICATE_REPLAY = "duplicate_replay"
    FAILED_RETRYABLE_NO_RECOVERY = "failed_retryable_no_recovery"
    RUNTIME_UNKNOWN_CONFUSION = "runtime_unknown_confusion"
    POLICY_EXISTS_NO_ENFORCEMENT = "policy_exists_no_enforcement"
    LARGE_FILE_VIOLATION = "large_file_violation"
    MIXED_RESPONSIBILITY_VIOLATION = "mixed_responsibility_violation"
    DEAD_CODE = "dead_code"
    PARTIAL_WIRING = "partial_wiring"
    STALE_COMPATIBILITY_SHIM = "stale_compatibility_shim"
    FALLBACK_TRIGGERED = "fallback_triggered"
    OPERATOR_MANUAL_INTERVENTION = "operator_manual_intervention"
    # §B
    CLAUDE_WRONG_ASSUMPTION = "claude_wrong_assumption"
    CLAUDE_INSUFFICIENT_FIX_FOLLOWUP = "claude_insufficient_fix_followup"
    CI_GREEN_BUT_LIVE_FAIL = "ci_green_but_live_fail"
    SLASH_CHANNEL_PATH_DIVERGENCE = "slash_channel_path_divergence"
    CODE_EXISTS_BUT_WIRING_MISSING = "code_exists_but_wiring_missing"
    KNOWN_RULE_VIOLATION = "known_rule_violation"
    # §I
    RETRYABLE_FAILURE_RETRY_SUCCESS = "retryable_failure_retry_success"
    FALLBACK_SUCCESS_AFTER_FAIL = "fallback_success_after_fail"
    LIVE_SMOKE_FAIL_SUBSEQUENT_FIX = "live_smoke_fail_subsequent_fix"


_CAPTURE_REASON_VALUES: frozenset[str] = frozenset(r.value for r in CaptureReason)


def is_capture_reason_known(value: Any) -> bool:
    return isinstance(value, str) and value in _CAPTURE_REASON_VALUES


# ---------------------------------------------------------------------------
# Status / severity / scope
# ---------------------------------------------------------------------------


class TroubleshootingStatus(str, Enum):
    OPEN = "open"
    MITIGATED = "mitigated"
    FIXED = "fixed"
    SUPERSEDED = "superseded"


_TERMINAL_STATUSES: frozenset[TroubleshootingStatus] = frozenset(
    {TroubleshootingStatus.FIXED, TroubleshootingStatus.SUPERSEDED}
)


SEVERITY_LOW: str = "low"
SEVERITY_MEDIUM: str = "medium"
SEVERITY_HIGH: str = "high"
SEVERITY_CRITICAL: str = "critical"


_SEVERITIES: frozenset[str] = frozenset(
    {SEVERITY_LOW, SEVERITY_MEDIUM, SEVERITY_HIGH, SEVERITY_CRITICAL}
)


# Detected-by vocabulary — supervisor, gateway, role, claude_code, codex.
# Free-form is allowed but these tokens drive heuristic owner lookup +
# repeat-mistake correlation between runtime and LLM coding paths.
DETECTED_BY_RUNTIME_GATEWAY: str = "runtime/gateway"
DETECTED_BY_RUNTIME_SUPERVISOR: str = "runtime/supervisor"
DETECTED_BY_RUNTIME_ROLE: str = "runtime/role"
DETECTED_BY_RUNTIME_WORKER: str = "runtime/worker"
DETECTED_BY_SELF_IMPROVEMENT: str = "runtime/self-improvement"
DETECTED_BY_CLAUDE_CODE: str = "tooling/claude-code"
DETECTED_BY_CODEX: str = "tooling/codex"


# ---------------------------------------------------------------------------
# Record dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TroubleshootingRecord:
    """One troubleshooting / postmortem entry.

    Frozen so the ledger relies on "the only way to mutate is to re-insert".
    All 20 user-required fields plus a ``capture_reason`` enum value and
    a ``recorded_at`` timestamp.
    """

    # Identity + lifecycle
    record_id: str
    title: str
    problem_signature: str
    capture_reason: str  # CaptureReason.value
    detected_at: str
    recorded_at: str
    detected_by: str
    owner_role: str
    scope: str
    severity: str
    status: str  # TroubleshootingStatus.value

    # Investigation
    symptom: str = ""
    exact_evidence: str = ""
    reproduction_steps: Tuple[str, ...] = ()
    root_cause_hypothesis: str = ""
    confirmed_root_cause: str = ""

    # Resolution
    attempted_fix: str = ""
    final_fix: str = ""
    prevention_rule: str = ""

    # Cross-links
    related_session_ids: Tuple[str, ...] = ()
    related_job_ids: Tuple[str, ...] = ()
    related_prs: Tuple[str, ...] = ()
    related_files: Tuple[str, ...] = ()

    # Followup
    followup_required: bool = False

    # Extra (operator memo / structured tags)
    tags: Tuple[str, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict)

    occurrence_count: int = 1

    def is_terminal(self) -> bool:
        try:
            return TroubleshootingStatus(self.status) in _TERMINAL_STATUSES
        except ValueError:
            return False

    def to_payload(self) -> Mapping[str, Any]:
        payload = asdict(self)
        payload["reproduction_steps"] = list(self.reproduction_steps)
        payload["related_session_ids"] = list(self.related_session_ids)
        payload["related_job_ids"] = list(self.related_job_ids)
        payload["related_prs"] = list(self.related_prs)
        payload["related_files"] = list(self.related_files)
        payload["tags"] = list(self.tags)
        payload["extra"] = dict(self.extra)
        return payload

    @classmethod
    def from_payload(cls, data: Mapping[str, Any]) -> Optional["TroubleshootingRecord"]:
        if not isinstance(data, Mapping):
            return None
        record_id = str(data.get("record_id") or "").strip()
        problem_signature = str(data.get("problem_signature") or "").strip()
        if not record_id or not problem_signature:
            return None
        return cls(
            record_id=record_id,
            title=str(data.get("title") or ""),
            problem_signature=problem_signature,
            capture_reason=_normalise_capture_reason(data.get("capture_reason")),
            detected_at=str(data.get("detected_at") or ""),
            recorded_at=str(data.get("recorded_at") or ""),
            detected_by=str(data.get("detected_by") or ""),
            owner_role=str(data.get("owner_role") or ""),
            scope=str(data.get("scope") or ""),
            severity=_normalise_severity(data.get("severity")),
            status=_normalise_status(data.get("status")),
            symptom=str(data.get("symptom") or ""),
            exact_evidence=str(data.get("exact_evidence") or ""),
            reproduction_steps=_as_str_tuple(data.get("reproduction_steps")),
            root_cause_hypothesis=str(data.get("root_cause_hypothesis") or ""),
            confirmed_root_cause=str(data.get("confirmed_root_cause") or ""),
            attempted_fix=str(data.get("attempted_fix") or ""),
            final_fix=str(data.get("final_fix") or ""),
            prevention_rule=str(data.get("prevention_rule") or ""),
            related_session_ids=_as_str_tuple(data.get("related_session_ids")),
            related_job_ids=_as_str_tuple(data.get("related_job_ids")),
            related_prs=_as_str_tuple(data.get("related_prs")),
            related_files=_as_str_tuple(data.get("related_files")),
            followup_required=bool(data.get("followup_required") or False),
            tags=_as_str_tuple(data.get("tags")),
            extra=dict(data.get("extra") or {}),
            occurrence_count=int(data.get("occurrence_count") or 1),
        )

    def bump(
        self,
        *,
        now: Optional[datetime] = None,
        additional_evidence: Optional[str] = None,
        additional_session_ids: Iterable[str] = (),
        additional_job_ids: Iterable[str] = (),
        additional_prs: Iterable[str] = (),
        additional_files: Iterable[str] = (),
        severity_escalation: Optional[str] = None,
    ) -> "TroubleshootingRecord":
        """Return a new record reflecting another occurrence.

        Severity can only escalate; the ledger never silently downgrades.
        Evidence is concatenated (newline-separated) so the operator can
        see the trajectory.
        """

        when = (now or _utc_now()).replace(microsecond=0).isoformat()
        next_severity = self.severity
        if severity_escalation:
            new_sev = _normalise_severity(severity_escalation)
            if _SEVERITY_ORDER[new_sev] > _SEVERITY_ORDER[self.severity]:
                next_severity = new_sev
        new_evidence = self.exact_evidence
        if additional_evidence:
            if new_evidence:
                new_evidence = new_evidence + "\n---\n" + additional_evidence
            else:
                new_evidence = additional_evidence
        return replace(
            self,
            recorded_at=when,
            severity=next_severity,
            exact_evidence=new_evidence,
            related_session_ids=_merge_tuple(
                self.related_session_ids, additional_session_ids
            ),
            related_job_ids=_merge_tuple(self.related_job_ids, additional_job_ids),
            related_prs=_merge_tuple(self.related_prs, additional_prs),
            related_files=_merge_tuple(self.related_files, additional_files),
            occurrence_count=self.occurrence_count + 1,
        )


_SEVERITY_ORDER: Mapping[str, int] = {
    SEVERITY_LOW: 0,
    SEVERITY_MEDIUM: 1,
    SEVERITY_HIGH: 2,
    SEVERITY_CRITICAL: 3,
}


def _normalise_severity(value: Any) -> str:
    raw = (str(value or "").strip() or SEVERITY_MEDIUM).lower()
    if raw not in _SEVERITIES:
        return SEVERITY_MEDIUM
    return raw


def _normalise_status(value: Any) -> str:
    raw = (str(value or "").strip() or TroubleshootingStatus.OPEN.value).lower()
    try:
        return TroubleshootingStatus(raw).value
    except ValueError:
        return TroubleshootingStatus.OPEN.value


def _normalise_capture_reason(value: Any) -> str:
    raw = (str(value or "").strip() or CaptureReason.OPERATOR_MANUAL_INTERVENTION.value).lower()
    if raw not in _CAPTURE_REASON_VALUES:
        return CaptureReason.OPERATOR_MANUAL_INTERVENTION.value
    return raw


def _as_str_tuple(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    if not isinstance(value, Iterable):
        return ()
    return tuple(str(x) for x in value if isinstance(x, str) and x)


def _merge_tuple(
    existing: Tuple[str, ...], new: Iterable[str]
) -> Tuple[str, ...]:
    seen: list[str] = list(existing)
    for item in new or ():
        text = str(item or "").strip()
        if text and text not in seen:
            seen.append(text)
    return tuple(seen)


# ---------------------------------------------------------------------------
# Markdown rendering — enforces §E note-quality sections
# ---------------------------------------------------------------------------


_REQUIRED_SECTIONS: Tuple[str, ...] = (
    "증상",
    "재현 절차",
    "관찰 증거",
    "원인 분석",
    "수정 내용",
    "재발 방지",
    "관련 세션 / PR / 파일 / 큐 row",
    "남은 리스크",
)


def render_troubleshooting_note(record: TroubleshootingRecord) -> str:
    """8 섹션이 빠짐없이 들어간 Obsidian-friendly markdown.

    빈 섹션도 그대로 렌더 — operator 가 "왜 이 섹션이 비어있지?" 를 한
    번에 보고 follow-up 할 수 있게. §E 의 "반드시 아래 섹션이 있어야 한다"
    를 코드 측에서 강제.
    """

    front_matter = _render_frontmatter(record)
    lines: list[str] = [front_matter, "", f"# {record.title or record.problem_signature}", ""]
    lines.append(_render_meta_block(record))
    lines.append("")
    for section_name in _REQUIRED_SECTIONS:
        lines.append(f"## {section_name}")
        lines.append("")
        body = _render_section_body(record, section_name)
        lines.append(body if body.strip() else "_기록되지 않음 — operator follow-up 필요._")
        lines.append("")
    if record.tags:
        lines.append("## 태그")
        lines.append("")
        lines.append(", ".join(f"#{t}" for t in record.tags))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_frontmatter(record: TroubleshootingRecord) -> str:
    fm = [
        "---",
        f"title: troubleshooting · {record.title or record.problem_signature}",
        "kind: troubleshooting",
        f"status: {record.status}",
        f"severity: {record.severity}",
        f"capture_reason: {record.capture_reason}",
        f"problem_signature: {record.problem_signature}",
        f"owner_role: {record.owner_role}",
        f"scope: {record.scope}",
        f"detected_by: {record.detected_by}",
        f"detected_at: {record.detected_at}",
        f"recorded_at: {record.recorded_at}",
        f"occurrence_count: {record.occurrence_count}",
        f"followup_required: {str(record.followup_required).lower()}",
    ]
    if record.tags:
        fm.append("tags:")
        for tag in record.tags:
            fm.append(f"  - {tag}")
    fm.append("---")
    return "\n".join(fm)


def _render_meta_block(record: TroubleshootingRecord) -> str:
    return (
        f"| 항목 | 값 |\n"
        f"| --- | --- |\n"
        f"| record_id | `{record.record_id}` |\n"
        f"| severity | {record.severity} |\n"
        f"| occurrence | {record.occurrence_count} |\n"
        f"| owner_role | {record.owner_role} |\n"
        f"| detected_by | {record.detected_by} |\n"
    )


def _render_section_body(record: TroubleshootingRecord, section: str) -> str:
    if section == "증상":
        return record.symptom
    if section == "재현 절차":
        if not record.reproduction_steps:
            return ""
        return "\n".join(f"{idx + 1}. {step}" for idx, step in enumerate(record.reproduction_steps))
    if section == "관찰 증거":
        return record.exact_evidence
    if section == "원인 분석":
        parts: list[str] = []
        if record.root_cause_hypothesis:
            parts.append(f"**가설:** {record.root_cause_hypothesis}")
        if record.confirmed_root_cause:
            parts.append(f"**확인된 원인:** {record.confirmed_root_cause}")
        return "\n\n".join(parts)
    if section == "수정 내용":
        parts = []
        if record.attempted_fix:
            parts.append(f"**시도한 수정:** {record.attempted_fix}")
        if record.final_fix:
            parts.append(f"**최종 수정:** {record.final_fix}")
        return "\n\n".join(parts)
    if section == "재발 방지":
        return record.prevention_rule
    if section == "관련 세션 / PR / 파일 / 큐 row":
        parts = []
        if record.related_session_ids:
            parts.append(
                "**세션:** " + ", ".join(f"`{s}`" for s in record.related_session_ids)
            )
        if record.related_job_ids:
            parts.append(
                "**큐 row:** " + ", ".join(f"`{j}`" for j in record.related_job_ids)
            )
        if record.related_prs:
            parts.append("**PR:** " + ", ".join(record.related_prs))
        if record.related_files:
            parts.append(
                "**파일:** "
                + ", ".join(f"`{f}`" for f in record.related_files)
            )
        return "\n\n".join(parts)
    if section == "남은 리스크":
        if record.followup_required:
            return "operator follow-up 필요 — 위 prevention_rule 의 enforcement 가 아직 자동화되지 않음."
        return "현재 알려진 추가 리스크 없음. (재발 시 본 record 의 occurrence_count 가 자동 증가.)"
    return ""


def required_sections() -> Tuple[str, ...]:
    """Public for tests + docs: 노트 품질 8 섹션 목록."""

    return _REQUIRED_SECTIONS


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc).replace(microsecond=0)


__all__ = (
    "CaptureReason",
    "DETECTED_BY_CLAUDE_CODE",
    "DETECTED_BY_CODEX",
    "DETECTED_BY_RUNTIME_GATEWAY",
    "DETECTED_BY_RUNTIME_ROLE",
    "DETECTED_BY_RUNTIME_SUPERVISOR",
    "DETECTED_BY_RUNTIME_WORKER",
    "DETECTED_BY_SELF_IMPROVEMENT",
    "SEVERITY_CRITICAL",
    "SEVERITY_HIGH",
    "SEVERITY_LOW",
    "SEVERITY_MEDIUM",
    "TroubleshootingRecord",
    "TroubleshootingStatus",
    "is_capture_reason_known",
    "render_troubleshooting_note",
    "required_sections",
)
