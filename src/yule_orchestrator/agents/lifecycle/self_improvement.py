"""Self-improvement signal detection — A-M10c skeleton.

Walk the running queue + workflow state and surface the signals
that should drive autonomous improvement work:

  * ``failed_retryable`` jobs accumulating without retry → operator
    attention or auto-retry candidate.
  * empty knowledge / research-log files generated → hydration
    regression.
  * duplicate approval cards on the same topic → topic-ledger
    regression.
  * stale supervisor heartbeats → runtime supervisor failure.

Each signal becomes a :class:`SelfImprovementSignal` that the M10c
follow-up wiring (in a later commit) will turn into a
``failure-postmortem`` or ``self-improvement-proposal`` Obsidian
note via :func:`agents.lifecycle.autonomous_producers.build_simple_body_request`.

This module is *detection only* — it does not enqueue notes, run a
runner, or call any LLM. That keeps it import-light and deterministic
for tests; the producer / runner side is wired separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple


SIGNAL_FAILED_RETRYABLE_PILEUP: str = "failed_retryable_pileup"
SIGNAL_DUPLICATE_TOPIC_APPROVAL: str = "duplicate_topic_approval"
SIGNAL_EMPTY_KNOWLEDGE_NOTE: str = "empty_knowledge_note"
SIGNAL_STALE_HEARTBEAT: str = "stale_heartbeat"
SIGNAL_REPEATED_USER_COMPLAINT: str = "repeated_user_complaint"


SEVERITY_LOW: str = "low"
SEVERITY_MEDIUM: str = "medium"
SEVERITY_HIGH: str = "high"


@dataclass(frozen=True)
class SelfImprovementSignal:
    """One detected anomaly worth investigating.

    ``signal`` is the canonical id (one of ``SIGNAL_*``).
    ``severity`` is a coarse ranking the proposal author can use to
    prioritise. ``evidence`` carries a small, JSON-friendly payload
    so the postmortem renderer can quote specific job ids / counts /
    titles.
    """

    signal: str
    severity: str
    summary: str
    evidence: Mapping[str, Any] = field(default_factory=dict)
    detected_at: str = ""

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "signal": self.signal,
            "severity": self.severity,
            "summary": self.summary,
            "evidence": dict(self.evidence),
            "detected_at": self.detected_at,
        }


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------


def detect_failed_retryable_pileup(
    *,
    jobs: Iterable[Any],
    threshold: int = 3,
) -> Optional[SelfImprovementSignal]:
    """Flag if more than *threshold* failed_retryable jobs sit in
    the queue. *jobs* is an iterable of :class:`Job` rows or any
    object with ``state`` and ``job_type`` / ``job_id`` attributes.
    """

    failing: list[Any] = []
    for job in jobs or ():
        state_value = getattr(getattr(job, "state", None), "value", "")
        if state_value == "failed_retryable":
            failing.append(job)
    if len(failing) <= threshold:
        return None
    sample_ids = [str(getattr(j, "job_id", "?")) for j in failing[:5]]
    job_types = sorted(
        {str(getattr(j, "job_type", "?") or "?") for j in failing}
    )
    severity = SEVERITY_HIGH if len(failing) > threshold * 2 else SEVERITY_MEDIUM
    return SelfImprovementSignal(
        signal=SIGNAL_FAILED_RETRYABLE_PILEUP,
        severity=severity,
        summary=(
            f"failed_retryable 잡 {len(failing)}건이 누적됨 — "
            f"job_type={job_types}"
        ),
        evidence={
            "count": len(failing),
            "job_types": job_types,
            "sample_job_ids": sample_ids,
        },
        detected_at=_utc_now_iso(),
    )


def detect_duplicate_topic_approval(
    *,
    jobs: Iterable[Any],
) -> Optional[SelfImprovementSignal]:
    """Flag if multiple approval_post rows for the same topic_key
    sit in the queue at non-terminal state. The M7.6 topic ledger
    should prevent this; if it slips through, the hydration /
    persistence layer is regressing.
    """

    by_topic: dict[str, list[Any]] = {}
    for job in jobs or ():
        if getattr(job, "job_type", None) != "approval_post":
            continue
        state_value = getattr(getattr(job, "state", None), "value", "")
        if state_value in {"failed_terminal", "failed_retryable"}:
            continue
        payload = getattr(job, "payload", None) or {}
        topic_key = ""
        extra = payload.get("extra")
        if isinstance(extra, Mapping):
            topic_key = str(extra.get("topic_key") or "")
        if not topic_key:
            metadata = payload.get("metadata")
            if isinstance(metadata, Mapping):
                topic_key = str(metadata.get("topic_key") or "")
        if not topic_key:
            continue
        by_topic.setdefault(topic_key, []).append(job)

    duplicates = {k: v for k, v in by_topic.items() if len(v) > 1}
    if not duplicates:
        return None
    return SelfImprovementSignal(
        signal=SIGNAL_DUPLICATE_TOPIC_APPROVAL,
        severity=SEVERITY_HIGH,
        summary=(
            f"동일 topic_key 의 approval_post 가 {len(duplicates)}건 중복 — "
            "M7.6 topic-ledger dedup 회귀 의심"
        ),
        evidence={
            "topics": {
                key: [str(getattr(j, "job_id", "?")) for j in rows]
                for key, rows in duplicates.items()
            },
        },
        detected_at=_utc_now_iso(),
    )


def detect_stale_heartbeat(
    *,
    heartbeats: Mapping[str, Any],
    now: Optional[datetime] = None,
    stale_after_seconds: int = 600,
) -> Optional[SelfImprovementSignal]:
    """Flag if any expected service hasn't beat in a long time.

    *heartbeats* is a mapping of ``service_id`` →
    ``{"updated_at": ISO-8601 string, ...}``. Callers usually pull
    this from :class:`HeartbeatStore` ``snapshot`` output.
    """

    if not heartbeats:
        return None
    when = (now or datetime.now(tz=timezone.utc)).replace(microsecond=0)
    stale: list[str] = []
    for service_id, payload in heartbeats.items():
        if not isinstance(payload, Mapping):
            continue
        updated_at = payload.get("updated_at")
        if not isinstance(updated_at, str):
            continue
        try:
            ts = datetime.fromisoformat(updated_at)
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if (when - ts).total_seconds() >= stale_after_seconds:
            stale.append(service_id)
    if not stale:
        return None
    return SelfImprovementSignal(
        signal=SIGNAL_STALE_HEARTBEAT,
        severity=SEVERITY_HIGH,
        summary=(
            f"{len(stale)} 개 서비스의 heartbeat 가 "
            f"{stale_after_seconds}s 이상 정체 — supervisor 점검 필요"
        ),
        evidence={"stale_service_ids": sorted(stale)},
        detected_at=_utc_now_iso(),
    )


def detect_empty_knowledge_note_attempts(
    *,
    failed_jobs: Iterable[Any],
    keyword: str = "hydration 부족",
) -> Optional[SelfImprovementSignal]:
    """Flag if multiple obsidian_write jobs are landing in
    failed_retryable with the empty-note guard's signature error
    string. Indicates the hydration pipeline (snapshot / pack /
    synthesis) is dropping content before reaching the writer.
    """

    matches: list[Any] = []
    for job in failed_jobs or ():
        if getattr(job, "job_type", None) != "obsidian_write":
            continue
        result = getattr(job, "result", None) or {}
        error = str((result or {}).get("error") or "") if isinstance(result, Mapping) else ""
        if keyword in error:
            matches.append(job)
    if len(matches) < 2:
        return None
    return SelfImprovementSignal(
        signal=SIGNAL_EMPTY_KNOWLEDGE_NOTE,
        severity=SEVERITY_MEDIUM,
        summary=(
            f"빈 knowledge/research-log 노트 작성 시도가 {len(matches)}회 실패 — "
            "hydration 파이프라인 점검 필요"
        ),
        evidence={
            "count": len(matches),
            "sample_job_ids": [
                str(getattr(j, "job_id", "?")) for j in matches[:5]
            ],
        },
        detected_at=_utc_now_iso(),
    )


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


def collect_self_improvement_signals(
    *,
    jobs: Iterable[Any] = (),
    failed_jobs: Iterable[Any] = (),
    heartbeats: Optional[Mapping[str, Any]] = None,
    failed_retryable_threshold: int = 3,
    stale_after_seconds: int = 600,
    now: Optional[datetime] = None,
) -> Tuple[SelfImprovementSignal, ...]:
    """Run every detector against the observed state and return
    the non-None signals. Order: severity descending, then signal
    id alphabetical.
    """

    materialized_jobs = list(jobs)
    materialized_failed = list(failed_jobs) or [
        j
        for j in materialized_jobs
        if getattr(getattr(j, "state", None), "value", "")
        == "failed_retryable"
    ]
    detectors = [
        detect_failed_retryable_pileup(
            jobs=materialized_jobs, threshold=failed_retryable_threshold
        ),
        detect_duplicate_topic_approval(jobs=materialized_jobs),
        detect_empty_knowledge_note_attempts(failed_jobs=materialized_failed),
    ]
    if heartbeats is not None:
        detectors.append(
            detect_stale_heartbeat(
                heartbeats=heartbeats,
                now=now,
                stale_after_seconds=stale_after_seconds,
            )
        )
    signals = [s for s in detectors if s is not None]
    severity_rank = {SEVERITY_HIGH: 0, SEVERITY_MEDIUM: 1, SEVERITY_LOW: 2}
    signals.sort(key=lambda s: (severity_rank.get(s.severity, 3), s.signal))
    return tuple(signals)


# ---------------------------------------------------------------------------
# Markdown rendering — used by the M10c follow-up to compose the
# proposal body that ``build_simple_body_request`` consumes.
# ---------------------------------------------------------------------------


def render_signals_as_proposal_body(
    signals: Sequence[SelfImprovementSignal],
    *,
    title: str = "self-improvement proposal",
) -> str:
    """Return a markdown body summarising *signals* — caller passes
    it via ``metadata['body']`` to
    :func:`build_simple_body_request` for vault save.
    """

    lines: list[str] = [f"# {title}", ""]
    if not signals:
        lines.append("_감지된 신호 없음_")
        lines.append("")
        return "\n".join(lines)
    for sig in signals:
        lines.append(f"## [{sig.severity.upper()}] {sig.signal}")
        lines.append("")
        lines.append(sig.summary)
        lines.append("")
        if sig.evidence:
            lines.append("**evidence:**")
            for key, value in sorted(sig.evidence.items()):
                lines.append(f"- `{key}`: {value}")
            lines.append("")
        lines.append("**제안 조치:**")
        lines.append(_default_remediation_for(sig))
        lines.append("")
    lines.append("## 자동 기록 안내")
    lines.append("")
    lines.append(
        "이 문서는 self-improvement 신호 감지에 따라 자동 작성된 제안서입니다. "
        "위험 등급에 따라 일부는 자동 처리되며, 나머지는 `#승인-대기` 카드로 "
        "사용자 검토를 요청합니다."
    )
    lines.append("")
    return "\n".join(lines)


_REMEDIATION_BY_SIGNAL: Mapping[str, str] = {
    SIGNAL_FAILED_RETRYABLE_PILEUP: (
        "잡별 실패 사유를 그룹핑한 뒤 (a) 무관한 일시 오류는 자동 requeue, "
        "(b) hydration 누락 등 코드 회귀는 fix branch 작성, (c) 외부 의존 실패는 "
        "circuit-break 정책에 위임."
    ),
    SIGNAL_DUPLICATE_TOPIC_APPROVAL: (
        "topic-ledger 의 dedup 분기를 재검증하고, 누락된 분기에 대한 "
        "회귀 테스트 추가. 임시로 중복 카드는 사용자 안내와 함께 한 건만 활성화."
    ),
    SIGNAL_STALE_HEARTBEAT: (
        "supervisor 모듈의 watch 루프와 service registry 를 점검. "
        "재시작이 필요한 경우 L3 승인 카드를 통해 사용자에게 보고."
    ),
    SIGNAL_EMPTY_KNOWLEDGE_NOTE: (
        "snapshot / synthesis / pack hydration 경로를 추적해 어느 단계에서 "
        "내용이 비는지 확인. 운영-리서치 thread fetcher 와 ledger persistence "
        "테스트 보강."
    ),
    SIGNAL_REPEATED_USER_COMPLAINT: (
        "사용자 메시지에서 동일 키워드/불만이 반복되면 운영-리서치 thread 에 "
        "tech-lead 가 해석 + 우선순위 부여."
    ),
}


def _default_remediation_for(signal: SelfImprovementSignal) -> str:
    return _REMEDIATION_BY_SIGNAL.get(
        signal.signal,
        "신호별 해석 정책이 아직 등록되지 않았습니다. self_improvement.py "
        "에 _REMEDIATION_BY_SIGNAL 항목을 추가하세요.",
    )


# ---------------------------------------------------------------------------
# Proposal planner — A-M12 wiring
# ---------------------------------------------------------------------------
#
# Maps a :class:`SelfImprovementSignal` (or several) to a concrete
# :class:`SelfImprovementProposal`. The planner consults
# :func:`agents.lifecycle.autonomy_policy.decide_autonomy` so the
# L0–L4 ladder owns the routing decision; the planner only translates
# the verdict into the right downstream artifact:
#
#   * L0 / L1 / L2 → :class:`ObsidianWriteRequest` for the
#     ``self-improvement-proposal`` (or ``failure-postmortem``) note kind
#     so the supervisor can enqueue it on the obsidian_write queue. The
#     write itself runs without human approval (``audit_required`` is
#     enforced separately by the agent-ops audit log).
#   * L3 → an opaque approval-envelope mapping. The planner does NOT
#     enqueue an approval card; the dispatch_fn the supervisor wires
#     in (test stub or production helper) is responsible for that
#     handoff. Keeping the envelope here means the planner stays free
#     of queue side effects.
#   * L4 → blocked. The planner refuses to build a write_request OR an
#     approval envelope; ``blocked_reason`` carries the L4 verdict
#     verbatim so callers can log it but never auto-execute. This is
#     the "dangerous action 자동 실행 금지" rail.
#
# The runtime-code-change stub uses the same plan helpers but always
# proposes :data:`autonomy_policy.ACTION_RUNTIME_CODE_CHANGE` (default
# L3) so it never resolves to an L1/L2 auto-write — branch + commit
# automation is intentionally deferred.


# Default autonomy actions per signal — picks the L2 ``failure-
# postmortem`` family for "something is failing" signals, the L2
# ``self-improvement-proposal`` family for "user pattern" signals.
_SIGNAL_TO_ACTION: Mapping[str, str] = {
    SIGNAL_FAILED_RETRYABLE_PILEUP: "failure_postmortem_create",
    SIGNAL_DUPLICATE_TOPIC_APPROVAL: "failure_postmortem_create",
    SIGNAL_STALE_HEARTBEAT: "failure_postmortem_create",
    SIGNAL_EMPTY_KNOWLEDGE_NOTE: "failure_postmortem_create",
    SIGNAL_REPEATED_USER_COMPLAINT: "self_improvement_proposal",
}


# Note kind per autonomy action — only the L2 actions resolve to a
# write here; L3+ never reaches the note-kind stage because the
# planner returns an approval envelope or a blocked verdict instead.
_ACTION_TO_NOTE_KIND: Mapping[str, str] = {
    "failure_postmortem_create": "failure-postmortem",
    "self_improvement_proposal": "self-improvement-proposal",
    "blog_draft_create": "blog-draft",
}


@dataclass(frozen=True)
class SelfImprovementProposal:
    """One actionable plan derived from a :class:`SelfImprovementSignal`.

    Exactly one of ``write_request`` / ``approval_envelope`` /
    ``blocked_reason`` is populated:

      * ``write_request`` — set for L0/L1/L2 signals; an
        :class:`ObsidianWriteRequest` ready for
        :meth:`ObsidianWriterWorker.enqueue`.
      * ``approval_envelope`` — set for L3 signals; a JSON-friendly
        mapping carrying the autonomy decision id, action type, and a
        short summary the dispatch_fn can use to compose an approval
        card. The planner never inserts queue rows.
      * ``blocked_reason`` — set for L4 signals; the human-readable
        rationale from the autonomy decision. The supervisor / caller
        records this in agent-ops audit but MUST NOT auto-execute.

    ``decision`` carries the :class:`AutonomyDecision` itself so callers
    have the audit-required / requires_human / escalation_reasons for
    free.
    """

    signal: SelfImprovementSignal
    decision: Any  # AutonomyDecision (lazy import in test code)
    write_request: Optional[Any] = None
    approval_envelope: Optional[Mapping[str, Any]] = None
    blocked_reason: Optional[str] = None

    @property
    def is_auto_executable(self) -> bool:
        return (
            self.write_request is not None
            and self.approval_envelope is None
            and self.blocked_reason is None
        )

    @property
    def needs_human_approval(self) -> bool:
        return self.approval_envelope is not None

    @property
    def is_blocked(self) -> bool:
        return self.blocked_reason is not None


def plan_self_improvement_proposal(
    signal: SelfImprovementSignal,
    *,
    session_id: str = "",
    project: Optional[str] = None,
    layout: Optional[str] = None,
    action_override: Optional[str] = None,
    risk_level: Optional[str] = None,
    reversible: Optional[bool] = None,
    external_side_effect: Optional[bool] = None,
    requested_by: str = "self_improvement_detector",
    decide_fn: Optional[Any] = None,
    request_builder: Optional[Any] = None,
    extras: Optional[Mapping[str, Any]] = None,
) -> SelfImprovementProposal:
    """Plan a downstream artifact for *signal*.

    Resolution order:

      1. Pick the autonomy action — *action_override* if given,
         otherwise :data:`_SIGNAL_TO_ACTION` (defaults to
         ``self_improvement_proposal`` for unknown signals).
      2. Build an :class:`AutonomyContext` and run *decide_fn*
         (defaults to :func:`autonomy_policy.decide_autonomy`).
         Risk metadata escalation flows through the policy as usual.
      3. L0/L1/L2 → call *request_builder* (defaults to
         :func:`autonomous_producers.build_simple_body_request`) with
         the markdown body produced by
         :func:`render_signals_as_proposal_body`.
      4. L3 → return an approval envelope (no queue side effects).
      5. L4 → return a blocked verdict.

    ``decide_fn`` and ``request_builder`` are seams so unit tests can
    isolate the planner without importing the full autonomy chain.
    """

    from .autonomy_policy import (
        AutonomyContext,
        AutonomyLevel,
        decide_autonomy as _default_decide,
    )

    decide = decide_fn or _default_decide

    action = action_override or _SIGNAL_TO_ACTION.get(
        signal.signal, "self_improvement_proposal"
    )

    severity_to_risk = {
        SEVERITY_HIGH: "high",
        SEVERITY_MEDIUM: "medium",
        SEVERITY_LOW: "low",
    }
    inferred_risk = severity_to_risk.get(signal.severity, "medium")
    ctx = AutonomyContext(
        action=action,
        session_id=session_id,
        topic_key=str(signal.evidence.get("topic_key") or "") or None,
        summary=signal.summary,
        risk_level=risk_level if risk_level is not None else inferred_risk,
        reversible=True if reversible is None else bool(reversible),
        external_side_effect=(
            False
            if external_side_effect is None
            else bool(external_side_effect)
        ),
        requested_by=requested_by,
        reason=signal.summary,
        extra=dict(extras or {}),
    )
    decision = decide(ctx)

    level = decision.autonomy_level
    if level == AutonomyLevel.L4_STRONG_APPROVAL_OR_FORBIDDEN:
        return SelfImprovementProposal(
            signal=signal,
            decision=decision,
            blocked_reason=decision.reason
            or "L4 — 강한 승인 또는 금지, 자동 실행 금지",
        )

    if level == AutonomyLevel.L3_HUMAN_APPROVAL:
        envelope: Mapping[str, Any] = _build_approval_envelope(
            signal=signal,
            decision=decision,
            session_id=session_id,
        )
        return SelfImprovementProposal(
            signal=signal,
            decision=decision,
            approval_envelope=envelope,
        )

    # L0 / L1 / L2 — auto-executable write request.
    note_kind = _ACTION_TO_NOTE_KIND.get(action, "self-improvement-proposal")
    body = render_signals_as_proposal_body(
        [signal],
        title=_proposal_title(signal),
    )
    request_kwargs = {
        "session": _SessionRef(session_id=session_id),
        "note_kind": note_kind,
        "title": _proposal_title(signal),
        "body": body,
        "autonomy_level": level.value,
        "project": project,
        "layout": layout,
        "extras": {
            "self_improvement_signal": signal.signal,
            "severity": signal.severity,
            "decision_id": decision.decision_id,
            "topic_key": ctx.topic_key,
        },
    }
    builder = request_builder
    if builder is None:
        from .autonomous_producers import build_simple_body_request as builder

    write_request = builder(**request_kwargs)
    return SelfImprovementProposal(
        signal=signal,
        decision=decision,
        write_request=write_request,
    )


def plan_self_improvement_proposals(
    signals: Sequence[SelfImprovementSignal],
    *,
    session_id: str = "",
    project: Optional[str] = None,
    layout: Optional[str] = None,
    decide_fn: Optional[Any] = None,
    request_builder: Optional[Any] = None,
) -> Tuple[SelfImprovementProposal, ...]:
    """Plan proposals for a batch of signals (preserves ``signals`` order)."""

    proposals: list[SelfImprovementProposal] = []
    for sig in signals:
        proposals.append(
            plan_self_improvement_proposal(
                sig,
                session_id=session_id,
                project=project,
                layout=layout,
                decide_fn=decide_fn,
                request_builder=request_builder,
            )
        )
    return tuple(proposals)


def propose_runtime_code_change_stub(
    *,
    summary: str,
    session_id: str = "",
    risk_level: str = "medium",
    decide_fn: Optional[Any] = None,
) -> SelfImprovementProposal:
    """Adapter stub — propose a runtime code change without executing it.

    This exists so the M12 self-improvement loop can wire a "we'd like
    to fix the code that triggered this signal" proposal without the
    branch + commit machinery. The autonomy ladder maps
    ``runtime_code_change`` to L3, which means this helper always
    returns an approval envelope. **The planner never creates a git
    branch, runs a commit, or hits a remote** — that surface is
    deferred to a later phase. Tests assert this contract.
    """

    synthetic_signal = SelfImprovementSignal(
        signal=SIGNAL_REPEATED_USER_COMPLAINT,
        severity=SEVERITY_MEDIUM,
        summary=summary,
        evidence={},
        detected_at=_utc_now_iso(),
    )
    return plan_self_improvement_proposal(
        synthetic_signal,
        session_id=session_id,
        action_override="runtime_code_change",
        risk_level=risk_level,
        # Code changes touch shared repo state — treat as an external
        # side effect so the autonomy policy escalates if it somehow
        # fell back to an L0/L1/L2 default.
        external_side_effect=True,
        decide_fn=decide_fn,
    )


def _proposal_title(signal: SelfImprovementSignal) -> str:
    label = signal.signal.replace("_", "-")
    return f"self-improvement: {label}"[:80]


def _build_approval_envelope(
    *,
    signal: SelfImprovementSignal,
    decision: Any,
    session_id: str,
) -> Mapping[str, Any]:
    """Compose the JSON-friendly L3 approval envelope.

    The dispatch_fn (test stub or production handoff) consumes this
    payload to compose an approval card. Keeping it as a plain dict
    means the planner doesn't depend on the queue layer.
    """

    decision_payload: Mapping[str, Any] = {}
    try:
        decision_payload = dict(decision.to_payload())
    except Exception:  # noqa: BLE001 — best effort
        decision_payload = {
            "action": getattr(decision, "action", None),
            "autonomy_level": getattr(
                getattr(decision, "autonomy_level", None), "value", None
            ),
            "reason": getattr(decision, "reason", None),
        }

    return {
        "kind": "approval_request",
        "session_id": session_id,
        "signal": signal.to_payload(),
        "autonomy_decision": decision_payload,
        "summary": signal.summary,
        "requested_action": decision_payload.get("action"),
        "title": _proposal_title(signal),
    }


@dataclass(frozen=True)
class _SessionRef:
    """Minimal stand-in for ``WorkflowSession`` so the request builder
    only needs ``.session_id`` / ``.extra`` / ``.prompt`` / ``.thread_id``.

    The self-improvement planner deliberately does NOT load a real
    workflow session — these proposals are emitted from the supervisor
    sweep, where no session context is naturally available.
    """

    session_id: str
    extra: Mapping[str, Any] = field(default_factory=dict)
    prompt: str = ""
    thread_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


__all__ = (
    "SEVERITY_HIGH",
    "SEVERITY_LOW",
    "SEVERITY_MEDIUM",
    "SIGNAL_DUPLICATE_TOPIC_APPROVAL",
    "SIGNAL_EMPTY_KNOWLEDGE_NOTE",
    "SIGNAL_FAILED_RETRYABLE_PILEUP",
    "SIGNAL_REPEATED_USER_COMPLAINT",
    "SIGNAL_STALE_HEARTBEAT",
    "SelfImprovementProposal",
    "SelfImprovementSignal",
    "collect_self_improvement_signals",
    "detect_duplicate_topic_approval",
    "detect_empty_knowledge_note_attempts",
    "detect_failed_retryable_pileup",
    "detect_stale_heartbeat",
    "plan_self_improvement_proposal",
    "plan_self_improvement_proposals",
    "propose_runtime_code_change_stub",
    "render_signals_as_proposal_body",
)
