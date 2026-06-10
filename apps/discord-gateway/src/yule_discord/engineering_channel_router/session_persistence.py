"""engineering_channel_router — session.extra mutations + load helpers.

Every gate / hook that needs to write to ``session.extra`` or look up a
session by id / recency flows through this module. Keeping the single
write surface here means future schema additions (new ``session.extra``
keys, new audit log shapes) have one canonical edit site.

Owned helpers (organised by what they touch on ``session.extra``):

* lifecycle bookkeeping — :func:`_persist_role_selection`,
  :func:`_persist_lifecycle_mode`, :func:`_persist_coding_session_context`,
  :func:`_persist_extra_keys`, :func:`_persist_thread_id`,
  :func:`_record_persistence_failure`, :func:`_is_terminal`.
* coding flow — :func:`_persist_coding_proposal`, :func:`_persist_coding_job`,
  :func:`_proposal_to_dict`, :func:`_proposal_from_dict`.
* reporting — :func:`_work_report_to_dict` (used by the work-report
  preview at lifecycle close).
* session lookup — :func:`_load_session_by_id`, :func:`_most_recent_session`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from yule_orchestrator.agents.coding.authorization import (
    CodingAuthorizationProposal,
)


def _is_terminal(session: Any) -> bool:
    state = getattr(session, "state", None)
    state_value = getattr(state, "value", state)
    return str(state_value).lower() in {"completed", "rejected"}

def _persist_coding_proposal(
    session: Any,
    proposal: CodingAuthorizationProposal,
) -> Any:
    """Stash a fresh proposal under ``session.extra['coding_proposal']``."""

    return _persist_extra_keys(
        session,
        {
            "coding_proposal": _proposal_to_dict(proposal),
            "coding_job": None,  # supersedes any prior pending job copy
        },
    )

def _persist_coding_job(session: Any, job_payload: Mapping[str, object]) -> Any:
    """Replace any pending proposal with the approved coding job payload."""

    return _persist_extra_keys(
        session,
        {
            "coding_job": dict(job_payload),
            "coding_proposal": None,  # consumed
        },
    )

def _persist_role_selection(
    session: Any,
    canonical_prompt: str,
) -> Any:
    """Run :func:`role_selection.recommend_active_roles` against
    *canonical_prompt* and stash the result on ``session.extra``.

    Best-effort: import or persistence failures simply skip — the
    legacy "all roles" fallback path remains operational. Used right
    after intake so the work-report builder + research scoping see a
    populated ``active_research_roles`` from turn one.
    """

    if session is None:
        return session
    try:
        from yule_orchestrator.agents.lifecycle.role_selection import (
            apply_role_selection_to_extra,
            recommend_active_roles,
        )
    except Exception:  # noqa: BLE001
        return session
    try:
        hint_sequence = tuple(getattr(session, "role_sequence", ()) or ())
    except Exception:  # noqa: BLE001
        hint_sequence = ()
    try:
        selection = recommend_active_roles(
            user_prompt=canonical_prompt or "",
            hint_role_sequence=hint_sequence,
        )
    except Exception:  # noqa: BLE001
        return session
    try:
        existing = dict(getattr(session, "extra", {}) or {})
    except Exception:  # noqa: BLE001
        existing = {}
    merged = apply_role_selection_to_extra(existing, selection)
    # Only forward the four selection-specific keys to _persist_extra_keys
    # so we don't accidentally rewrite unrelated extras with stale copies.
    selection_updates = {
        key: merged[key]
        for key in (
            "active_research_roles",
            "excluded_research_roles",
            "role_selection_source",
            "role_selection_reasons",
        )
        if key in merged
    }
    if not selection_updates:
        return session
    # C4 cleanup — store ``active_research_roles`` in the canonical
    # ``engineering-agent/<short>`` form so council bootstrap, status
    # diagnostic, and downstream readers see a single shape. Backward-
    # compatible: legacy short-form data still survives reads (council
    # vocabulary normalises both on input).
    try:
        from ...agents.council import normalize_roles

        active = selection_updates.get("active_research_roles")
        if isinstance(active, list):
            selection_updates["active_research_roles"] = list(
                normalize_roles(active)
            )
        excluded = selection_updates.get("excluded_research_roles")
        if isinstance(excluded, list):
            selection_updates["excluded_research_roles"] = list(
                normalize_roles(excluded)
            )
    except Exception:  # noqa: BLE001 — never block intake on canonicalisation
        pass
    return _persist_extra_keys(session, selection_updates)

def _persist_coding_session_context(
    session: Any,
    *,
    message_text: str,
    user_links: Sequence[str] = (),
) -> Any:
    """P0-H stage 2 + P0-I stage 3 — store gateway-prepared coding session context.

    Calls :func:`prepare_coding_session_context` to compute the
    work_mode / topology / scope / github_target / repo_contract /
    coding_handoff_packet extras for *session*, then merges them in.
    Additionally (P0-I) runs the **tracking enforcement validator** and
    stores the result for the status surface to display.

    Ask-once contract: if the session already has work_mode set, the
    helper does not re-prompt and does not overwrite.

    Best-effort — any failure leaves the session untouched so partial
    install / missing gh CLI never blocks intake.
    """

    if session is None:
        return session
    try:
        from yule_orchestrator.agents.coding.coding_session_context import (
            prepare_coding_session_context,
        )
    except Exception:  # noqa: BLE001 - partial install fallback
        return session

    try:
        existing_extra = dict(getattr(session, "extra", None) or {})
    except Exception:  # noqa: BLE001
        existing_extra = {}

    try:
        context = prepare_coding_session_context(
            message_text=message_text or "",
            user_links=tuple(user_links or ()),
            existing_extra=existing_extra,
            existing_session_id=getattr(session, "session_id", None),
            # discover_contract=False until vault/workspace clone wiring lands.
            # The contract still records its own fallback line in extras.
        )
    except Exception:  # noqa: BLE001
        return session

    extras_update = dict(context.extras_update or {})

    # P0-I stage 3 — tracking enforcement validation. Run it against
    # the *post-update* extras so the validator sees github_target /
    # work_mode / handoff packet that this very call just persisted.
    try:
        from yule_orchestrator.agents.coding.tracking_enforcement import (
            validate_tracking_chain,
        )

        post_update_extra = dict(existing_extra)
        post_update_extra.update(extras_update)
        tracking = validate_tracking_chain(post_update_extra)
        extras_update["tracking_validation"] = dict(tracking.to_dict())
        if tracking.blocked and tracking.blocked_reason:
            extras_update["tracking_blocked_reason"] = tracking.blocked_reason
    except Exception:  # noqa: BLE001
        pass

    if not extras_update:
        return session
    return _persist_extra_keys(session, extras_update)

def _persist_lifecycle_mode(session: Any, canonical_prompt: str) -> Any:
    """Mark *session* as research-only when the prompt signals that.

    Live regression: the gateway used to advertise an executor role
    ("실행 후보 backend-engineer") even on a request like "오늘은 코드
    수정 없이 자료 수집이 목표야". Phase 2 fixes that by stashing the
    lifecycle mode at intake so every downstream consumer (work_report
    builder, status diagnostic, member-bot research path) reads the
    same answer.

    The session.extra layout matches the spec's bullet 5:
        lifecycle_mode: "research_only" | "implementation"
        executor_role:  null when research-only
        research_leads: list[str]   roles leading the investigation

    Best-effort — any import or persistence failure leaves the session
    untouched so a partial agent layout cannot block intake.
    """

    if session is None:
        return session
    try:
        from yule_orchestrator.agents.coding.authorization import (
            LIFECYCLE_MODE_IMPLEMENTATION,
            LIFECYCLE_MODE_RESEARCH_ONLY,
            recommend_authorization,
        )
    except Exception:  # noqa: BLE001
        return session

    try:
        proposal = recommend_authorization(user_request=canonical_prompt or "")
    except Exception:  # noqa: BLE001
        return session

    if proposal.lifecycle_mode == LIFECYCLE_MODE_RESEARCH_ONLY:
        updates = {
            "lifecycle_mode": LIFECYCLE_MODE_RESEARCH_ONLY,
            "executor_role": None,
            "research_leads": list(proposal.research_leads),
        }
    else:
        updates = {
            "lifecycle_mode": LIFECYCLE_MODE_IMPLEMENTATION,
        }
    return _persist_extra_keys(session, updates)

def _work_report_to_dict(report: Any) -> dict:
    """Serialise a :class:`agents.reports.work_report.WorkReport` into a plain
    JSON-friendly dict so the workflow store can persist it under
    ``session.extra['work_report']``."""

    return {
        "session_id": getattr(report, "session_id", None),
        "title": getattr(report, "title", "") or "",
        "canonical_prompt": getattr(report, "canonical_prompt", "") or "",
        "executive_summary": getattr(report, "executive_summary", "") or "",
        "research_summary": getattr(report, "research_summary", "") or "",
        "tech_lead_recommendation": getattr(
            report, "tech_lead_recommendation", ""
        )
        or "",
        "role_decisions": dict(getattr(report, "role_decisions", {}) or {}),
        "risks": list(getattr(report, "risks", ()) or ()),
        "proposed_next_steps": list(
            getattr(report, "proposed_next_steps", ()) or ()
        ),
        "requires_code_change": bool(
            getattr(report, "requires_code_change", False)
        ),
        "recommended_executor_role": getattr(
            report, "recommended_executor_role", None
        ),
        "approval_request": getattr(report, "approval_request", None),
        "participants": list(getattr(report, "participants", ()) or ()),
        "reference_count": int(getattr(report, "reference_count", 0) or 0),
        "research_stop_reason": getattr(report, "research_stop_reason", None),
        "under_covered_roles": list(
            getattr(report, "under_covered_roles", ()) or ()
        ),
        # Phase 3 status gate fields.
        "status": getattr(report, "status", "interim"),
        "missing_roles": list(getattr(report, "missing_roles", ()) or ()),
        "has_research_pack": bool(
            getattr(report, "has_research_pack", False)
        ),
        "has_synthesis": bool(getattr(report, "has_synthesis", False)),
    }

def _persist_extra_keys(session: Any, updates: Mapping[str, object]) -> Any:
    """Merge *updates* into ``session.extra`` and persist via ``update_session``.

    Always mutates the live ``extra`` dict when one is present, so test
    fixtures using mutable dataclass stubs observe the new keys without
    having to capture the returned session. Production WorkflowSession
    is frozen — for that path we rely on ``dataclasses.replace`` +
    ``update_session`` to land the change in SQLite.

    Stabilisation Phase 1: persistence failures used to be silently
    swallowed, which made live debugging impossible. We now stamp a
    ``persistence_error`` entry on the session's live extra dict (when
    available) so the status diagnostic + supervisor can surface
    "왜 저장이 안 됐어?" without having to grep logs. The user-visible
    reply chain is still kept intact (no exception leaks past this
    helper).
    """

    try:
        from dataclasses import replace as _dc_replace
        from datetime import datetime as _dt

        from yule_orchestrator.agents.workflow_state import update_session
    except Exception as exc:  # noqa: BLE001
        _record_persistence_failure(
            session,
            step="import update_session",
            reason=str(exc),
            updates=updates,
        )
        return session

    # Try in-place mutation first so test stubs (plain dataclasses with
    # a regular dict ``extra``) observe the change directly. Production
    # WorkflowSession holds an immutable mapping; this no-ops there.
    live = getattr(session, "extra", None)
    if isinstance(live, dict):
        for key, value in updates.items():
            live[key] = value

    existing = dict(getattr(session, "extra", {}) or {})
    merged = {**existing, **dict(updates)}
    try:
        updated = _dc_replace(session, extra=merged)
    except TypeError:
        # Non-dataclass stub — in-place mutation above already covered it.
        return session
    try:
        update_session(updated, now=_dt.now().astimezone())
    except Exception as exc:  # noqa: BLE001
        _record_persistence_failure(
            updated,
            step="update_session",
            reason=str(exc),
            updates=updates,
        )
    return updated

def _record_persistence_failure(
    session: Any,
    *,
    step: str,
    reason: str,
    updates: Mapping[str, object],
) -> None:
    """Stamp a persistence failure note on the live ``session.extra``.

    Best-effort — the session.extra mutation is wrapped so even
    pathological stubs never raise out of this helper. The note keeps
    the offending step + reason + the keys that were being written so
    the diagnostic responder can show the operator exactly which
    update silently failed during the live MVP loop.
    """

    if session is None:
        return
    try:
        live = getattr(session, "extra", None)
        if isinstance(live, dict):
            live["persistence_error"] = {
                "step": step,
                "reason": reason,
                "keys": sorted(str(k) for k in (updates or {}).keys()),
            }
    except Exception:  # noqa: BLE001
        return

def _persist_thread_id(
    session: Any,
    thread_id: Optional[int],
) -> Any:
    """Write the Discord work-thread id back to ``session.thread_id``.

    MVP closure refactor: delegates to
    :func:`agents.lifecycle.persistence.persist_thread_link` so the
    router and any other caller (member-bot, supervisor cleanup)
    follow the same persistence contract — including the structured
    ``persistence_error`` stamp on failure. Behaviour is identical to
    the prior inline implementation; only the import/replace
    sequence is consolidated upstream.
    """

    from yule_orchestrator.agents.lifecycle.persistence import persist_thread_link

    result = persist_thread_link(session, thread_id)
    return result.session

# Canonical (de)serialisers now live in the agents layer — keep the old
# ``_proposal_to_dict`` / ``_proposal_from_dict`` names as aliases so existing
# importers/tests still resolve them from this module.
from yule_orchestrator.agents.coding.authorization import proposal_from_dict as _proposal_from_dict  # noqa: E402,F401
from yule_orchestrator.agents.coding.authorization import proposal_to_dict as _proposal_to_dict  # noqa: E402,F401

def _load_session_by_id(
    list_sessions_fn: Callable[..., Sequence[Any]],
    session_id: Optional[str],
) -> Optional[Any]:
    if not session_id:
        return None
    try:
        try:
            sessions = list_sessions_fn(limit=50)
        except TypeError:
            sessions = list_sessions_fn()
    except Exception:  # noqa: BLE001
        return None
    for session in sessions or ():
        if getattr(session, "session_id", None) == session_id:
            return session
    return None

def _most_recent_session(sessions: Sequence[Any]) -> Optional[Any]:
    if not sessions:
        return None

    def _sort_key(s: Any):
        ts = getattr(s, "updated_at", None)
        if ts is None:
            return (0, 0)
        try:
            epoch = ts.timestamp()
        except Exception:  # noqa: BLE001
            epoch = 0
        return (1, epoch)

    return max(sessions, key=_sort_key)



__all__ = (
    "_is_terminal",
    "_persist_coding_proposal",
    "_persist_coding_job",
    "_persist_role_selection",
    "_persist_coding_session_context",
    "_persist_lifecycle_mode",
    "_work_report_to_dict",
    "_persist_extra_keys",
    "_record_persistence_failure",
    "_persist_thread_id",
    "_proposal_to_dict",
    "_proposal_from_dict",
    "_load_session_by_id",
    "_most_recent_session",
)
