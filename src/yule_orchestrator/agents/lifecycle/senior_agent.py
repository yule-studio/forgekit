"""Senior-agent MVP coordinator — F-M13.

Single integration seam tying M8 readiness, M9 topic ledger, M10a
autonomy ladder, M10b Obsidian hydration, M10c research-log auto save,
M11 role-runner dispatch, and M12 self-improvement signals into one
loop a tester / production caller can drive without re-walking the
underlying primitives.

The module is **pure-Python** — no Discord client, no SQLite, no LLM
provider. Every external dependency is injected so the same coordinator
runs in a unit test and in the real runtime. M13 explicitly does NOT
introduce a new persistence model or duplicate behaviour the existing
producers already cover (forum_obsidian_handoff for the L3 approval
path, ObsidianWriterWorker for vault writes); instead it delegates to
those surfaces and only orchestrates the cross-module flow:

  intake/topic ledger → role-runner dispatch (with audit) →
  research-log auto save (L1) → optional self-improvement proposal
  (L2) → return a structured outcome the caller can rely on.

Knowledge note finalisation stays L3 and continues to flow through
:func:`agents.job_queue.forum_obsidian_handoff.route_forum_obsidian_save_request`
— this module only ensures the writer / topic ledger / audit trail
that path needs is in place before the user types
"Obsidian 에 정리하고 싶어".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (
    Any,
    Callable,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)


logger = logging.getLogger(__name__)


# Public coordinator entry-points are exported at the bottom; these
# imports stay at module load so the M13 surface fails loudly if any
# of M8-M12 regress (rather than silently no-op'ing under a broken
# install).
from .agent_ops_log import (
    AgentOpsEntry,
    SESSION_EXTRA_KEY as AGENT_OPS_SESSION_EXTRA_KEY,
    append_agent_ops_audit,
    build_agent_ops_entry,
    read_agent_ops_audit,
)
from .autonomous_producers import (
    build_research_log_request,
    build_simple_body_request,
)
from .autonomy_policy import (
    ACTION_RESEARCH_LOG_SAVE,
    ACTION_ROLE_TAKE_RECORD,
    ACTION_SELF_IMPROVEMENT_PROPOSAL,
    ACTION_USER_ORDERED_RESEARCH,
    AutonomyContext,
    AutonomyDecision,
    decide_autonomy,
)
from .research_topic import (
    STATUS_RESEARCHING,
    TopicLedgerRecord,
    build_ledger_record,
    read_topic_ledger,
    transition_topic_ledger,
    write_topic_ledger,
)
from .self_improvement import (
    SelfImprovementSignal,
    collect_self_improvement_signals,
    render_signals_as_proposal_body,
)
from .thread_snapshot import ThreadSnapshot


# ---------------------------------------------------------------------------
# Outcome dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleRunOutcome:
    """One role's contribution to a senior-agent research order.

    ``provider`` is the role-runner backend that produced the take
    (one of "claude" / "codex" / "ollama" / "deterministic"); the
    coordinator copies this onto the agent-ops audit so an operator
    can answer "이 take 누가 썼어?" without inspecting Discord.
    """

    role: str
    provider: str
    status: str
    text: str
    used_fallback: bool
    detail: Optional[str] = None


@dataclass(frozen=True)
class SeniorAgentRunOutcome:
    """What :func:`handle_research_order` produced.

    ``ledger_record`` is the canonical topic ledger after this run —
    same prompt + same thread later returns the same ``topic_key``.
    ``role_outputs`` is empty when the caller did not pass a
    role-runner dispatcher (e.g. M11 fallback-only environments).
    ``research_log_job_id`` is None when the writer wasn't injected;
    the caller can then enqueue the same request later if needed.
    """

    ledger_record: TopicLedgerRecord
    role_outputs: Tuple[RoleRunOutcome, ...]
    research_log_job_id: Optional[str]
    research_log_created: bool
    audit_entries: Tuple[AgentOpsEntry, ...]
    used_runner_fallback: bool


@dataclass(frozen=True)
class ImprovementProposalOutcome:
    """What :func:`emit_self_improvement_proposal` produced.

    ``signals`` carries the detected anomalies even when no proposal
    was queued (so the caller can still surface them via
    ``#봇-상태``). ``proposal_job_id`` is None when nothing fired —
    either no signals, or the writer wasn't injected.
    """

    signals: Tuple[SelfImprovementSignal, ...]
    proposal_job_id: Optional[str]
    proposal_created: bool
    audit_entry: Optional[AgentOpsEntry]


# ---------------------------------------------------------------------------
# Injected dependency types
# ---------------------------------------------------------------------------


# (session, RoleRunnerInput) → RoleRunnerOutput. Built by the caller
# via :func:`agents.runners.build_role_runner_dispatcher`. We type it
# loosely so this module doesn't need to import runners (keeping the
# import graph one-way: senior_agent depends on lifecycle + outputs of
# runners, never on runner internals).
RoleRunnerDispatch = Callable[[Any, Any], Any]

#: Persists a session.extra mutation. Production wires this to
#: ``workflow_state.update_session``; tests pass an in-memory writer.
SessionExtraWriter = Callable[[Any, Mapping[str, Any]], None]


# ---------------------------------------------------------------------------
# Default dependency wiring
# ---------------------------------------------------------------------------


def _default_session_extra_writer(session: Any, new_extra: Mapping[str, Any]) -> None:
    """In-place update of ``session.extra`` when the caller didn't
    inject a writer. Mirrors the SimpleNamespace-friendly path the
    forum-handoff / autonomous-producers helpers already use.
    """

    if session is None:
        return
    extra = getattr(session, "extra", None)
    if isinstance(extra, dict):
        extra.clear()
        extra.update(new_extra)
        return
    try:
        from dataclasses import replace as _replace

        replaced = _replace(session, extra=dict(new_extra))
    except TypeError:
        return
    # Best-effort writeback to a real workflow_state row when one is
    # available — silently no-op in test contexts where the row isn't
    # backed by a SQLite cache.
    try:
        from ..workflow_state import update_session as _update

        _update(replaced, now=datetime.now(tz=timezone.utc))
    except Exception:  # noqa: BLE001 - update is observability only
        logger.debug(
            "senior_agent: workflow_state.update_session unavailable; "
            "in-memory mutation only",
            exc_info=True,
        )


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def _persist_extra(
    *,
    session: Any,
    new_extra: Mapping[str, Any],
    writer: Optional[SessionExtraWriter],
) -> None:
    """Funnel every session.extra mutation through one writer hook so
    test fakes only need to capture once.
    """

    fn = writer or _default_session_extra_writer
    try:
        fn(session, dict(new_extra))
    except Exception:  # noqa: BLE001 - audit/ledger writes are best-effort
        logger.warning(
            "senior_agent: session.extra writer raised; mutation lost",
            exc_info=True,
        )


def _stamp_audit(
    *,
    session: Any,
    decision: AutonomyDecision,
    outcome: str,
    summary: Optional[str] = None,
    references: Sequence[str] = (),
    job_id: Optional[str] = None,
    writer: Optional[SessionExtraWriter] = None,
) -> AgentOpsEntry:
    """Build + persist one agent-ops entry. Returns the entry so the
    coordinator's outcome can echo what the audit log received.
    """

    entry = build_agent_ops_entry(
        decision=decision,
        outcome=outcome,
        summary=summary,
        references=references,
        job_id=job_id,
    )
    extra_in = getattr(session, "extra", None) if session is not None else None
    new_extra = append_agent_ops_audit(extra_in or {}, entry)
    _persist_extra(session=session, new_extra=new_extra, writer=writer)
    return entry


# ---------------------------------------------------------------------------
# Topic ledger helper
# ---------------------------------------------------------------------------


def _ensure_topic_ledger(
    *,
    session: Any,
    research_thread_id: Optional[int],
    active_roles: Sequence[str],
    writer: Optional[SessionExtraWriter],
) -> TopicLedgerRecord:
    """Read or build the topic ledger record, persist it, return it.

    The same prompt + same thread always maps to the same ``topic_key``
    via :func:`build_ledger_record` — re-running the senior-agent loop
    on the same intake therefore reuses the prior record without
    creating a sibling.
    """

    existing = read_topic_ledger(session)
    if existing is not None:
        return existing
    record = build_ledger_record(
        session=session,
        research_thread_id=research_thread_id,
        active_roles=active_roles,
        status=STATUS_RESEARCHING,
    )
    extra_in = getattr(session, "extra", None) if session is not None else None
    new_extra = write_topic_ledger(extra_in or {}, record)
    _persist_extra(session=session, new_extra=new_extra, writer=writer)
    return record


# ---------------------------------------------------------------------------
# Role runner pass
# ---------------------------------------------------------------------------


def _run_active_roles(
    *,
    session: Any,
    record: TopicLedgerRecord,
    active_roles: Sequence[str],
    role_runner_dispatch: Optional[RoleRunnerDispatch],
    snapshot: Optional[ThreadSnapshot],
    role_profile_loader: Optional[Callable[[str], Mapping[str, Any]]],
    extra_writer: Optional[SessionExtraWriter],
    audit_entries_out: List[AgentOpsEntry],
) -> Tuple[Tuple[RoleRunOutcome, ...], bool]:
    """Drive every active role through the runner dispatcher.

    Returns ``(outcomes, used_fallback)``. Each role's audit entry is
    appended to *audit_entries_out* so the caller can return it as
    part of :class:`SeniorAgentRunOutcome.audit_entries`.

    No dispatcher → no role outputs (M11 fallback-only mode); the
    coordinator stays usable in environments without LLM wiring.
    """

    if role_runner_dispatch is None or not active_roles:
        return ((), False)

    # Lazy import — keep the runners module out of the import graph
    # for callers who only need the topic-ledger / research-log
    # subset of the coordinator surface.
    from ..runners.role_runner import (
        RoleRunnerInput,
        RoleRunnerOutput,
        STATUS_OK,
    )

    role_outputs: List[RoleRunOutcome] = []
    used_fallback = False
    for role in active_roles:
        profile: Mapping[str, Any] = {}
        if role_profile_loader is not None:
            try:
                loaded = role_profile_loader(role)
                if isinstance(loaded, Mapping):
                    profile = dict(loaded)
            except Exception:  # noqa: BLE001 - loader is best-effort
                logger.debug(
                    "senior_agent: role_profile_loader raised for role=%s",
                    role,
                    exc_info=True,
                )

        runner_input = RoleRunnerInput(
            role=role,
            session_id=str(getattr(session, "session_id", "") or ""),
            prompt=str(getattr(session, "prompt", "") or ""),
            role_profile=profile,
            topic_memory={
                "topic_key": record.topic_key,
                "canonical_title": record.canonical_title,
                "status": record.status,
                "revision": record.revision,
            },
            source_context=(
                snapshot.to_payload() if snapshot is not None else {}
            ),
            previous_decisions=(),
        )

        try:
            output: Any = role_runner_dispatch(session, runner_input)
        except Exception as exc:  # noqa: BLE001 - dispatcher must not crash loop
            logger.warning(
                "senior_agent: role-runner dispatch raised for role=%s",
                role,
                exc_info=True,
            )
            decision = decide_autonomy(
                AutonomyContext(
                    action=ACTION_ROLE_TAKE_RECORD,
                    session_id=str(getattr(session, "session_id", "") or ""),
                    topic_key=record.topic_key,
                    summary=f"role-runner dispatch raised for {role}",
                )
            )
            entry = _stamp_audit(
                session=session,
                decision=decision,
                outcome=f"failure:role_runner_dispatch_raised:{type(exc).__name__}",
                summary=f"{role} runner dispatch 실패",
                writer=extra_writer,
            )
            audit_entries_out.append(entry)
            role_outputs.append(
                RoleRunOutcome(
                    role=role,
                    provider="deterministic",
                    status="error",
                    text="",
                    used_fallback=True,
                    detail=str(exc) or type(exc).__name__,
                )
            )
            used_fallback = True
            continue

        provider = str(getattr(output, "provider", "") or "deterministic")
        status = str(getattr(output, "status", "") or "")
        text = str(getattr(output, "text", "") or "")
        fallback_flag = bool(getattr(output, "used_fallback", False))
        detail = getattr(output, "detail", None)
        if status != STATUS_OK:
            used_fallback = used_fallback or fallback_flag or status != STATUS_OK

        decision = decide_autonomy(
            AutonomyContext(
                action=ACTION_ROLE_TAKE_RECORD,
                session_id=str(getattr(session, "session_id", "") or ""),
                topic_key=record.topic_key,
                summary=f"{role} take via {provider}",
            )
        )
        outcome_str = (
            f"role_take:{status}:{provider}"
            f"{':fallback' if fallback_flag else ''}"
        )
        entry = _stamp_audit(
            session=session,
            decision=decision,
            outcome=outcome_str,
            summary=(
                f"{role} take 생성 (provider={provider}, status={status})"
            ),
            writer=extra_writer,
        )
        audit_entries_out.append(entry)
        role_outputs.append(
            RoleRunOutcome(
                role=role,
                provider=provider,
                status=status,
                text=text,
                used_fallback=fallback_flag,
                detail=str(detail) if detail else None,
            )
        )

    return tuple(role_outputs), used_fallback


# ---------------------------------------------------------------------------
# Research-log auto save
# ---------------------------------------------------------------------------


def _enqueue_research_log(
    *,
    session: Any,
    record: TopicLedgerRecord,
    snapshot: Optional[ThreadSnapshot],
    selected_roles: Sequence[str],
    source_thread_url: Optional[str],
    source_thread_title: Optional[str],
    requested_by: Optional[str],
    obsidian_writer_worker: Any,
    extra_writer: Optional[SessionExtraWriter],
    audit_entries_out: List[AgentOpsEntry],
) -> Tuple[Optional[str], bool]:
    """Build + enqueue the L1 research-log obsidian_write request.

    Returns ``(job_id, created)``. ``created`` is False when the
    writer's idempotency dedup found an active twin row for this
    session/topic. None worker → ``(None, False)``.
    """

    if obsidian_writer_worker is None:
        return (None, False)

    request = build_research_log_request(
        session=session,
        snapshot=snapshot,
        canonical_title=record.canonical_title,
        topic_key=record.topic_key,
        source_thread_url=source_thread_url,
        source_thread_title=source_thread_title,
        selected_roles=selected_roles,
        requested_by=requested_by,
    )
    try:
        job, created = obsidian_writer_worker.enqueue(request)
    except Exception as exc:  # noqa: BLE001 - enqueue must not break the loop
        logger.warning(
            "senior_agent: research-log enqueue raised", exc_info=True
        )
        decision = decide_autonomy(
            AutonomyContext(
                action=ACTION_RESEARCH_LOG_SAVE,
                session_id=str(getattr(session, "session_id", "") or ""),
                topic_key=record.topic_key,
                summary="research-log enqueue 실패",
            )
        )
        entry = _stamp_audit(
            session=session,
            decision=decision,
            outcome=f"failure:research_log_enqueue_raised:{type(exc).__name__}",
            summary="research-log 자동 저장 enqueue 실패",
            writer=extra_writer,
        )
        audit_entries_out.append(entry)
        return (None, False)

    job_id = getattr(job, "job_id", None)
    decision = decide_autonomy(
        AutonomyContext(
            action=ACTION_RESEARCH_LOG_SAVE,
            session_id=str(getattr(session, "session_id", "") or ""),
            job_id=job_id,
            topic_key=record.topic_key,
            summary="research-log 자동 저장",
        )
    )
    if created:
        outcome_str = "research_log_enqueued"
        summary = (
            f"운영-리서치 thread research-log 자동 저장 잡 enqueue "
            f"(topic={record.topic_key})"
        )
    else:
        outcome_str = f"skipped:research_log_already_active (job=`{job_id or '?'}`)"
        summary = (
            f"동일 세션/topic 의 research-log 잡이 이미 큐에 존재 — "
            f"신규 enqueue 생략 (topic={record.topic_key})"
        )
    entry = _stamp_audit(
        session=session,
        decision=decision,
        outcome=outcome_str,
        summary=summary,
        job_id=job_id,
        writer=extra_writer,
    )
    audit_entries_out.append(entry)
    return (job_id, bool(created))


# ---------------------------------------------------------------------------
# Public entry point — research order
# ---------------------------------------------------------------------------


def handle_research_order(
    *,
    session: Any,
    research_thread_id: Optional[int] = None,
    active_roles: Optional[Sequence[str]] = None,
    snapshot: Optional[ThreadSnapshot] = None,
    role_runner_dispatch: Optional[RoleRunnerDispatch] = None,
    role_profile_loader: Optional[Callable[[str], Mapping[str, Any]]] = None,
    obsidian_writer_worker: Any = None,
    source_thread_url: Optional[str] = None,
    source_thread_title: Optional[str] = None,
    requested_by: Optional[str] = None,
    extra_writer: Optional[SessionExtraWriter] = None,
) -> SeniorAgentRunOutcome:
    """Run one senior-agent research-order pass.

    Steps (every step is L1 — auto-execute, audit required; no
    human approval card is posted):

      1. Resolve / persist the topic ledger record (M9).
      2. Stamp an :data:`ACTION_USER_ORDERED_RESEARCH` audit entry.
      3. For each active role, dispatch through the role-runner
         (M11). Per-role provider / fallback status lands on the
         agent-ops audit so an operator can answer "이 take 누가
         썼어?" without scraping Discord.
      4. Enqueue an L1 research-log Obsidian write (M10b/c) carrying
         the snapshot + extracted links + role summaries that the
         hydration pipeline already round-trips.

    Knowledge note finalisation stays out of this loop — it remains
    L3 and flows through the existing forum-handoff producer when
    the operator says "Obsidian 에 정리하고 싶어". This function
    purely sets up the research-log + topic ledger trail that path
    consumes.

    All external dependencies (role runner dispatcher / Obsidian
    writer / session.extra writer) are injected so the same call
    runs in tests with stubs and in production with the real
    primitives. No Discord, no SQLite, no LLM here.
    """

    if active_roles is None:
        extra = getattr(session, "extra", None) or {}
        raw_roles = (
            extra.get("active_research_roles")
            if isinstance(extra, Mapping)
            else None
        )
        if isinstance(raw_roles, (list, tuple)):
            active_roles = tuple(
                str(r) for r in raw_roles if isinstance(r, str) and r
            )
        else:
            active_roles = ()

    audit_entries: List[AgentOpsEntry] = []

    record = _ensure_topic_ledger(
        session=session,
        research_thread_id=research_thread_id,
        active_roles=active_roles,
        writer=extra_writer,
    )

    intake_decision = decide_autonomy(
        AutonomyContext(
            action=ACTION_USER_ORDERED_RESEARCH,
            session_id=str(getattr(session, "session_id", "") or ""),
            topic_key=record.topic_key,
            summary=(
                f"사용자 명시 리서치 오더 수신 (topic={record.topic_key})"
            ),
        )
    )
    intake_entry = _stamp_audit(
        session=session,
        decision=intake_decision,
        outcome="research_order_received",
        summary=(
            f"사용자 명시 리서치 오더 — topic_key={record.topic_key}, "
            f"active_roles={list(active_roles)}"
        ),
        writer=extra_writer,
    )
    audit_entries.append(intake_entry)

    role_outputs, used_fallback = _run_active_roles(
        session=session,
        record=record,
        active_roles=active_roles,
        role_runner_dispatch=role_runner_dispatch,
        snapshot=snapshot,
        role_profile_loader=role_profile_loader,
        extra_writer=extra_writer,
        audit_entries_out=audit_entries,
    )

    research_log_job_id, created = _enqueue_research_log(
        session=session,
        record=record,
        snapshot=snapshot,
        selected_roles=active_roles,
        source_thread_url=source_thread_url,
        source_thread_title=source_thread_title,
        requested_by=requested_by,
        obsidian_writer_worker=obsidian_writer_worker,
        extra_writer=extra_writer,
        audit_entries_out=audit_entries,
    )

    return SeniorAgentRunOutcome(
        ledger_record=record,
        role_outputs=role_outputs,
        research_log_job_id=research_log_job_id,
        research_log_created=created,
        audit_entries=tuple(audit_entries),
        used_runner_fallback=used_fallback,
    )


# ---------------------------------------------------------------------------
# Public entry point — self-improvement proposal emission
# ---------------------------------------------------------------------------


def emit_self_improvement_proposal(
    *,
    session: Any,
    jobs: Iterable[Any] = (),
    failed_jobs: Iterable[Any] = (),
    heartbeats: Optional[Mapping[str, Any]] = None,
    obsidian_writer_worker: Any = None,
    title: Optional[str] = None,
    project: Optional[str] = None,
    layout: Optional[str] = None,
    extra_writer: Optional[SessionExtraWriter] = None,
    failed_retryable_threshold: int = 3,
    stale_after_seconds: int = 600,
) -> ImprovementProposalOutcome:
    """Run the M12 self-improvement detectors and, if any fired,
    enqueue an L2 ``self-improvement-proposal`` Obsidian write.

    Always returns the detected signals — even when no proposal was
    enqueued — so the caller (status surface, supervisor, test)
    can observe the agent's "noticing" without inspecting the queue.

    The L2 autonomy level means the proposal is auto-written but the
    decision is logged as a mandatory post-report on the agent-ops
    audit. Operators see "이 자료가 왜 자동으로 만들어졌는지" in the
    rendered note's frontmatter (autonomy_level + reasoning).
    """

    signals = collect_self_improvement_signals(
        jobs=jobs,
        failed_jobs=failed_jobs,
        heartbeats=heartbeats,
        failed_retryable_threshold=failed_retryable_threshold,
        stale_after_seconds=stale_after_seconds,
    )
    if not signals:
        return ImprovementProposalOutcome(
            signals=(),
            proposal_job_id=None,
            proposal_created=False,
            audit_entry=None,
        )
    if obsidian_writer_worker is None:
        return ImprovementProposalOutcome(
            signals=signals,
            proposal_job_id=None,
            proposal_created=False,
            audit_entry=None,
        )

    proposal_title = title or (
        f"self-improvement proposal "
        f"{datetime.now(tz=timezone.utc).date().isoformat()}"
    )
    body = render_signals_as_proposal_body(signals, title=proposal_title)
    request = build_simple_body_request(
        session=session,
        note_kind="self-improvement-proposal",
        title=proposal_title,
        body=body,
        autonomy_level="L2_AUTO_POST_REPORT",
        project=project,
        layout=layout,
        extras={
            "signal_count": len(signals),
            "signal_ids": [s.signal for s in signals],
        },
    )

    try:
        job, created = obsidian_writer_worker.enqueue(request)
    except Exception as exc:  # noqa: BLE001 - emission must not crash caller
        logger.warning(
            "senior_agent: self-improvement enqueue raised", exc_info=True
        )
        decision = decide_autonomy(
            AutonomyContext(
                action=ACTION_SELF_IMPROVEMENT_PROPOSAL,
                session_id=str(getattr(session, "session_id", "") or ""),
                summary="self-improvement proposal enqueue 실패",
            )
        )
        entry = _stamp_audit(
            session=session,
            decision=decision,
            outcome=f"failure:self_improvement_enqueue_raised:{type(exc).__name__}",
            summary="self-improvement proposal enqueue 실패",
            writer=extra_writer,
        )
        return ImprovementProposalOutcome(
            signals=signals,
            proposal_job_id=None,
            proposal_created=False,
            audit_entry=entry,
        )

    job_id = getattr(job, "job_id", None)
    decision = decide_autonomy(
        AutonomyContext(
            action=ACTION_SELF_IMPROVEMENT_PROPOSAL,
            session_id=str(getattr(session, "session_id", "") or ""),
            job_id=job_id,
            summary=(
                f"self-improvement proposal 자동 생성 "
                f"(signals={len(signals)})"
            ),
        )
    )
    if created:
        outcome_str = "self_improvement_proposal_enqueued"
        summary = (
            f"L2 — 감지된 신호 {len(signals)}건에 대해 self-improvement "
            f"proposal Obsidian write enqueue"
        )
    else:
        outcome_str = (
            f"skipped:self_improvement_already_active "
            f"(job=`{job_id or '?'}`)"
        )
        summary = (
            "동일 제목/세션의 self-improvement proposal 잡이 이미 활성 — "
            "신규 enqueue 생략"
        )
    entry = _stamp_audit(
        session=session,
        decision=decision,
        outcome=outcome_str,
        summary=summary,
        job_id=job_id,
        writer=extra_writer,
    )

    return ImprovementProposalOutcome(
        signals=signals,
        proposal_job_id=job_id,
        proposal_created=bool(created),
        audit_entry=entry,
    )


# ---------------------------------------------------------------------------
# Convenience surface for tests / runtime callers
# ---------------------------------------------------------------------------


def replay_audit_entries(session: Any) -> Tuple[AgentOpsEntry, ...]:
    """Replay the agent-ops audit list off *session.extra* in
    insertion order. Thin wrapper around
    :func:`agents.lifecycle.agent_ops_log.read_agent_ops_audit` so
    tests and supervisor callers can reach the same surface through
    the M13 entry point.
    """

    return read_agent_ops_audit(session)


__all__ = (
    "ImprovementProposalOutcome",
    "RoleRunOutcome",
    "RoleRunnerDispatch",
    "SeniorAgentRunOutcome",
    "SessionExtraWriter",
    "emit_self_improvement_proposal",
    "handle_research_order",
    "replay_audit_entries",
)
