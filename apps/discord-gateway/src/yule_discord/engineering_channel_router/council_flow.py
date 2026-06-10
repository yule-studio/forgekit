"""Engineering channel router → council bootstrap glue (C2).

This module exists so ``main.py`` stays the *orchestration* file and
does not absorb council details. It owns one responsibility: take a
session that was just created at intake, call
:func:`agents.council_bootstrap.bootstrap_council`, and stash the
resulting payload onto ``session.extra`` through
:func:`session_persistence._persist_extra_keys`.

Hard contract:

- Best-effort. Every exception is swallowed — intake / kickoff /
  research_loop / work_report 흐름이 council 실패로 막히면 안 된다.
- Idempotent. Already-bootstrapped sessions (
  :func:`agents.council_bootstrap.already_bootstrapped`) are passed
  through unchanged.
- Pure persistence — no Discord I/O, no LLM call. Provider × seat
  matrix is C3-and-later.
"""

from __future__ import annotations

from typing import Any, Sequence

from .session_persistence import _persist_extra_keys


def _coerce_active_roles(session: Any) -> Sequence[str]:
    """Pull ``active_research_roles`` off ``session.extra`` with safe fallbacks.

    Order of preference:
    1. ``session.extra['active_research_roles']`` (set by
       ``_persist_role_selection`` at intake).
    2. ``session.role_sequence`` (legacy dispatcher).
    3. ``("engineering-agent/tech-lead",)`` — single-role council so the
       lifecycle substage still advances rather than getting stuck.
    """

    if session is None:
        return ()
    try:
        extra = dict(getattr(session, "extra", {}) or {})
    except Exception:  # noqa: BLE001
        extra = {}
    candidate = extra.get("active_research_roles")
    if isinstance(candidate, (list, tuple)) and candidate:
        return tuple(str(r) for r in candidate if str(r).strip())
    try:
        seq = tuple(getattr(session, "role_sequence", ()) or ())
    except Exception:  # noqa: BLE001
        seq = ()
    if seq:
        return tuple(str(r) for r in seq if str(r).strip())
    return ("engineering-agent/tech-lead",)


def _coerce_work_mode(session: Any) -> str | None:
    try:
        extra = dict(getattr(session, "extra", {}) or {})
    except Exception:  # noqa: BLE001
        return None
    value = extra.get("work_mode")
    if isinstance(value, str) and value.strip():
        return value
    return None


def _research_pack_ref(session: Any) -> str | None:
    try:
        extra = dict(getattr(session, "extra", {}) or {})
    except Exception:  # noqa: BLE001
        return None
    if extra.get("research_pack"):
        return "session.extra.research_pack"
    forum_url = extra.get("research_forum_thread_url")
    if isinstance(forum_url, str) and forum_url.strip():
        return forum_url
    return None


def _stamp_bootstrap_error(session: Any, reason: str) -> Any:
    """Best-effort stamp of ``council_bootstrap_error`` on session.extra.

    Status diagnostic surfaces this so the operator sees *why* the
    council never started even though intake / kickoff completed. The
    hard-rail "silent swallow" still holds — intake / kickoff / research
    loop are never blocked.
    """

    try:
        from yule_engineering.agents.lifecycle.council_substage import (
            COUNCIL_BOOTSTRAP_ERROR_EXTRA_KEY,
        )
    except Exception:  # noqa: BLE001
        return session
    try:
        return _persist_extra_keys(
            session, {COUNCIL_BOOTSTRAP_ERROR_EXTRA_KEY: reason}
        )
    except Exception:  # noqa: BLE001
        live = getattr(session, "extra", None)
        if isinstance(live, dict):
            live[COUNCIL_BOOTSTRAP_ERROR_EXTRA_KEY] = reason
        return session


