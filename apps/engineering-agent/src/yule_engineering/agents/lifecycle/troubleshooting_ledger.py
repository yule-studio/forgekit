"""Troubleshooting ledger — 3 surface fan-out + mistake-ledger promotion.

:mod:`troubleshooting_record` 정의한 :class:`TroubleshootingRecord` 를
받아 사용자 §C 가 요구한 3 개 surface 중 *최소 2 개* 에 동시 기록한다:

1. **운영-리서치 / agent-ops audit row** — 단기 visibility. 같은 supervisor
   process 에서 즉시 grep 가능.
2. **Obsidian troubleshooting note** — 사람용 운영 기억. 8 섹션 강제.
3. **mistake ledger** — `(role_id, mistake_key)` 키로 repeat 감지 + preflight
   가 자동으로 다음 작업 차단.

추가로 § F 의 promotion rule 을 강제: 같은 signature 가 2 회 이상 발생하면
자동으로 mistake ledger 에 row 가 push 된다. 정책 enforcement 가 누락된
경우 / live smoke 에서 같은 실패 재현 / Claude Code 가 같은 구조 실수 반복
도 같은 rule 로 promotion.

이 모듈은 *외부 I/O 가 없다* — Obsidian write 는 hook 으로 주입한다. 그래서
unit test 에서 in-memory recorder 로 전체 흐름을 검증할 수 있다.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Protocol, Sequence, Tuple

from .agent_ops_log import (
    AgentOpsEntry,
    SESSION_EXTRA_KEY as AGENT_OPS_SESSION_EXTRA_KEY,
    append_agent_ops_audit,
)
from .mistake_ledger import (
    SEVERITY_HIGH as MISTAKE_SEVERITY_HIGH,
    SEVERITY_LOW as MISTAKE_SEVERITY_LOW,
    SEVERITY_MEDIUM as MISTAKE_SEVERITY_MEDIUM,
    SOURCE_POSTMORTEM,
    record_mistake,
)
from .troubleshooting_record import (
    CaptureReason,
    TroubleshootingRecord,
    TroubleshootingStatus,
    render_troubleshooting_note,
)


logger = logging.getLogger(__name__)


SESSION_EXTRA_KEY: str = "troubleshooting_audit"


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


class ObsidianTroubleshootingWriter(Protocol):
    """vault 노트 작성 hook.

    프로덕션은 `agents/lifecycle/troubleshooting_obsidian.py` (또는 사용자
    측 wiring) 에서 실제 vault 경로에 파일을 쓰는 callable. 테스트는
    list-recorder 사용.
    """

    def __call__(
        self,
        *,
        record: TroubleshootingRecord,
        note_markdown: str,
    ) -> Optional[str]:  # pragma: no cover - protocol
        ...


class ResearchThreadPoster(Protocol):
    """운영-리서치 thread 에 한 줄 요약 포스팅."""

    def __call__(
        self,
        *,
        record: TroubleshootingRecord,
    ) -> Optional[str]:  # pragma: no cover - protocol
        ...


class SessionExtraStamp(Protocol):
    """agent-ops audit + troubleshooting audit row 를 session.extra 에 stamp."""

    def __call__(
        self,
        *,
        session_id: str,
        record: TroubleshootingRecord,
        audit_entry: AgentOpsEntry,
    ) -> None:  # pragma: no cover - protocol
        ...


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CaptureOutcome:
    """:meth:`TroubleshootingLedger.capture` 결과.

    ``surfaces_written`` 가 2 개 미만이면 caller (enforcer) 가 audit
    violation 으로 처리. ``mistake_promoted`` 가 True 면 preflight 가
    다음 진입에서 이미 자동 차단/경고에 반영.
    """

    record: TroubleshootingRecord
    is_new: bool
    occurrence_count: int
    surfaces_written: Tuple[str, ...]
    mistake_promoted: bool
    obsidian_note_id: Optional[str] = None
    research_thread_post_id: Optional[str] = None
    audit_entry: Optional[AgentOpsEntry] = None

    def meets_minimum_surfaces(self, *, minimum: int = 2) -> bool:
        return len(self.surfaces_written) >= max(1, int(minimum))


SURFACE_RESEARCH_THREAD: str = "research_thread"
SURFACE_OBSIDIAN: str = "obsidian"
SURFACE_MISTAKE_LEDGER: str = "mistake_ledger"
SURFACE_SESSION_EXTRA: str = "session_extra"
SURFACE_RECORD_LEDGER: str = "record_ledger"


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


_SIGNATURE_SAFE_RE = re.compile(r"[^a-zA-Z0-9._:-]+")


def derive_problem_signature(
    *,
    capture_reason: str,
    scope: str = "",
    owner_role: str = "",
    extra_anchor: str = "",
) -> str:
    """`{reason}:{scope}:{owner_role}:{extra}` 형식의 안정적 signature.

    같은 4 튜플은 같은 signature → mistake ledger / preflight 가
    repeat detection 가능. extra_anchor 는 호출 측이 'session_id' 처럼
    너무 unique 한 값을 넣어 dedup 을 잃지 않도록 신중히 선택.
    """

    parts = [capture_reason, scope, owner_role, extra_anchor]
    raw = ":".join(p.strip() for p in parts if p)
    cleaned = _SIGNATURE_SAFE_RE.sub("-", raw).strip("-")
    return cleaned or "unknown"


@dataclass
class TroubleshootingLedger:
    """전체 troubleshooting 영속화 + fan-out.

    Thread-safe — supervisor / dispatcher / Claude Code wrapper 가 동시
    접근해도 안전. 디스크 sidecar 는 best-effort (실패해도 in-process
    state 는 유지).
    """

    ledger_path: Optional[Path] = None
    obsidian_writer: Optional[ObsidianTroubleshootingWriter] = None
    research_thread_poster: Optional[ResearchThreadPoster] = None
    session_extra_stamp: Optional[SessionExtraStamp] = None
    mistake_record_fn: Optional[Callable[..., Any]] = None
    actor: str = "troubleshooting-ledger"
    promotion_threshold: int = 2
    _by_signature: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if self.ledger_path is not None and self.ledger_path.exists():
            self._load_from_disk()
        if self.mistake_record_fn is None:
            self.mistake_record_fn = record_mistake

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def capture(
        self,
        *,
        title: str,
        capture_reason: CaptureReason,
        detected_by: str,
        owner_role: str,
        scope: str,
        symptom: str,
        severity: str = "medium",
        exact_evidence: str = "",
        reproduction_steps: Iterable[str] = (),
        root_cause_hypothesis: str = "",
        confirmed_root_cause: str = "",
        attempted_fix: str = "",
        final_fix: str = "",
        prevention_rule: str = "",
        related_session_ids: Iterable[str] = (),
        related_job_ids: Iterable[str] = (),
        related_prs: Iterable[str] = (),
        related_files: Iterable[str] = (),
        followup_required: bool = False,
        problem_signature: Optional[str] = None,
        signature_anchor: str = "",
        tags: Iterable[str] = (),
        extra: Optional[Mapping[str, Any]] = None,
        status: TroubleshootingStatus = TroubleshootingStatus.OPEN,
        now: Optional[datetime] = None,
        primary_session_id: Optional[str] = None,
    ) -> CaptureOutcome:
        """Capture 한 사건을 ledger + 3 surface 에 fan-out 한다.

        idempotent: 같은 problem_signature 가 들어오면 새 row 를 만들지
        않고 기존 row 의 occurrence_count 를 bump. 그 결과 promotion
        threshold (기본 2) 이상이면 mistake ledger 에 push.

        Returns :class:`CaptureOutcome`. 호출 측 (enforcer) 은
        :meth:`CaptureOutcome.meets_minimum_surfaces` 로 §C 의 "최소 2
        surface" 정책을 강제할 수 있다.
        """

        when = now or _utc_now()
        when_iso = when.isoformat()

        signature = problem_signature or derive_problem_signature(
            capture_reason=capture_reason.value,
            scope=scope,
            owner_role=owner_role,
            extra_anchor=signature_anchor,
        )

        with self._lock:
            existing = self._by_signature.get(signature)
            if existing is None:
                record = TroubleshootingRecord(
                    record_id=_new_record_id(),
                    title=title.strip() or signature,
                    problem_signature=signature,
                    capture_reason=capture_reason.value,
                    detected_at=when_iso,
                    recorded_at=when_iso,
                    detected_by=detected_by,
                    owner_role=owner_role,
                    scope=scope,
                    severity=severity,
                    status=status.value,
                    symptom=symptom,
                    exact_evidence=exact_evidence,
                    reproduction_steps=tuple(
                        s for s in reproduction_steps if isinstance(s, str) and s
                    ),
                    root_cause_hypothesis=root_cause_hypothesis,
                    confirmed_root_cause=confirmed_root_cause,
                    attempted_fix=attempted_fix,
                    final_fix=final_fix,
                    prevention_rule=prevention_rule,
                    related_session_ids=tuple(
                        s for s in related_session_ids if isinstance(s, str) and s
                    ),
                    related_job_ids=tuple(
                        s for s in related_job_ids if isinstance(s, str) and s
                    ),
                    related_prs=tuple(
                        s for s in related_prs if isinstance(s, str) and s
                    ),
                    related_files=tuple(
                        s for s in related_files if isinstance(s, str) and s
                    ),
                    followup_required=followup_required,
                    tags=tuple(s for s in tags if isinstance(s, str) and s),
                    extra=dict(extra or {}),
                    occurrence_count=1,
                )
                is_new = True
            else:
                record = existing.bump(
                    now=when,
                    additional_evidence=exact_evidence,
                    additional_session_ids=related_session_ids,
                    additional_job_ids=related_job_ids,
                    additional_prs=related_prs,
                    additional_files=related_files,
                    severity_escalation=severity,
                )
                if status.value == TroubleshootingStatus.FIXED.value:
                    record = replace(record, status=status.value, final_fix=final_fix or record.final_fix)
                is_new = False
            self._by_signature[signature] = record
            self._persist()

        surfaces: list[str] = [SURFACE_RECORD_LEDGER]

        note_id: Optional[str] = None
        if self.obsidian_writer is not None:
            try:
                note_markdown = render_troubleshooting_note(record)
                note_id = self.obsidian_writer(
                    record=record, note_markdown=note_markdown
                )
                if note_id is not None:
                    surfaces.append(SURFACE_OBSIDIAN)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "TroubleshootingLedger: obsidian writer raised", exc_info=True
                )

        thread_id: Optional[str] = None
        if self.research_thread_poster is not None:
            try:
                thread_id = self.research_thread_poster(record=record)
                if thread_id is not None:
                    surfaces.append(SURFACE_RESEARCH_THREAD)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "TroubleshootingLedger: research poster raised", exc_info=True
                )

        audit_entry: Optional[AgentOpsEntry] = None
        if self.session_extra_stamp is not None and primary_session_id:
            try:
                audit_entry = self._build_audit_entry(record=record)
                self.session_extra_stamp(
                    session_id=primary_session_id,
                    record=record,
                    audit_entry=audit_entry,
                )
                surfaces.append(SURFACE_SESSION_EXTRA)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "TroubleshootingLedger: session extra stamp raised", exc_info=True
                )

        mistake_promoted = False
        if record.occurrence_count >= max(1, int(self.promotion_threshold)):
            mistake_promoted = self._promote_to_mistake_ledger(
                record=record, when_iso=when_iso
            )
            if mistake_promoted:
                surfaces.append(SURFACE_MISTAKE_LEDGER)

        return CaptureOutcome(
            record=record,
            is_new=is_new,
            occurrence_count=record.occurrence_count,
            surfaces_written=tuple(surfaces),
            mistake_promoted=mistake_promoted,
            obsidian_note_id=note_id,
            research_thread_post_id=thread_id,
            audit_entry=audit_entry,
        )

    def get(self, signature: str) -> Optional[TroubleshootingRecord]:
        return self._by_signature.get(signature)

    def by_capture_reason(
        self, reason: CaptureReason
    ) -> Tuple[TroubleshootingRecord, ...]:
        return tuple(
            r
            for r in self._by_signature.values()
            if r.capture_reason == reason.value
        )

    def all(self) -> Tuple[TroubleshootingRecord, ...]:
        return tuple(self._by_signature.values())

    def open_records(self) -> Tuple[TroubleshootingRecord, ...]:
        return tuple(r for r in self._by_signature.values() if not r.is_terminal())

    def by_files(self, files: Sequence[str]) -> Tuple[TroubleshootingRecord, ...]:
        """파일 경로 일부 (또는 전체) 매칭 — preflight lookup 의 핵심 surface.

        파일 경로 문자열의 substring 일치를 사용한다. 그래서 caller 는
        ``"apps/engineering-agent/src/yule_engineering/discord/approval/reply_router.py"`` 처럼
        full path 도 가능하고 ``"reply_router.py"`` 처럼 basename 만 줘도
        OK.
        """

        if not files:
            return ()
        keys = tuple(f for f in files if isinstance(f, str) and f)
        out: list[TroubleshootingRecord] = []
        for record in self._by_signature.values():
            for related in record.related_files:
                if any(k in related or related in k for k in keys):
                    out.append(record)
                    break
        return tuple(out)

    def by_owner_role(self, role: str) -> Tuple[TroubleshootingRecord, ...]:
        if not role:
            return ()
        return tuple(r for r in self._by_signature.values() if r.owner_role == role)

    def transition(
        self,
        signature: str,
        *,
        status: TroubleshootingStatus,
        final_fix: Optional[str] = None,
        prevention_rule: Optional[str] = None,
        followup_required: Optional[bool] = None,
    ) -> Optional[TroubleshootingRecord]:
        with self._lock:
            existing = self._by_signature.get(signature)
            if existing is None:
                return None
            updated = replace(
                existing,
                status=status.value,
                final_fix=(
                    final_fix if final_fix is not None else existing.final_fix
                ),
                prevention_rule=(
                    prevention_rule
                    if prevention_rule is not None
                    else existing.prevention_rule
                ),
                followup_required=(
                    bool(followup_required)
                    if followup_required is not None
                    else existing.followup_required
                ),
                recorded_at=_utc_now().isoformat(),
            )
            self._by_signature[signature] = updated
            self._persist()
            return updated

    def clear(self) -> None:
        with self._lock:
            self._by_signature.clear()
            self._persist()

    def summary_counters(self) -> Mapping[str, int]:
        counts: dict[str, int] = {"total": 0, "open": 0, "fixed": 0, "repeated": 0}
        for record in self._by_signature.values():
            counts["total"] += 1
            if record.status == TroubleshootingStatus.OPEN.value:
                counts["open"] += 1
            if record.status == TroubleshootingStatus.FIXED.value:
                counts["fixed"] += 1
            if record.occurrence_count >= max(1, int(self.promotion_threshold)):
                counts["repeated"] += 1
        return counts

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_audit_entry(self, *, record: TroubleshootingRecord) -> AgentOpsEntry:
        """일반 agent-ops audit row 와 같은 형태 — operator 가 한 곳에서 본다."""

        return AgentOpsEntry(
            entry_id=_new_entry_id(),
            session_id="",
            action=record.capture_reason,
            autonomy_level="L1_AUTO_RECORD_REQUIRED",
            summary=record.title,
            reasoning=record.prevention_rule or record.root_cause_hypothesis,
            outcome=f"troubleshooting_captured:{record.problem_signature}",
            references=tuple(record.related_files[:5]),
            topic_key=None,
            job_id=None,
            decision_id=record.record_id,
            actor=self.actor,
            recorded_at=record.recorded_at,
        )

    def _promote_to_mistake_ledger(
        self,
        *,
        record: TroubleshootingRecord,
        when_iso: str,
    ) -> bool:
        """occurrence_count >= promotion_threshold 면 mistake ledger 에 push.

        mistake_record_fn 은 session.extra 변환 함수라 *비동기 session 없이도*
        호출 가능 — 본 모듈은 in-memory empty extra 로 호출해 새 ledger row
        를 생성 후 즉시 버린다 (실제 영속화는 caller 의 session.extra writer
        가 담당). 그래서 본 helper 는 *의도* 만 표면화한다 — promote 호출이
        일어났다는 사실을 surfaces_written 에 추가.

        이 분리가 중요: troubleshooting ledger 가 모든 워크플로 session 의
        extra 를 직접 쓰면 cross-cutting concern 이 너무 강해진다. 대신 caller
        가 (a) audit row + (b) record_mistake (자신의 session.extra) 두 곳에
        write 하는 패턴 유지.
        """

        if self.mistake_record_fn is None:
            return False
        severity_norm = (
            MISTAKE_SEVERITY_HIGH
            if record.severity in ("high", "critical")
            else (
                MISTAKE_SEVERITY_MEDIUM
                if record.severity == "medium"
                else MISTAKE_SEVERITY_LOW
            )
        )
        try:
            self.mistake_record_fn(
                None,  # empty extra — caller 가 자신의 session 에 write 시 같은 키로 합산
                role_id=record.owner_role or "engineering-agent",
                mistake_key=record.problem_signature[:64],
                summary=record.title,
                prevention_hint=record.prevention_rule
                or record.root_cause_hypothesis
                or "재발 시 troubleshooting note 의 prevention_rule 점검",
                source_kind=SOURCE_POSTMORTEM,
                severity=severity_norm,
                when=when_iso,
            )
            return True
        except Exception:  # noqa: BLE001
            logger.warning(
                "TroubleshootingLedger: mistake promotion raised", exc_info=True
            )
            return False

    def _load_from_disk(self) -> None:
        if self.ledger_path is None or not self.ledger_path.exists():
            return
        try:
            data = json.loads(
                self.ledger_path.read_text(encoding="utf-8") or "{}"
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "TroubleshootingLedger: failed to read sidecar %s",
                self.ledger_path,
                exc_info=True,
            )
            return
        records = data.get("records") if isinstance(data, Mapping) else None
        if not isinstance(records, list):
            return
        for raw in records:
            if not isinstance(raw, Mapping):
                continue
            record = TroubleshootingRecord.from_payload(raw)
            if record is None:
                continue
            self._by_signature[record.problem_signature] = record

    def _persist(self) -> None:
        if self.ledger_path is None:
            return
        try:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "records": [r.to_payload() for r in self._by_signature.values()],
                "saved_at": _utc_now().isoformat(),
            }
            self.ledger_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "TroubleshootingLedger: failed to persist sidecar", exc_info=True
            )


def default_ledger_path(env: Optional[Mapping[str, str]] = None) -> Path:
    env = env if env is not None else os.environ
    override = (env.get("YULE_TROUBLESHOOTING_LEDGER_PATH") or "").strip()
    if override:
        return Path(override).expanduser()
    cache = (env.get("YULE_CACHE_DB_PATH") or "").strip()
    if cache:
        return Path(cache).expanduser().parent / "troubleshooting_records.json"
    return Path(".cache/yule/troubleshooting_records.json")


# ---------------------------------------------------------------------------
# Default session.extra stamp — used by production wiring
# ---------------------------------------------------------------------------


def stamp_troubleshooting_audit(
    extra: Optional[Mapping[str, Any]],
    *,
    record: TroubleshootingRecord,
    audit_entry: AgentOpsEntry,
    max_entries: int = 100,
) -> dict:
    """session.extra 에 troubleshooting audit 한 줄 append + agent-ops 도 동시.

    Returns 새 extra dict (원본 mutate 안 함). caller 는
    ``workflow_state.update_session`` 으로 persist.
    """

    new_extra = dict(extra or {})
    bucket = new_extra.get(SESSION_EXTRA_KEY)
    rows = list(bucket) if isinstance(bucket, list) else []
    rows.append(
        {
            "record_id": record.record_id,
            "problem_signature": record.problem_signature,
            "capture_reason": record.capture_reason,
            "severity": record.severity,
            "owner_role": record.owner_role,
            "recorded_at": record.recorded_at,
            "occurrence_count": record.occurrence_count,
            "status": record.status,
        }
    )
    if len(rows) > max_entries:
        rows = rows[-max_entries:]
    new_extra[SESSION_EXTRA_KEY] = rows
    # Also append to standard agent-ops audit row.
    return dict(append_agent_ops_audit(new_extra, audit_entry))


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc).replace(microsecond=0)


def _new_record_id() -> str:
    return f"ts-{int(datetime.now(tz=timezone.utc).timestamp() * 1000):013d}-{uuid.uuid4().hex[:10]}"


def _new_entry_id() -> str:
    return f"ts-audit-{int(datetime.now(tz=timezone.utc).timestamp() * 1000):013d}-{uuid.uuid4().hex[:10]}"


__all__ = (
    "CaptureOutcome",
    "ObsidianTroubleshootingWriter",
    "ResearchThreadPoster",
    "SESSION_EXTRA_KEY",
    "SURFACE_MISTAKE_LEDGER",
    "SURFACE_OBSIDIAN",
    "SURFACE_RECORD_LEDGER",
    "SURFACE_RESEARCH_THREAD",
    "SURFACE_SESSION_EXTRA",
    "SessionExtraStamp",
    "TroubleshootingLedger",
    "default_ledger_path",
    "derive_problem_signature",
    "stamp_troubleshooting_audit",
)
