"""Production runners for the standalone queue workers — A-M6.1a.

A-M11 layers an optional :class:`RoleRunner` dispatcher on top of the
open/turn paths so the role's Discord post can come from a real LLM
backend (Claude / Codex / Ollama) when one is configured. The
deterministic body still drives the outcome whenever the dispatcher
returns an inactive-role / fallback / error status, so wiring the
dispatcher in is purely additive.

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
import os
from dataclasses import replace as _dc_replace
from typing import Any, Awaitable, Callable, Mapping, Optional


logger = logging.getLogger(__name__)

# Opt-in: compact long previous_decisions + reference-mode source_context on the
# role-runner input assembly hot path. Default off so existing behaviour is
# byte-for-byte unchanged; protected regions (recent K + decision/synthesis) are
# never folded (token_budget.compact_decisions).
ENV_RUNNER_INPUT_COMPACTION = "YULE_RUNNER_INPUT_COMPACTION_ENABLED"
_RUNNER_INPUT_COMPACTION_THRESHOLD = 1200
_RUNNER_INPUT_KEEP_RECENT = 4


def _runner_input_compaction_enabled() -> bool:
    return (os.environ.get(ENV_RUNNER_INPUT_COMPACTION) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# Tests inject these to avoid touching the workflow store / collector.
SessionLoader = Callable[[str], Optional[Any]]
PackLoader = Callable[[Any], Any]
# (session, RoleRunnerInput) → RoleRunnerOutput. Built by the caller
# via :func:`agents.runners.build_role_runner_dispatcher`.
RoleRunnerDispatch = Callable[[Any, Any], Any]


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
    turn_call_fn: Optional[Callable[..., Any]] = None,
    synthesis_call_fn: Optional[Callable[..., Any]] = None,
    persist_outcome_fn: Optional[Callable[..., Any]] = None,
    synthesis_queue_factory: Optional[Callable[[], Any]] = None,
    synthesis_audit_persist_fn: Optional[Callable[..., bool]] = None,
    role_runner_dispatch: Optional[RoleRunnerDispatch] = None,
) -> Callable[[Any], Any]:
    """Return a sync runner for :meth:`RoleTakeWorker.process_job`.

    Dispatches on ``payload['kind']``:

      * ``open`` (default) — fresh per-role take. M4 routes
        ``[research-open:<sid>]`` markers through this body.
      * ``turn`` — chained next-role take in the deliberation
        sequence. Replays previous role takes deterministically and
        appends the next-role dispatch directive.
      * ``synthesis`` — tech-lead synthesis closing the chain.
        Reuses persisted synthesis text when present, otherwise
        re-runs synthesis over replayed takes.

    A-M6.1a wired only the open-call path; A-M6.2 brings chained
    turn + synthesis into the standalone worker so the engineering
    runtime can run end-to-end without the gateway holding a
    closure for each kind. Tests inject ``open_call_fn`` /
    ``turn_call_fn`` / ``synthesis_call_fn`` to drive each branch
    without ``engineering_team_runtime`` imports.

    A-M11: when *role_runner_dispatch* is supplied, the open and
    turn bodies layer a configured-LLM take on top of the
    deterministic outcome. The deterministic message is preserved
    (``runner_role_take`` artifact) so the audit trail is intact —
    the LLM text wins as the visible message only when the
    dispatcher reports ``status="ok"``. Inactive roles, deterministic
    fallback, and runner errors all keep the legacy message
    untouched. Synthesis is never re-rendered through the dispatcher
    because the synthesis path already has its own M7 fallback
    automation.

    Sync (not async) because every underlying render function in
    ``engineering_team_runtime`` is sync.
    :class:`RoleTakeWorker.process_job` already tolerates either
    shape via direct call.
    """

    def _runner(job: Any) -> Any:
        payload: Mapping[str, Any] = job.payload or {}
        session_id = job.session_id
        role = job.role
        kind = payload.get("kind") or "open"

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

        pack_loader_fn = pack_loader or (lambda _s: None)
        if kind == "open":
            open_fn = open_call_fn or _default_build_open_call_outcome
            outcome = open_fn(
                role=role,
                session_id=session_id,
                session=session,
                pack_loader=pack_loader_fn,
            )
            outcome = _maybe_apply_role_runner(
                outcome=outcome,
                dispatch=role_runner_dispatch,
                role=role,
                session_id=session_id,
                session=session,
                pack_loader=pack_loader_fn,
                kind="open",
                payload=payload,
            )
        elif kind == "turn":
            turn_fn = turn_call_fn or _default_build_turn_outcome
            outcome = turn_fn(
                role=role,
                session_id=session_id,
                session=session,
                pack_loader=pack_loader_fn,
                payload=payload,
            )
            outcome = _maybe_apply_role_runner(
                outcome=outcome,
                dispatch=role_runner_dispatch,
                role=role,
                session_id=session_id,
                session=session,
                pack_loader=pack_loader_fn,
                kind="turn",
                payload=payload,
            )
        elif kind == "synthesis":
            synth_fn = synthesis_call_fn or _default_build_synthesis_outcome
            synth_kwargs: dict = {
                "role": role,
                "session_id": session_id,
                "session": session,
                "pack_loader": pack_loader_fn,
            }
            # Forward the M7.2 fallback wiring only to the default
            # implementation — custom synthesis_call_fn injected by
            # tests / future profiles keeps the simple 4-arg shape.
            if synthesis_call_fn is None:
                synth_kwargs["queue_factory"] = synthesis_queue_factory
                synth_kwargs["audit_persist_fn"] = synthesis_audit_persist_fn
            outcome = synth_fn(**synth_kwargs)
        else:
            raise RuntimeError(
                f"role_take kind={kind!r} not supported by standalone worker"
            )

        persist = persist_outcome_fn or _default_persist_role_take
        try:
            persist(session=session, outcome=outcome, kind=kind)
        except Exception:  # noqa: BLE001 - observability only
            logger.warning(
                "role_take runner: persist_role_take_outcome raised",
                exc_info=True,
            )
        return outcome

    return _runner


def _default_build_open_call_outcome(*, role: str, session_id: str, session: Any, pack_loader):
    from ..engineering_team_runtime import _build_open_call_outcome

    return _build_open_call_outcome(
        role=role,
        session_id=session_id,
        session=session,
        pack_loader=pack_loader,
    )


def _default_build_turn_outcome(
    *,
    role: str,
    session_id: str,
    session: Any,
    pack_loader,
    payload: Mapping[str, Any],
):
    """Mirror the gateway's ``_turn_runner`` body inside the
    standalone worker.

    ``payload['effective_role']`` is set by the gateway when the
    chained dispatch was directed at a different role than the
    member bot answering — without it we fall back to the job's
    ``role`` so direct producers (CLI replays / tests) still work.
    """

    from ..engineering_team_runtime import (
        RESEARCH_SYNTHESIS_ROLE,
        ResearchTurnOutcome,
        _maybe_load_pack,
        _next_research_role,
        _replay_role_takes_until,
        _role_address,
        deliberation_research_role_sequence,
        deliberation_role_turn,
        research_dispatch_directive,
    )

    sequence = deliberation_research_role_sequence(session)
    effective_role = str(
        (payload or {}).get("effective_role") or role
    )
    if effective_role not in sequence:
        # Producer error — synthesis role or unknown role asked for
        # a chained turn. Surface as None so the worker stamps the
        # row SAVED with no ``runner_result`` (gateway side stays
        # quiet, supervisor diagnostic shows the row).
        return None

    research_pack = _maybe_load_pack(pack_loader, session)
    previous_turns = _replay_role_takes_until(
        session, sequence, effective_role, research_pack
    )
    _take, rendered = deliberation_role_turn(
        session,
        _role_address(effective_role),
        research_pack=research_pack,
        previous_turns=previous_turns,
    )
    next_role = _next_research_role(sequence, effective_role)
    if next_role is None:
        next_directive = research_dispatch_directive(
            session_id, RESEARCH_SYNTHESIS_ROLE
        )
    else:
        next_directive = research_dispatch_directive(session_id, next_role)
    message = rendered
    if next_directive:
        message = f"{rendered}\n\n{next_directive}"
    return ResearchTurnOutcome(
        role=role,
        session_id=session_id,
        message=message,
        next_directive=next_directive,
        is_synthesis=False,
    )


def _default_build_synthesis_outcome(
    *,
    role: str,
    session_id: str,
    session: Any,
    pack_loader,
    queue_factory: Optional[Callable[[], Any]] = None,
    audit_persist_fn: Optional[Callable[..., bool]] = None,
):
    """Mirror the gateway's ``_synthesis_runner`` body, with M7.2
    degrade / fallback automation layered on top.

    Pipeline (each branch returns a :class:`ResearchTurnOutcome`):

      1. **Scan** the role_take queue for the session and classify
         each expected role into completed / failed / pending /
         missing via :func:`runtime.fallback.scan_role_take_results`.

      2. **All-role fallback** — every expected role hit
         ``FAILED_TERMINAL`` (no pending retry, no completed take).
         Build a deterministic template synthesis, persist a
         fallback audit, and return the outcome. The synthesis
         dataclass carries ``approval_required=True`` so the M5b
         obsidian writer guard refuses to auto-save the content.

      3. **Degraded** — at least one role failed terminally but
         others completed (and no retry is pending). Run normal
         synthesis, prepend the degrade banner naming the failed
         + missing roles, persist a degrade audit record.

      4. **Pending retry** — at least one role is in
         ``FAILED_RETRYABLE``. We do NOT trigger fallback; the
         retry has not run yet. Synthesis proceeds without a
         banner so the operator sees the partial state without us
         prematurely committing to a fallback.

      5. **Default** — no failures. Cached synthesis text wins;
         otherwise replay role takes and synthesize fresh.

    *queue_factory* / *audit_persist_fn* default to production
    wiring; tests inject stubs to drive the pipeline without a
    live SQLite or workflow_state cache. Failures inside the
    fallback pipeline (queue read raised, audit write raised)
    fall back silently to the legacy synthesis path so an
    observability bug never prevents a synthesis from going out.
    """

    from ..engineering_team_runtime import (
        ResearchTurnOutcome,
        _load_synthesis_text_from_session_extra,
        _maybe_load_pack,
        _replay_role_takes,
        deliberation_research_role_sequence,
        synthesize_thread,
    )

    research_pack = _maybe_load_pack(pack_loader, session)
    expected_roles = deliberation_research_role_sequence(session)

    scan = _safe_scan_role_take_results(
        queue_factory=queue_factory,
        session_id=session_id,
        expected_roles=expected_roles,
    )

    # --- All-role fallback ------------------------------------------------
    if scan is not None and scan.all_terminally_failed:
        from ...runtime.fallback import (
            FALLBACK_AUTHORITY_DETERMINISTIC_TEMPLATE,
            build_deterministic_fallback_synthesis,
            build_fallback_audit_record,
        )

        try:
            _synth, rendered = build_deterministic_fallback_synthesis(
                session=session,
                expected_roles=expected_roles,
                research_pack=research_pack,
            )
        except Exception:  # noqa: BLE001 — fallback must not crash synthesis
            logger.warning(
                "synthesis runner: deterministic fallback raised, "
                "falling back to legacy synthesis path",
                exc_info=True,
            )
        else:
            record = build_fallback_audit_record(
                session_id=session_id,
                notice=scan.to_degrade_notice(),
                authority=FALLBACK_AUTHORITY_DETERMINISTIC_TEMPLATE,
            )
            _safe_persist_fallback_audit(audit_persist_fn, record)
            return ResearchTurnOutcome(
                role=role,
                session_id=session_id,
                message=rendered,
                next_directive=None,
                is_synthesis=True,
            )

    # --- Default + degraded synthesis path -------------------------------
    synthesis_text = _load_synthesis_text_from_session_extra(session)
    if not synthesis_text:
        accumulated = _replay_role_takes(
            session, expected_roles, research_pack
        )
        _synth, synthesis_text = synthesize_thread(
            session, accumulated, research_pack=research_pack
        )

    if scan is not None and scan.degrade_required:
        from ...runtime.fallback import (
            FALLBACK_AUTHORITY_DEGRADED_SYNTHESIS,
            build_fallback_audit_record,
            render_degraded_synthesis_text,
        )

        notice = scan.to_degrade_notice()
        synthesis_text = render_degraded_synthesis_text(
            base_text=synthesis_text, notice=notice
        )
        record = build_fallback_audit_record(
            session_id=session_id,
            notice=notice,
            authority=FALLBACK_AUTHORITY_DEGRADED_SYNTHESIS,
        )
        _safe_persist_fallback_audit(audit_persist_fn, record)

    return ResearchTurnOutcome(
        role=role,
        session_id=session_id,
        message=synthesis_text,
        next_directive=None,
        is_synthesis=True,
    )


def _safe_scan_role_take_results(
    *,
    queue_factory: Optional[Callable[[], Any]],
    session_id: str,
    expected_roles: Any,
):
    """Best-effort scanner invocation.

    Production wiring: lazy-construct a :class:`JobQueue` against
    the default db path. Tests pass an explicit *queue_factory*
    that returns a temp-DB-backed queue. Scanner / queue exceptions
    return ``None`` so the caller falls through to the legacy
    synthesis path — degrade automation never blocks synthesis.
    """

    from ...runtime.fallback import scan_role_take_results

    try:
        if queue_factory is None:
            from .store import JobQueue

            queue = JobQueue()
        else:
            queue = queue_factory()
        return scan_role_take_results(
            queue=queue,
            session_id=session_id,
            expected_roles=expected_roles,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "synthesis runner: role-take scan raised; degrade "
            "automation skipped for this turn",
            exc_info=True,
        )
        return None


def _safe_persist_fallback_audit(
    audit_persist_fn: Optional[Callable[..., bool]],
    record: Any,
) -> None:
    """Best-effort persistence call. Always returns None; the caller
    treats audit as observability, not load-bearing data.
    """

    from ...runtime.fallback import persist_fallback_audit

    persist = audit_persist_fn or persist_fallback_audit
    try:
        persist(record)
    except Exception:  # noqa: BLE001
        logger.warning(
            "synthesis runner: fallback audit persist raised",
            exc_info=True,
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


# ---------------------------------------------------------------------------
# Role runner integration (A-M11)
# ---------------------------------------------------------------------------


def _maybe_apply_role_runner(
    *,
    outcome: Any,
    dispatch: Optional[RoleRunnerDispatch],
    role: str,
    session_id: str,
    session: Any,
    pack_loader: Callable[[Any], Any],
    kind: str,
    payload: Mapping[str, Any],
) -> Any:
    """Layer a configured-runner take on top of the deterministic outcome.

    Returns *outcome* unchanged when:

      * ``dispatch`` is None (no runner wired — production CLI path).
      * The dispatcher's gate excludes the role (inactive role).
      * The dispatcher returns ``status`` other than ``"ok"``.
      * The outcome is None (legacy producer signaled "skip").
      * Any unexpected failure inside the runner integration —
        deterministic fallback must never be derailed by a runner
        misconfiguration.

    On a successful runner take, returns a copy of the outcome with
    ``message`` swapped for the runner text and ``runner_provenance``
    metadata stamped onto :class:`ResearchTurnOutcome` when the
    dataclass supports it. Older outcome shapes (which lack the
    field) keep flowing through with the message swap only — the
    audit trail still names the provider via the dispatcher's
    audit_writer.
    """

    if dispatch is None or outcome is None:
        return outcome

    try:
        input_ = _build_role_runner_input(
            role=role,
            session_id=session_id,
            session=session,
            pack_loader=pack_loader,
            kind=kind,
            payload=payload,
        )
    except Exception:  # noqa: BLE001 - input prep must not break body
        logger.warning(
            "role_take runner: building RoleRunnerInput raised; "
            "deterministic outcome preserved",
            exc_info=True,
        )
        return outcome

    try:
        runner_output = dispatch(session, input_)
    except Exception:  # noqa: BLE001 - dispatcher must not raise
        logger.warning(
            "role_take runner: role-runner dispatch raised; "
            "deterministic outcome preserved",
            exc_info=True,
        )
        return outcome

    status = getattr(runner_output, "status", None)
    text = getattr(runner_output, "text", "") or ""
    if status != "ok" or not text.strip():
        # Inactive role / fallback / error → leave deterministic
        # outcome untouched. The dispatcher already audited the
        # decision.
        return outcome

    provider = getattr(runner_output, "provider", None) or "?"
    new_message = (
        f"{text.rstrip()}\n\n"
        f"_provider: {provider}_"
    )
    try:
        return _dc_replace(outcome, message=new_message)
    except TypeError:
        # Outcome is not a dataclass — fall back to mutating a copy
        # via attribute setattr where possible. If even that fails,
        # surrender to the legacy outcome to avoid silent breakage.
        try:
            setattr(outcome, "message", new_message)
        except Exception:  # noqa: BLE001
            return outcome
        return outcome


def _build_role_runner_input(
    *,
    role: str,
    session_id: str,
    session: Any,
    pack_loader: Callable[[Any], Any],
    kind: str,
    payload: Mapping[str, Any],
) -> Any:
    """Translate session + pack into a :class:`RoleRunnerInput`.

    Imports the runner module lazily so installs without the runners
    package available (early bootstrap, partial deploy) do not fail
    just because the import chain loads ``standalone_runners``.
    """

    from ..runners.role_runner import RoleRunnerInput

    prompt = (getattr(session, "prompt", "") or "").strip()
    role_profile = _safe_role_profile(role)
    topic_memory = _safe_topic_memory(session)
    source_context = _safe_source_context(session=session, pack_loader=pack_loader)
    previous_decisions = _safe_previous_decisions(session)

    metadata = {
        "kind": kind,
        "task_type": getattr(session, "task_type", None),
        "effective_role": payload.get("effective_role") if payload else None,
    }

    # Conservative capability inference (LLM-minimization Phase C): only stamp a
    # capability_class when the (role, task) signal is unambiguous. Anything
    # unclear is left unset so behaviour is unchanged.
    inferred = _infer_capability_class(
        role=role, kind=kind, task_type=metadata.get("task_type")
    )
    if inferred:
        metadata["capability_class"] = inferred

    # Token-efficiency hot path (opt-in): fold long previous_decisions and carry
    # source_context as references. Protected region (recent K + decision /
    # synthesis) is preserved by token_budget.compact_decisions.
    if _runner_input_compaction_enabled():
        source_context, previous_decisions, eff = _slim_runner_input(
            source_context, previous_decisions
        )
        if eff:
            metadata["token_efficiency"] = eff

    return RoleRunnerInput(
        role=role,
        session_id=session_id,
        prompt=prompt,
        role_profile=role_profile,
        topic_memory=topic_memory,
        source_context=source_context,
        previous_decisions=previous_decisions,
        metadata=metadata,
    )


def _infer_capability_class(
    *, role: str, kind: str, task_type: Optional[str]
) -> Optional[str]:
    """Conservative role/task → capability_class. None when unclear.

    Only the unambiguous cases are inferred (LLM-minimization Phase C); the rest
    keep the current behaviour (no capability → llm_required default).
    """

    short = (role or "").split("/", 1)[-1].strip().lower()
    tt = (task_type or "").strip().lower()
    if short == "security-engineer":
        return "security_gate"
    if short == "qa-engineer" and ("test" in tt or "qa" in tt):
        return "verification"
    return None


def _slim_runner_input(
    source_context: Mapping[str, Any],
    previous_decisions: tuple,
) -> tuple:
    """Apply deterministic slimming; never raises. Returns (src, prev, metrics).

    metrics carries previous_decisions_saved / source_context_saved /
    compaction_applied so the dispatch receipt + benchmark can show evidence.
    """

    try:
        from ..harness.token_budget import compact_decisions, reference_sources
    except Exception:  # noqa: BLE001 - slimming is best-effort
        return source_context, previous_decisions, {}

    metrics: dict = {}
    try:
        comp = compact_decisions(
            list(previous_decisions),
            threshold_tokens=_RUNNER_INPUT_COMPACTION_THRESHOLD,
            keep_recent=_RUNNER_INPUT_KEEP_RECENT,
        )
        previous_decisions = tuple(comp.decisions)
        metrics["previous_decisions_saved"] = comp.saved_tokens
        metrics["compaction_applied"] = comp.applied
        metrics["folded_decisions"] = comp.folded_count
    except Exception:  # noqa: BLE001
        logger.warning("runner input compaction failed; using full decisions", exc_info=True)

    try:
        ref = reference_sources(source_context or {})
        if ref.saved_tokens > 0:
            source_context = ref.slim
            metrics["source_context_saved"] = ref.saved_tokens
    except Exception:  # noqa: BLE001
        logger.warning("runner source reference-mode failed; using full source", exc_info=True)

    return source_context, previous_decisions, metrics


def _safe_role_profile(role: str) -> Mapping[str, Any]:
    try:
        from yule_agent_runtime.policies import role_policy_for
    except Exception:  # noqa: BLE001 - partial install fallback
        return {"role": role}
    try:
        # Roles arrive in short form ("ai-engineer"). The policy
        # registry keys long form ("engineering-agent/ai-engineer")
        # too, so we try both.
        candidates = [role, f"engineering-agent/{role}"]
        for candidate in candidates:
            policy = role_policy_for(candidate)
            if policy is not None:
                return {
                    "role": role,
                    "role_id": getattr(policy, "role_id", candidate),
                    "short_name": getattr(policy, "short_name", role),
                    "memory_role_filter": getattr(
                        policy, "memory_role_filter", None
                    ),
                    "preferred_source_kinds": list(
                        getattr(policy, "preferred_source_kinds", ()) or ()
                    ),
                    "preferred_note_kinds": list(
                        getattr(policy, "preferred_note_kinds", ()) or ()
                    ),
                    "description": getattr(policy, "description", ""),
                }
    except Exception:  # noqa: BLE001 - registry optional
        pass
    return {"role": role}


def _safe_topic_memory(session: Any) -> Mapping[str, Any]:
    try:
        from ..lifecycle.research_topic import read_topic_ledger
    except Exception:  # noqa: BLE001
        return {}
    try:
        record = read_topic_ledger(session)
    except Exception:  # noqa: BLE001
        return {}
    if record is None:
        return {}
    try:
        payload = record.to_payload()  # type: ignore[attr-defined]
        return dict(payload or {})
    except Exception:  # noqa: BLE001
        return {}


def _safe_source_context(
    *, session: Any, pack_loader: Callable[[Any], Any]
) -> Mapping[str, Any]:
    pack: Any = None
    try:
        pack = pack_loader(session)
    except Exception:  # noqa: BLE001
        pack = None
    if pack is None:
        return {}
    title = (getattr(pack, "title", "") or "").strip()
    summary = (getattr(pack, "summary", "") or "").strip()
    sources = getattr(pack, "sources", None) or ()
    excerpts: list = []
    if isinstance(sources, (list, tuple)):
        for item in list(sources)[:5]:
            label = (
                getattr(item, "title", None)
                or getattr(item, "url", None)
                or str(item)
            )
            text = str(label).strip()
            if text:
                excerpts.append(text)
    out = {}
    if title:
        out["title"] = title
    if summary:
        out["summary"] = summary
    if excerpts:
        out["sources"] = excerpts
    return out


def _safe_previous_decisions(session: Any) -> tuple:
    extra = getattr(session, "extra", None)
    if not isinstance(extra, Mapping):
        return ()
    bucket = extra.get("role_takes")
    if not isinstance(bucket, Mapping):
        return ()
    out: list = []
    for role_key, take in bucket.items():
        if not isinstance(take, Mapping):
            continue
        out.append({
            "role": str(role_key),
            "summary": str(take.get("summary") or take.get("message") or ""),
        })
    return tuple(out)


apply_role_runner_to_outcome = _maybe_apply_role_runner


__all__ = (
    "apply_role_runner_to_outcome",
    "build_research_runner",
    "build_role_take_runner",
)