def maybe_bootstrap_council(
    session: Any,
    *,
    canonical_prompt: str,
) -> Any:
    """Top-level entry — call after intake + role_selection + thread kickoff.

    Best-effort: returns *session* even on failure (so the caller can
    chain). Stamps ``task_brief`` / ``role_work_orders`` /
    ``role_councils`` / ``lifecycle_substage`` on success. On failure
    paths writes a 1-line reason to ``session.extra
    ['council_bootstrap_error']`` so the status diagnostic can surface
    *why* the council never started.
    """

    if session is None:
        return session

    # Lazy imports — keeps the router import graph free of agents.council
    # for any session that never reaches this code path (e.g. routing
    # short-circuits before intake).
    try:
        from yule_engineering.agents.council_bootstrap import (
            already_bootstrapped,
            bootstrap_council,
            persist_bootstrap_to_session,
        )
    except Exception as exc:  # noqa: BLE001
        return _stamp_bootstrap_error(
            session, f"council module import failed: {exc}"
        )

    try:
        existing_extra = dict(getattr(session, "extra", {}) or {})
    except Exception:  # noqa: BLE001
        existing_extra = {}

    if already_bootstrapped(existing_extra):
        return session

    session_id = getattr(session, "session_id", None)
    if not session_id:
        return _stamp_bootstrap_error(session, "session_id missing")

    active_roles = _coerce_active_roles(session)
    if not active_roles:
        return _stamp_bootstrap_error(
            session, "no active_research_roles and no role_sequence fallback"
        )

    try:
        bootstrap = bootstrap_council(
            session_id=str(session_id),
            canonical_prompt=canonical_prompt or "",
            active_roles=active_roles,
            work_mode=_coerce_work_mode(session),
            research_pack_ref=_research_pack_ref(session),
        )
    except Exception as exc:  # noqa: BLE001 — never block intake
        return _stamp_bootstrap_error(session, f"bootstrap raised: {exc}")

    try:
        return persist_bootstrap_to_session(
            session,
            bootstrap,
            persist_extra_keys=_persist_extra_keys,
        )
    except Exception as exc:  # noqa: BLE001
        return _stamp_bootstrap_error(session, f"persist failed: {exc}")


def apply_signoff_to_session(
    session: Any,
    *,
    signoff: Any,
) -> Any:
    """Stamp the tech-lead signoff onto the session.

    Best-effort silent-swallow with the same diagnostic stamp pattern
    used elsewhere in this glue module. ``signoff`` is duck-typed (any
    object with ``status`` / ``rationale`` / ``conditions`` /
    ``signed_off_by`` / ``signed_off_at``) so callers can import the
    real :class:`agents.council.TechLeadSignoff` without circular deps.
    """

    if session is None:
        return session
    try:
        from yule_engineering.agents.council_approval import apply_tech_lead_signoff
    except Exception as exc:  # noqa: BLE001
        return _stamp_bootstrap_error(
            session, f"signoff import failed: {exc}"
        )
    try:
        extra = dict(getattr(session, "extra", {}) or {})
    except Exception:  # noqa: BLE001
        return session
    try:
        updates = apply_tech_lead_signoff(extra, signoff)
    except Exception as exc:  # noqa: BLE001
        return _stamp_bootstrap_error(session, f"signoff apply raised: {exc}")
    try:
        return _persist_extra_keys(session, dict(updates))
    except Exception as exc:  # noqa: BLE001
        return _stamp_bootstrap_error(session, f"signoff persist failed: {exc}")


