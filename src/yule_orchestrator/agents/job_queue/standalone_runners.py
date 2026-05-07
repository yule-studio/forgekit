"""Production runners for the standalone queue workers — A-M6.1a.

The in-process gateway path uses ``ResearchWorker.run_one`` /
``RoleTakeWorker.run_one`` with a closure that knows the request
context (channel, message, session). The standalone worker process
doesn't have that context — it has only the queued job. These
runners bridge the gap:

  * :func:`build_research_runner` — reload the session, replay the
    collector with the role/prompt the producer stamped on the
    job's payload, persist the resulting research_pack +
    collection_outcome onto session.extra. Forum publish + user
    follow_up message stay on the in-process gateway path until
    M6.2 wires them onto a separate "publish" job; that's a
    follow_up issue, not a regression — the work the gateway does
    today still runs because of M3's in-process ``run_one``.

  * :func:`build_role_take_runner` — reload the session, route to
    the right body (open-call / chained turn / synthesis) based
    on the job's ``payload['kind']``, and return the legacy
    :class:`ResearchTurnOutcome` shape. Member-bot side picks the
    outcome up via session.extra (already wired by M4's queue
    routing).

Both runners are *pure* — no Discord client, no message channel.
They take a :class:`Job` and return an outcome the supervisor /
status diagnostic surfaces. Discord-side rendering stays on the
gateway side until M6.2.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Mapping, Optional


logger = logging.getLogger(__name__)


# Tests inject these to avoid touching the workflow store / collector.
SessionLoader = Callable[[str], Optional[Any]]
PackLoader = Callable[[Any], Any]


# ---------------------------------------------------------------------------
# Research runner
# ---------------------------------------------------------------------------


def build_research_runner(
    *,
    session_loader: Optional[SessionLoader] = None,
    collect_fn: Optional[Callable[..., Any]] = None,
    persist_fn: Optional[Callable[..., Any]] = None,
) -> Callable[[Any], Awaitable[Any]]:
    """Return an async runner suitable for
    :meth:`ResearchWorker.process_job`'s ``runner`` arg.

    *session_loader* / *collect_fn* / *persist_fn* default to the
    real workflow store + collector + persistence. Tests inject
    stubs so the runner can be exercised without touching SQLite.

    The runner returns whatever ``collect_fn`` produced —
    typically a :class:`CollectionOutcome`. The worker's
    ``process_job`` body wraps that in the queue's state-machine
    transitions and stashes a summary onto ``result_json``.
    """

    async def _runner(job: Any) -> Any:
        payload: Mapping[str, Any] = job.payload or {}
        session_id = job.session_id
        if not session_id:
            raise RuntimeError(
                "research_collect job missing session_id"
            )

        loader = session_loader or _default_session_loader
        session = loader(session_id)
        if session is None:
            raise RuntimeError(
                f"session {session_id!r} not found in workflow store"
            )

        role = payload.get("role_for_research") or "tech-lead"
        prompt = (
            payload.get("prompt_excerpt")
            or getattr(session, "prompt", "")
            or ""
        )

        collect = collect_fn or _default_collect
        outcome = collect(
            role=role,
            prompt=prompt,
            session_id=session_id,
            task_type=getattr(session, "task_type", None),
            user_links=tuple(
                getattr(session, "references_user", ()) or ()
            ),
        )

        persist = persist_fn or _default_persist_research_state
        try:
            persist(session=session, outcome=outcome)
        except Exception:  # noqa: BLE001 - persistence is observability
            logger.warning(
                "research runner: persist_research_state failed",
                exc_info=True,
            )
        return outcome

    return _runner


def _default_session_loader(session_id: str):
    from ..workflow_state import load_session

    return load_session(session_id)


def _default_collect(**kwargs):
    from ..research.collector import auto_collect_or_request_more_input

    return auto_collect_or_request_more_input(**kwargs)


def _default_persist_research_state(*, session: Any, outcome: Any) -> None:
    """Stash the outcome's pack + collection_outcome onto session.extra.

    The gateway path already does this via
    ``persist_research_pack_state`` in the lifecycle module; we
    delegate so the standalone runner produces the same on-disk
    shape the existing in-process flow does.
    """

    try:
        from ..lifecycle.persistence import persist_research_pack_state
    except Exception:  # noqa: BLE001 - partial install fallback
        return
    try:
        persist_research_pack_state(
            session,
            research_pack=getattr(outcome, "pack", None),
            collection_outcome=getattr(outcome, "collection_outcome", outcome),
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "research runner: persist_research_pack_state raised",
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Role-take runner
# ---------------------------------------------------------------------------


def build_role_take_runner(
    *,
    session_loader: Optional[SessionLoader] = None,
    pack_loader: Optional[PackLoader] = None,
    open_call_fn: Optional[Callable[..., Any]] = None,
    persist_outcome_fn: Optional[Callable[..., Any]] = None,
) -> Callable[[Any], Any]:
    """Return a sync runner for :meth:`RoleTakeWorker.process_job`.

    Open-call kind is the path the live operator drives most often
    today (M4 already routes ``[research-open:<sid>]`` markers
    through the queue). Chained ``turn`` and ``synthesis`` kinds
    are accepted but currently delegate to the same open-call body
    via the gateway-side resolver — a fuller split lands in M6.2.

    Sync (not async) because the underlying render functions
    (``_build_open_call_outcome`` / ``deliberation_role_turn``) are
    sync. :class:`RoleTakeWorker.process_job` already handles
    async-or-sync runners via direct call.
    """

    def _runner(job: Any) -> Any:
        payload: Mapping[str, Any] = job.payload or {}
        session_id = job.session_id
        role = job.role
        kind = payload.get("kind")

        if not session_id:
            raise RuntimeError("role_take job missing session_id")
        if not role:
            raise RuntimeError("role_take job missing role")

        loader = session_loader or _default_session_loader
        session = loader(session_id)
        if session is None:
            raise RuntimeError(
                f"session {session_id!r} not found for role_take"
            )

        # M6.1a only wires the open-call path through the standalone
        # runner because that's the scope M4's queue routing already
        # exercises end-to-end. Chained turns and synthesis still
        # rely on the gateway-side body until M6.2; if a producer
        # enqueues one of those kinds for the standalone worker we
        # surface that as failed_retryable so the supervisor sees
        # the gap rather than silently producing an empty take.
        if kind != "open" and kind is not None:
            raise RuntimeError(
                f"role_take kind={kind!r} not supported in standalone worker yet "
                "(M6.2 will wire chained turn/synthesis)"
            )

        pack_loader_fn = pack_loader or (lambda _s: None)
        open_fn = open_call_fn or _default_build_open_call_outcome
        outcome = open_fn(
            role=role,
            session_id=session_id,
            session=session,
            pack_loader=pack_loader_fn,
        )

        persist = persist_outcome_fn or _default_persist_role_take
        try:
            persist(session=session, outcome=outcome, kind=kind or "open")
        except Exception:  # noqa: BLE001 - observability only
            logger.warning(
                "role_take runner: persist_role_take_outcome raised",
                exc_info=True,
            )
        return outcome

    return _runner


def _default_build_open_call_outcome(*, role: str, session_id: str, session: Any, pack_loader):
    from ...discord.engineering_team_runtime import _build_open_call_outcome

    return _build_open_call_outcome(
        role=role,
        session_id=session_id,
        session=session,
        pack_loader=pack_loader,
    )


def _default_persist_role_take(*, session: Any, outcome: Any, kind: str) -> None:
    """Stash a small "role take complete" marker onto session.extra.

    The legacy ``record_role_turn_event`` writer already keeps a
    role_turns record per role — we let the member bot's post path
    handle that side. Here we only mirror what the gateway already
    does post-render: stamp ``last_role_take[<role>]`` so the
    status diagnostic / supervisor surface sees the standalone
    worker's contribution without re-loading the queue row.
    """

    if outcome is None or not hasattr(outcome, "role"):
        return
    try:
        from dataclasses import replace as _replace
        from ..workflow_state import update_session
        from datetime import datetime as _dt
        from datetime import timezone as _tz
    except Exception:  # noqa: BLE001
        return

    extra = dict(getattr(session, "extra", None) or {})
    bucket = dict(extra.get("last_role_take") or {})
    bucket[outcome.role] = {
        "kind": kind,
        "session_id": getattr(outcome, "session_id", None),
        "is_synthesis": bool(getattr(outcome, "is_synthesis", False)),
    }
    extra["last_role_take"] = bucket
    try:
        updated = _replace(session, extra=extra)
    except TypeError:
        return
    try:
        update_session(updated, now=_dt.now(tz=_tz.utc))
    except Exception:  # noqa: BLE001
        pass


__all__ = (
    "build_research_runner",
    "build_role_take_runner",
)
