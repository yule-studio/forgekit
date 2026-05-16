"""Seed-backlog detectors for the self-improvement runtime loop.

기존 :mod:`agents.lifecycle.self_improvement` 의 detector 들은 queue /
heartbeat 의 *일반적* 회귀 (failed_retryable pileup, stale heartbeat,
duplicate topic 등) 를 잡는다. 그것만으로는 사용자가 보낸 known-failure
backlog (engineering_write reply mismatch / qa-test 오분류 / coding
continuation 정체 등) 가 surface 되지 않는다.

이 모듈은 그 *seed signals* 를 코드로 못박는다. 각 detector 는:

* :class:`SelfImprovementSignal` 형식의 객체를 반환 (or None)
* 같은 신호가 매 sweep 마다 다시 잡히면 *동일한 evidence* 가 stamp 되도록
  설계 — :class:`ProblemLedger` 가 dedup 할 수 있게.
* 외부 I/O 없음 — caller 가 ``jobs`` / ``sessions`` / ``heartbeats`` 를
  injectable 로 넘긴다.

새 신호 ID:

* :data:`SIGNAL_ENGINEERING_WRITE_REPLY_MISMATCH`
* :data:`SIGNAL_APPROVAL_NO_MATCHING_REPLY`
* :data:`SIGNAL_QA_TEST_MISCLASSIFICATION`
* :data:`SIGNAL_CODING_CONTINUATION_STALLED`
* :data:`SIGNAL_SUPERVISOR_WATCH_UNKNOWN`
* :data:`SIGNAL_OBSIDIAN_RENDER_FAILURE`
* :data:`SIGNAL_MEMBER_BOT_PRESENCE_CONFUSION`
* :data:`SIGNAL_ISSUELESS_BOOTSTRAP_FAILURE`
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from .self_improvement import (
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    SelfImprovementSignal,
)


# ---------------------------------------------------------------------------
# New signal IDs
# ---------------------------------------------------------------------------


SIGNAL_ENGINEERING_WRITE_REPLY_MISMATCH: str = "engineering_write_reply_mismatch"
SIGNAL_APPROVAL_NO_MATCHING_REPLY: str = "approval_no_matching_reply"
SIGNAL_QA_TEST_MISCLASSIFICATION: str = "qa_test_misclassification"
SIGNAL_CODING_CONTINUATION_STALLED: str = "coding_continuation_stalled"
SIGNAL_SUPERVISOR_WATCH_UNKNOWN: str = "supervisor_watch_unknown_surface"
SIGNAL_OBSIDIAN_RENDER_FAILURE: str = "obsidian_render_failure"
SIGNAL_MEMBER_BOT_PRESENCE_CONFUSION: str = "member_bot_presence_confusion"
SIGNAL_ISSUELESS_BOOTSTRAP_FAILURE: str = "issueless_bootstrap_failure"


# ---------------------------------------------------------------------------
# Observation context
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ObservationContext:
    """Snapshot of runtime state the seed detectors operate on.

    Filled in once per sweep by the runtime self-improvement loop.
    Detectors stay pure-data so unit tests can drive them with hand-
    crafted snapshots.
    """

    jobs: Sequence[Any] = ()
    failed_jobs: Sequence[Any] = ()
    sessions: Sequence[Any] = ()
    heartbeats: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    audit_log: Sequence[Mapping[str, Any]] = ()
    now: Optional[datetime] = None

    def now_or_utc(self) -> datetime:
        return self.now or datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_CODING_INTENT_KEYWORDS: Tuple[str, ...] = (
    "구현",
    "구현해",
    "코딩",
    "고쳐",
    "수정해",
    "PR 만들",
    "PR 열어",
    "기능 추가",
    "버그 fix",
    "버그 픽스",
    "implement",
    "build the",
    "add feature",
)


def _job_state(job: Any) -> str:
    state_obj = getattr(job, "state", None)
    return str(getattr(state_obj, "value", state_obj) or "")


def _job_payload(job: Any) -> Mapping[str, Any]:
    payload = getattr(job, "payload", None)
    return payload if isinstance(payload, Mapping) else {}


def _job_result(job: Any) -> Mapping[str, Any]:
    result = getattr(job, "result", None)
    return result if isinstance(result, Mapping) else {}


def _session_extra(session: Any) -> Mapping[str, Any]:
    extra = getattr(session, "extra", None)
    return extra if isinstance(extra, Mapping) else {}


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    text = value
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        ts = datetime.fromisoformat(text)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Detector: engineering_write approval card reply mismatch
# ---------------------------------------------------------------------------


def detect_engineering_write_reply_mismatch(
    *,
    jobs: Iterable[Any],
    threshold: int = 1,
) -> Optional[SelfImprovementSignal]:
    """Flag when an ``approval_post`` with
    ``approval_kind=engineering_write`` is in ``saved`` (posted) state
    but its session has the well-known *no matching approval* error
    marker (or repeated reply parses).

    The marker that the reply_router emits when no matching approval is
    found is the ``last_no_match_reason`` field on the approval job's
    result. Repeated occurrences mean the router has a binding bug.
    """

    matches: list[Any] = []
    for job in jobs or ():
        if getattr(job, "job_type", None) != "approval_post":
            continue
        payload = _job_payload(job)
        extra = payload.get("extra") if isinstance(payload.get("extra"), Mapping) else {}
        kind = str(
            payload.get("approval_kind")
            or (extra.get("approval_kind") if isinstance(extra, Mapping) else "")
            or ""
        )
        if kind != "engineering_write":
            continue
        result = _job_result(job)
        if result.get("no_matching_approval_count"):
            matches.append(job)
            continue
        if result.get("last_no_match_reason"):
            matches.append(job)

    if len(matches) < threshold:
        return None

    sample_ids = [str(getattr(j, "job_id", "?")) for j in matches[:5]]
    return SelfImprovementSignal(
        signal=SIGNAL_ENGINEERING_WRITE_REPLY_MISMATCH,
        severity=SEVERITY_HIGH,
        summary=(
            f"engineering_write approval card 의 reply 매칭이 {len(matches)}회 "
            "실패 — reply_router posted_message_id 매칭 회귀 의심"
        ),
        evidence={
            "count": len(matches),
            "sample_job_ids": sample_ids,
            "approval_kind": "engineering_write",
        },
        detected_at=_utc_now_iso(),
    )


# ---------------------------------------------------------------------------
# Detector: approval card posted but no matching reply found
# ---------------------------------------------------------------------------


def detect_approval_no_matching_reply(
    *,
    jobs: Iterable[Any],
    sessions: Iterable[Any] = (),
    now: Optional[datetime] = None,
    stale_after_seconds: int = 1800,
) -> Optional[SelfImprovementSignal]:
    """Approval posted (``saved``), ``posted_message_id`` stamped, but no
    reply observed for more than ``stale_after_seconds``.

    Surfaces the "no_matching_approval" symptom even when the
    reply_router isn't raising — the gateway might be silently dropping
    the message.
    """

    when = (now or datetime.now(tz=timezone.utc)).replace(microsecond=0)
    stale: list[Tuple[str, str, str]] = []  # (job_id, kind, posted_at)
    for job in jobs or ():
        if getattr(job, "job_type", None) != "approval_post":
            continue
        if _job_state(job) != "saved":
            continue
        result = _job_result(job)
        posted_message_id = result.get("posted_message_id")
        if not posted_message_id:
            continue
        if result.get("reply_resolved"):
            continue
        posted_at = _parse_iso(result.get("posted_at") or result.get("saved_at"))
        if posted_at is None:
            continue
        if (when - posted_at).total_seconds() < stale_after_seconds:
            continue
        payload = _job_payload(job)
        kind = str(payload.get("approval_kind") or "")
        stale.append(
            (
                str(getattr(job, "job_id", "?")),
                kind,
                posted_at.isoformat(),
            )
        )
    if not stale:
        return None
    return SelfImprovementSignal(
        signal=SIGNAL_APPROVAL_NO_MATCHING_REPLY,
        severity=SEVERITY_HIGH,
        summary=(
            f"posting 됐지만 매칭 reply 가 {stale_after_seconds}s 이상 미수신: "
            f"{len(stale)}건"
        ),
        evidence={
            "count": len(stale),
            "sample": [
                {"job_id": jid, "approval_kind": k, "posted_at": pat}
                for jid, k, pat in stale[:5]
            ],
            "stale_after_seconds": stale_after_seconds,
        },
        detected_at=_utc_now_iso(),
    )


# ---------------------------------------------------------------------------
# Detector: qa-test misclassification on issue-less full-stack request
# ---------------------------------------------------------------------------


def detect_qa_test_misclassification(
    *,
    sessions: Iterable[Any],
    threshold: int = 1,
) -> Optional[SelfImprovementSignal]:
    """Sessions whose dispatcher classification ended up as
    ``qa-test`` / ``unknown`` while the user's prompt clearly contains
    coding-intent keywords ("구현", "고쳐", "implement", etc.).

    Surfaces the stack_detector / classifier regression the user has
    flagged repeatedly.
    """

    bad: list[Tuple[str, str, str]] = []
    for session in sessions or ():
        extra = _session_extra(session)
        # 1순위: dispatcher 가 명시한 classification marker. 2순위: session.task_type
        # 이 fallback 이 있어야 *기존* approved 세션도 회귀 신호를 만들 수 있다.
        label = ""
        classifier = extra.get("dispatcher_classification")
        if isinstance(classifier, Mapping):
            label = str(classifier.get("label") or "").lower()
        if not label:
            label = str(getattr(session, "task_type", "") or "").lower()
        if label not in {"qa-test", "qa_test", "unknown"}:
            continue
        prompt = str(getattr(session, "prompt", "") or "")
        if not any(kw.lower() in prompt.lower() for kw in _CODING_INTENT_KEYWORDS):
            continue
        bad.append(
            (
                str(getattr(session, "session_id", "?")),
                label,
                prompt[:120],
            )
        )
    if len(bad) < threshold:
        return None
    return SelfImprovementSignal(
        signal=SIGNAL_QA_TEST_MISCLASSIFICATION,
        severity=SEVERITY_HIGH,
        summary=(
            f"코딩 intent prompt 가 qa-test/unknown 으로 분류된 세션 {len(bad)}건 — "
            "dispatcher.classify / stack_detector 회귀 의심"
        ),
        evidence={
            "count": len(bad),
            "samples": [
                {"session_id": sid, "label": label, "prompt": p}
                for sid, label, p in bad[:5]
            ],
        },
        detected_at=_utc_now_iso(),
    )


# ---------------------------------------------------------------------------
# Detector: approved but no coding continuation
# ---------------------------------------------------------------------------


def detect_coding_continuation_stalled(
    *,
    sessions: Iterable[Any],
    now: Optional[datetime] = None,
    stale_after_seconds: int = 1200,
) -> Optional[SelfImprovementSignal]:
    """Session has ``coding_proposal`` + ``approval_id`` stamped but no
    ``coding_execute_dispatch`` after the stale window. Indicates the
    approval→dispatch bridge (work_order_coding_continuation) failed.
    """

    when = (now or datetime.now(tz=timezone.utc)).replace(microsecond=0)
    stalled: list[Mapping[str, Any]] = []
    for session in sessions or ():
        extra = _session_extra(session)
        proposal = extra.get("coding_proposal")
        if not isinstance(proposal, Mapping) or not proposal:
            continue
        if extra.get("coding_execute_dispatch"):
            continue
        progress = extra.get("github_work_order_progress")
        approved_at = None
        if isinstance(progress, Mapping):
            entry = progress.get("coding_dispatch_queued") or progress.get(
                "issue_created"
            )
            if isinstance(entry, Mapping):
                approved_at = _parse_iso(entry.get("at"))
        if approved_at is None:
            approved_at = _parse_iso(extra.get("approved_at"))
        if approved_at is None:
            continue
        if (when - approved_at).total_seconds() < stale_after_seconds:
            continue
        stalled.append(
            {
                "session_id": str(getattr(session, "session_id", "?")),
                "approved_at": approved_at.isoformat(),
                "executor_role": proposal.get("executor_role"),
            }
        )
    if not stalled:
        return None
    return SelfImprovementSignal(
        signal=SIGNAL_CODING_CONTINUATION_STALLED,
        severity=SEVERITY_HIGH,
        summary=(
            f"approval 후 coding_execute 로 dispatch 되지 않은 세션 {len(stalled)}건 "
            f"(stale > {stale_after_seconds}s)"
        ),
        evidence={
            "count": len(stalled),
            "samples": stalled[:5],
            "stale_after_seconds": stale_after_seconds,
        },
        detected_at=_utc_now_iso(),
    )


# ---------------------------------------------------------------------------
# Detector: eng-supervisor-watch / eng-discord-gateway UNKNOWN surface
# ---------------------------------------------------------------------------


def detect_supervisor_watch_unknown_surface(
    *,
    heartbeats: Mapping[str, Mapping[str, Any]],
    now: Optional[datetime] = None,
    unknown_marker_seconds: int = 300,
) -> Optional[SelfImprovementSignal]:
    """eng-supervisor-watch / eng-discord-gateway heartbeat 가 살아있는데
    상태 필드가 UNKNOWN 으로 stuck 된 상황.

    The supervisor / gateway emit a ``last_status`` field on their
    heartbeat payload (M7+) — when it's ``UNKNOWN`` for longer than the
    threshold we surface the "온라인인데 뭐하는지 모름" symptom.
    """

    when = (now or datetime.now(tz=timezone.utc)).replace(microsecond=0)
    unknown_services: list[str] = []
    for service_id, payload in (heartbeats or {}).items():
        if not isinstance(payload, Mapping):
            continue
        if service_id not in {
            "eng-supervisor-watch",
            "eng-discord-gateway",
        }:
            continue
        status = str(payload.get("last_status") or "").upper()
        if status != "UNKNOWN":
            continue
        last_status_at = _parse_iso(payload.get("last_status_at")) or _parse_iso(
            payload.get("updated_at")
        )
        if last_status_at is None:
            continue
        if (when - last_status_at).total_seconds() < unknown_marker_seconds:
            continue
        unknown_services.append(service_id)
    if not unknown_services:
        return None
    return SelfImprovementSignal(
        signal=SIGNAL_SUPERVISOR_WATCH_UNKNOWN,
        severity=SEVERITY_MEDIUM,
        summary=(
            f"{', '.join(unknown_services)} surface 가 UNKNOWN 으로 "
            f"{unknown_marker_seconds}s 이상 유지됨 — 상태 보고 회귀 의심"
        ),
        evidence={
            "service_ids": unknown_services,
            "unknown_after_seconds": unknown_marker_seconds,
        },
        detected_at=_utc_now_iso(),
    )


# ---------------------------------------------------------------------------
# Detector: Obsidian knowledge render failure
# ---------------------------------------------------------------------------


_OBSIDIAN_RENDER_ERROR_TOKENS: Tuple[str, ...] = (
    "render",
    "frontmatter",
    "markdown render",
    "render_fn",
    "renderer",
    "vault render",
)


def detect_obsidian_render_failure(
    *,
    failed_jobs: Iterable[Any],
    threshold: int = 2,
) -> Optional[SelfImprovementSignal]:
    """``obsidian_write`` 잡이 render 단계에서 반복 실패."""

    matches: list[Tuple[str, str]] = []
    for job in failed_jobs or ():
        if getattr(job, "job_type", None) != "obsidian_write":
            continue
        result = _job_result(job)
        error = str(result.get("error") or "").lower()
        if not error:
            continue
        if not any(token in error for token in _OBSIDIAN_RENDER_ERROR_TOKENS):
            continue
        matches.append((str(getattr(job, "job_id", "?")), error[:160]))
    if len(matches) < threshold:
        return None
    return SelfImprovementSignal(
        signal=SIGNAL_OBSIDIAN_RENDER_FAILURE,
        severity=SEVERITY_MEDIUM,
        summary=(
            f"obsidian_write render 실패 반복 {len(matches)}회 — vault renderer 회귀 의심"
        ),
        evidence={
            "count": len(matches),
            "samples": [
                {"job_id": jid, "error": err} for jid, err in matches[:5]
            ],
        },
        detected_at=_utc_now_iso(),
    )


# ---------------------------------------------------------------------------
# Detector: member bot presence / closure confusion
# ---------------------------------------------------------------------------


def detect_member_bot_presence_confusion(
    *,
    heartbeats: Mapping[str, Mapping[str, Any]],
    sessions: Iterable[Any] = (),
    now: Optional[datetime] = None,
    idle_threshold_seconds: int = 1800,
) -> Optional[SelfImprovementSignal]:
    """Member bot heartbeat 가 살아있는데 ``session_status`` 상으로 최근
    작업이 없는 상태가 ``idle_threshold_seconds`` 이상 지속.

    "봇이 계속 온라인인데 뭐 하는지 모름" 증상의 코드 표현.
    """

    when = (now or datetime.now(tz=timezone.utc)).replace(microsecond=0)
    member_services = [
        sid
        for sid in (heartbeats or {}).keys()
        if isinstance(sid, str) and sid.startswith("eng-member-bot-")
    ]
    if not member_services:
        return None

    most_recent_activity = None
    for session in sessions or ():
        extra = _session_extra(session)
        last = _parse_iso(extra.get("last_activity_at"))
        if last is None:
            continue
        if most_recent_activity is None or last > most_recent_activity:
            most_recent_activity = last
    if most_recent_activity is not None and (
        when - most_recent_activity
    ).total_seconds() < idle_threshold_seconds:
        return None

    confused: list[str] = []
    for service_id in member_services:
        payload = heartbeats.get(service_id) if isinstance(heartbeats, Mapping) else None
        if not isinstance(payload, Mapping):
            continue
        beat = _parse_iso(payload.get("updated_at"))
        if beat is None:
            continue
        # Heartbeat 가 최근이면 (online) idle 신호로 분류
        if (when - beat).total_seconds() > idle_threshold_seconds:
            continue
        confused.append(service_id)
    if not confused:
        return None
    return SelfImprovementSignal(
        signal=SIGNAL_MEMBER_BOT_PRESENCE_CONFUSION,
        severity=SEVERITY_LOW,
        summary=(
            f"member bot {len(confused)}대 online 이지만 최근 "
            f"{idle_threshold_seconds}s 활동 없음 — closure 표시 검토"
        ),
        evidence={
            "service_ids": confused,
            "idle_threshold_seconds": idle_threshold_seconds,
        },
        detected_at=_utc_now_iso(),
    )


# ---------------------------------------------------------------------------
# Detector: issue-less bootstrap smoke failure
# ---------------------------------------------------------------------------


def detect_issueless_bootstrap_failure(
    *,
    failed_jobs: Iterable[Any],
    threshold: int = 1,
) -> Optional[SelfImprovementSignal]:
    """github_work_order 잡이 issue 없이 부트스트랩하다 실패한 패턴."""

    matches: list[str] = []
    for job in failed_jobs or ():
        if getattr(job, "job_type", None) not in {"github_work_order", "coding_execute"}:
            continue
        result = _job_result(job)
        error = str(result.get("error") or "").lower()
        if "issue" not in error and "bootstrap" not in error:
            continue
        matches.append(str(getattr(job, "job_id", "?")))
    if len(matches) < threshold:
        return None
    return SelfImprovementSignal(
        signal=SIGNAL_ISSUELESS_BOOTSTRAP_FAILURE,
        severity=SEVERITY_MEDIUM,
        summary=(
            f"issue-less bootstrap smoke / github_work_order 잡 실패 {len(matches)}회"
        ),
        evidence={
            "count": len(matches),
            "sample_job_ids": matches[:5],
        },
        detected_at=_utc_now_iso(),
    )


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


def collect_seed_signals(
    observation: ObservationContext,
) -> Tuple[SelfImprovementSignal, ...]:
    """Run every seed detector against *observation* and return the
    non-None signals, severity-descending.
    """

    detectors = [
        detect_engineering_write_reply_mismatch(jobs=observation.jobs),
        detect_approval_no_matching_reply(
            jobs=observation.jobs,
            sessions=observation.sessions,
            now=observation.now,
        ),
        detect_qa_test_misclassification(sessions=observation.sessions),
        detect_coding_continuation_stalled(
            sessions=observation.sessions, now=observation.now
        ),
        detect_supervisor_watch_unknown_surface(
            heartbeats=observation.heartbeats, now=observation.now
        ),
        detect_obsidian_render_failure(failed_jobs=observation.failed_jobs),
        detect_member_bot_presence_confusion(
            heartbeats=observation.heartbeats,
            sessions=observation.sessions,
            now=observation.now,
        ),
        detect_issueless_bootstrap_failure(failed_jobs=observation.failed_jobs),
    ]
    signals = [s for s in detectors if s is not None]
    severity_rank = {SEVERITY_HIGH: 0, SEVERITY_MEDIUM: 1, SEVERITY_LOW: 2}
    signals.sort(key=lambda s: (severity_rank.get(s.severity, 3), s.signal))
    return tuple(signals)


__all__ = (
    "ObservationContext",
    "SIGNAL_APPROVAL_NO_MATCHING_REPLY",
    "SIGNAL_CODING_CONTINUATION_STALLED",
    "SIGNAL_ENGINEERING_WRITE_REPLY_MISMATCH",
    "SIGNAL_ISSUELESS_BOOTSTRAP_FAILURE",
    "SIGNAL_MEMBER_BOT_PRESENCE_CONFUSION",
    "SIGNAL_OBSIDIAN_RENDER_FAILURE",
    "SIGNAL_QA_TEST_MISCLASSIFICATION",
    "SIGNAL_SUPERVISOR_WATCH_UNKNOWN",
    "collect_seed_signals",
    "detect_approval_no_matching_reply",
    "detect_coding_continuation_stalled",
    "detect_engineering_write_reply_mismatch",
    "detect_issueless_bootstrap_failure",
    "detect_member_bot_presence_confusion",
    "detect_obsidian_render_failure",
    "detect_qa_test_misclassification",
    "detect_supervisor_watch_unknown_surface",
)