def draft_approval_packet_for_session(
    session: Any,
    *,
    signoff: Any = None,
    executor_role: str | None = None,
    write_scope: tuple = (),
    forbidden_scope: tuple = (),
    test_strategy: str = "",
    rollback_plan: str = "",
    operator_requests: tuple = (),
) -> Any:
    """Draft an :class:`ApprovalPacket` for the session.

    Reads council state off ``session.extra`` and runs
    :func:`council_approval.draft_packet_from_session_extra`. On success
    the packet payload + ``tech_lead_signoff`` + substage transition to
    ``approval_packet_drafted`` are stamped. On block / failure a
    bootstrap-error reason is stamped so the status surface explains
    *why* nothing was drafted.
    """

    if session is None:
        return session
    try:
        from yule_engineering.agents.council_approval import draft_packet_from_session_extra
    except Exception as exc:  # noqa: BLE001
        return _stamp_bootstrap_error(
            session, f"packet import failed: {exc}"
        )
    try:
        extra = dict(getattr(session, "extra", {}) or {})
    except Exception:  # noqa: BLE001
        return session
    try:
        outcome = draft_packet_from_session_extra(
            extra,
            signoff=signoff,
            executor_role=executor_role,
            write_scope=write_scope,
            forbidden_scope=forbidden_scope,
            test_strategy=test_strategy,
            rollback_plan=rollback_plan,
            operator_requests=operator_requests,
        )
    except Exception as exc:  # noqa: BLE001
        return _stamp_bootstrap_error(session, f"packet draft raised: {exc}")
    if not outcome.created:
        return _stamp_bootstrap_error(
            session,
            f"packet not drafted: {outcome.block_reason or 'unknown'}",
        )
    try:
        return _persist_extra_keys(session, dict(outcome.extras_update))
    except Exception as exc:  # noqa: BLE001
        return _stamp_bootstrap_error(session, f"packet persist failed: {exc}")


def post_approval_surface_for_session(session: Any) -> Any:
    """Stamp the gateway surface payload + advance substage to
    ``approval_surface_posted``.

    Assumes :func:`draft_approval_packet_for_session` has already stamped
    the packet. The actual Discord card render lives in the gateway —
    this helper exposes only the payload contract.
    """

    if session is None:
        return session
    try:
        from yule_engineering.agents.council_approval import (
            post_gateway_surface,
            read_approval_packet,
        )
    except Exception as exc:  # noqa: BLE001
        return _stamp_bootstrap_error(
            session, f"surface import failed: {exc}"
        )
    try:
        extra = dict(getattr(session, "extra", {}) or {})
    except Exception:  # noqa: BLE001
        return session
    packet = read_approval_packet(extra)
    if packet is None:
        return _stamp_bootstrap_error(
            session, "surface: approval_packet missing"
        )
    try:
        updates = post_gateway_surface(packet)
    except Exception as exc:  # noqa: BLE001
        return _stamp_bootstrap_error(
            session, f"surface build raised: {exc}"
        )
    try:
        return _persist_extra_keys(session, dict(updates))
    except Exception as exc:  # noqa: BLE001
        return _stamp_bootstrap_error(
            session, f"surface persist failed: {exc}"
        )


def advance_council_for_role(
    session: Any,
    *,
    role: str,
    requested_status: Any = None,
    requested_disagreement: str | None = None,
) -> Any:
    """Drive the next council round for *role* on an already-bootstrapped
    session.

    Best-effort. Mirrors the C2 pattern of
    :func:`maybe_bootstrap_council` — silent swallow with a status
    surface trail on failure.
    """

    if session is None:
        return session
    try:
        from yule_engineering.agents.council_bootstrap import (
            advance_council_round,
        )
    except Exception as exc:  # noqa: BLE001
        return _stamp_bootstrap_error(
            session, f"advance_council_round import failed: {exc}"
        )

    try:
        extra = dict(getattr(session, "extra", {}) or {})
    except Exception:  # noqa: BLE001
        return session

    try:
        result = advance_council_round(
            extra,
            role=role,
            requested_status=requested_status,
            requested_disagreement=requested_disagreement,
        )
    except Exception as exc:  # noqa: BLE001
        return _stamp_bootstrap_error(session, f"advance raised: {exc}")

    if result is None:
        return _stamp_bootstrap_error(
            session, "advance_council_round: brief or work order missing"
        )

    try:
        return _persist_extra_keys(session, dict(result.extras_update))
    except Exception as exc:  # noqa: BLE001
        return _stamp_bootstrap_error(session, f"advance persist failed: {exc}")


__all__ = [
    "maybe_bootstrap_council",
    "advance_council_for_role",
    "apply_signoff_to_session",
    "draft_approval_packet_for_session",
    "post_approval_surface_for_session",
]
