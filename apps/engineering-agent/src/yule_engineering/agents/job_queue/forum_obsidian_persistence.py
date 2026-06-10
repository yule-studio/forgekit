"""Forum→Obsidian handoff — persistence / audit side of the producer.

Split out of :mod:`forum_obsidian_handoff` along the
``intake / routing / persistence`` axis. This module owns the
**persistence** responsibility: writing the topic ledger back to
``session.extra``, emitting the L1 research-log auto-save job, and
appending agent-ops audit entries for each handoff decision.

All functions here are best-effort and swallow their own failures —
persistence is observability and must never block the load-bearing
approval card from going out. The orchestrator
(:mod:`forum_obsidian_handoff`) imports these one-way; nothing here
imports the orchestrator except the shared ``_short_error`` helper.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Sequence


logger = logging.getLogger(__name__)


def _persist_ledger(
    *,
    session: Any,
    record: Any,
    session_updater: Optional[Callable[..., Any]] = None,
) -> None:
    """Best-effort persistence of the topic ledger record. Failure
    is swallowed — the ledger is observability; losing it doesn't
    block the approval card from going out.
    """

    if session is None:
        return
    try:
        from dataclasses import replace as _replace

        from ..lifecycle.research_topic import write_topic_ledger
        from ..workflow_state import update_session as _default_update
    except Exception:  # noqa: BLE001
        return

    updater = session_updater or _default_update
    extra_in = dict(getattr(session, "extra", None) or {})
    new_extra = write_topic_ledger(extra_in, record)
    try:
        updated = _replace(session, extra=new_extra)
    except TypeError:
        # SimpleNamespace-shaped session in tests — mutate in place.
        if isinstance(getattr(session, "extra", None), dict):
            session.extra.update(new_extra)
        return
    try:
        updater(updated, now=datetime.now(tz=timezone.utc))
    except Exception:  # noqa: BLE001
        logger.warning(
            "forum obsidian handoff: ledger persist raised", exc_info=True
        )


# ---------------------------------------------------------------------------
# A-M10c — research-log auto-save shim (L1 — auto-execute, audit required)
# ---------------------------------------------------------------------------


def _emit_research_log_auto_save(
    *,
    session: Any,
    ledger_record: Any,
    snapshot: Any,
    selected_roles: Sequence[str],
    source_thread_url: Optional[str],
    source_thread_title: Optional[str],
    requested_by: Optional[str],
    obsidian_writer_worker: Any,
    session_updater: Optional[Callable[..., Any]],
    approval_job_id: Optional[str],
) -> None:
    """Enqueue an L1 research-log obsidian_write alongside the L3
    approval card, with an agent-ops audit row recording the
    decision. Failure is swallowed — the approval queue must not
    suffer for the auto-save side-effect.
    """

    if obsidian_writer_worker is None:
        # Observability — the production wiring will inject the
        # worker; tests that don't care about the auto-save path
        # leave it None and we silently no-op.
        return

    # Lazy import of the shared short-error formatter keeps the
    # persistence module free of a top-level back-edge into the
    # orchestrator (avoids an import-order-dependent cycle).
    from .forum_obsidian_handoff import _short_error

    try:
        from ..lifecycle.autonomous_producers import build_research_log_request
    except Exception:  # noqa: BLE001
        return

    try:
        request = build_research_log_request(
            session=session,
            snapshot=snapshot,
            canonical_title=getattr(ledger_record, "canonical_title", None),
            topic_key=getattr(ledger_record, "topic_key", None),
            source_thread_url=source_thread_url,
            source_thread_title=source_thread_title,
            selected_roles=selected_roles,
            requested_by=requested_by,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "forum obsidian handoff: build_research_log_request raised",
            exc_info=True,
        )
        return

    try:
        job, created = obsidian_writer_worker.enqueue(request)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "forum obsidian handoff: research-log enqueue raised",
            exc_info=True,
        )
        _record_research_log_audit(
            session=session,
            ledger_record=ledger_record,
            outcome=f"failure:research_log_enqueue_raised:{_short_error(exc)}",
            summary="research-log 자동 저장 enqueue 실패",
            job_id=None,
            session_updater=session_updater,
        )
        return

    job_id = getattr(job, "job_id", None)
    if not created:
        outcome = f"skipped:research_log_already_active (job=`{job_id or '?'}`)"
        summary = (
            "동일 세션/topic/thread 의 research-log 잡이 이미 큐에 존재 — 신규 enqueue 생략"
        )
    else:
        outcome = "research_log_enqueued"
        summary = (
            f"운영-리서치 thread 의 research-log 자동 저장 잡 enqueue "
            f"(approval=`{approval_job_id or '-'}`)"
        )
    _record_research_log_audit(
        session=session,
        ledger_record=ledger_record,
        outcome=outcome,
        summary=summary,
        job_id=job_id,
        session_updater=session_updater,
    )


def _record_research_log_audit(
    *,
    session: Any,
    ledger_record: Any,
    outcome: str,
    summary: str,
    job_id: Optional[str],
    session_updater: Optional[Callable[..., Any]],
) -> None:
    """Append an L1 research-log audit entry to session.extra."""

    if session is None:
        return
    try:
        from ..lifecycle.agent_ops_log import (
            append_agent_ops_audit,
            build_agent_ops_entry,
        )
        from ..lifecycle.autonomy_policy import (
            ACTION_RESEARCH_LOG_SAVE,
            AutonomyContext,
            decide_autonomy,
        )
        from dataclasses import replace as _replace

        try:
            from ..workflow_state import update_session as _default_update
        except Exception:  # noqa: BLE001
            _default_update = None  # type: ignore[assignment]
    except Exception:  # noqa: BLE001
        return

    decision = decide_autonomy(
        AutonomyContext(
            action=ACTION_RESEARCH_LOG_SAVE,
            session_id=str(getattr(session, "session_id", "") or ""),
            job_id=job_id,
            topic_key=getattr(ledger_record, "topic_key", None),
            summary=summary,
        )
    )
    entry = build_agent_ops_entry(
        decision=decision,
        outcome=outcome,
        summary=summary,
        job_id=job_id,
    )

    extra_in = getattr(session, "extra", None) or {}
    new_extra = append_agent_ops_audit(extra_in, entry)
    try:
        updated = _replace(session, extra=new_extra)
    except TypeError:
        if isinstance(getattr(session, "extra", None), dict):
            session.extra.update(new_extra)
        return
    if _default_update is None and session_updater is None:
        return
    updater = session_updater or _default_update
    try:
        updater(updated, now=datetime.now(tz=timezone.utc))
    except Exception:  # noqa: BLE001
        logger.warning(
            "forum obsidian handoff: research-log audit persist raised",
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# A-M10a — agent-ops audit shim
# ---------------------------------------------------------------------------


def _record_handoff_audit(
    *,
    session: Any,
    outcome: str,
    summary: str,
    references: Sequence[str] = (),
    job_id: Optional[str] = None,
    topic_key: Optional[str] = None,
    requires_human: bool,
    session_updater: Optional[Callable[..., Any]] = None,
) -> None:
    """Append an agent-ops entry for one forum-handoff decision.

    The audit landing point is ``session.extra['agent_ops_audit']``
    — same shape as :mod:`agents.lifecycle.agent_ops_log` defines.
    Failure is swallowed; the audit is observability and must not
    block the friendly Discord reply.

    *requires_human* controls the autonomy level we stamp on the
    entry: when True (the topic requires a human approval card)
    we record the decision under the **L3** action surface so an
    operator scanning the audit can see "이 thread 는 사람 승인 단계로
    넘겼다" without inspecting the Discord channel. When False
    (skip outcomes that resolved without a card going out) we
    record the L1 forum-handoff-decision audit instead.
    """

    if session is None:
        return

    try:
        from ..lifecycle.agent_ops_log import (
            append_agent_ops_audit,
            build_agent_ops_entry,
        )
        from ..lifecycle.autonomy_policy import (
            ACTION_FORUM_HANDOFF_DECISION,
            ACTION_KNOWLEDGE_NOTE_FINALIZE,
            AutonomyContext,
            decide_autonomy,
        )
        from dataclasses import replace as _replace

        try:
            from ..workflow_state import update_session as _default_update
        except Exception:  # noqa: BLE001
            _default_update = None  # type: ignore[assignment]
    except Exception:  # noqa: BLE001
        return

    action = (
        ACTION_KNOWLEDGE_NOTE_FINALIZE
        if requires_human
        else ACTION_FORUM_HANDOFF_DECISION
    )
    decision = decide_autonomy(
        AutonomyContext(
            action=action,
            session_id=str(getattr(session, "session_id", "") or ""),
            job_id=job_id,
            topic_key=topic_key,
            summary=summary,
        )
    )
    if not decision.audit_required:
        return

    entry = build_agent_ops_entry(
        decision=decision,
        outcome=outcome,
        summary=summary,
        references=references,
        job_id=job_id,
    )
    extra_in = getattr(session, "extra", None) or {}
    new_extra = append_agent_ops_audit(extra_in, entry)

    try:
        updated = _replace(session, extra=new_extra)
    except TypeError:
        # SimpleNamespace-shaped session in tests — mutate in place.
        if isinstance(getattr(session, "extra", None), dict):
            session.extra.update(new_extra)
        return
    if _default_update is None and session_updater is None:
        return
    updater = session_updater or _default_update
    try:
        updater(updated, now=datetime.now(tz=timezone.utc))
    except Exception:  # noqa: BLE001
        logger.warning(
            "forum obsidian handoff: agent-ops audit persist raised",
            exc_info=True,
        )


__all__ = (
    "_emit_research_log_auto_save",
    "_persist_ledger",
    "_record_handoff_audit",
    "_record_research_log_audit",
)
