"""Discussion follow-up dispatcher — Round 4 of #73.

The discussion mode classifier (``agents.discussion.mode``) decides
*per turn* whether the request is discussion / research_only /
implementation_candidate / clarification_needed. The runtime takes
those decisions and turns them into actual queue rows so the
conversation keeps moving without the user having to prompt the bot
again:

  * ``discussion`` with missing role takes → enqueue a ``role_take``
    row per missing role (idempotent via
    :meth:`RoleTakeWorker.enqueue` keyed on
    ``(session_id, role, kind)``).
  * ``research_only`` → enqueue a ``research_collect`` row when the
    session doesn't already have a fresh research_pack
    (idempotent via :meth:`ResearchWorker.enqueue`).
  * ``implementation_candidate`` → leave the existing approval gate
    in charge (Round 3's :mod:`coding_execute_dispatcher` picks up
    the row once the user types the approval phrase).
  * ``clarification_needed`` → no enqueue. The follow-up dispatcher
    only stamps an idempotency marker so the producer doesn't
    re-fire on the same turn.

The dispatcher is *pure-ish*: it talks to the worker enqueue methods
(which dedup against the queue) and writes a single
``session.extra['discussion_followup']`` audit dict. It never reads
the queue directly, never spawns threads, and never blocks waiting
for I/O.

Idempotency contract:

  * ``session.extra['discussion_followup']['by_turn'][turn_id]`` is a
    dict ``{role: {job_id, dispatched_at, kind}}``. The dispatcher
    refuses to fire a second time for the same ``(turn_id, role,
    kind)`` triple.
  * Producer ticks call into :func:`dispatch_discussion_followup` on
    every iteration; the marker is what makes the call cheap when
    nothing has changed.
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

from .autonomy_producer import AutonomyDispatch, DispatchOutcome
from .next_task_selector import SOURCE_UNRESOLVED_DISCUSSION
from .role_take_worker import (
    JOB_TYPE_ROLE_TAKE,
    KIND_TURN,
    RoleTakeWorker,
)
from .research_worker import (
    JOB_TYPE_RESEARCH_COLLECT,
    ResearchWorker,
)


logger = logging.getLogger(__name__)


__all__ = (
    "DISCUSSION_FOLLOWUP_EXTRA_KEY",
    "DISCUSSION_FOLLOWUP_KIND_RESEARCH",
    "DISCUSSION_FOLLOWUP_KIND_ROLE_TAKE",
    "DiscussionFollowupDispatcher",
    "DiscussionFollowupOutcome",
    "build_discussion_followup_dispatcher",
    "dispatch_discussion_followup",
    "stamp_followup_marker",
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


DISCUSSION_FOLLOWUP_EXTRA_KEY: str = "discussion_followup"
DISCUSSION_FOLLOWUP_KIND_ROLE_TAKE: str = "role_take"
DISCUSSION_FOLLOWUP_KIND_RESEARCH: str = "research_collect"


# Mode strings the dispatcher recognises. Kept as plain literals so
# this module doesn't import :mod:`agents.discussion` (which would
# pull in the synthesizer / LLM seam stack).
_MODE_DISCUSSION: str = "discussion"
_MODE_RESEARCH_ONLY: str = "research_only"
_MODE_CLARIFICATION_NEEDED: str = "clarification_needed"
_MODE_IMPLEMENTATION_CANDIDATE: str = "implementation_candidate"


# ---------------------------------------------------------------------------
# Outcome model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiscussionFollowupOutcome:
    """One follow-up decision the dispatcher made.

    Mirrors :class:`AutonomyDispatch` so the producer can normalise
    these via :func:`autonomy_producer._normalize_dispatch` without
    knowing about discussion-mode semantics. ``mode`` carries the
    classifier verdict so the operator surface can read why a row
    was (not) enqueued.
    """

    session_id: str
    mode: str
    kind: str
    role: Optional[str] = None
    outcome: str = DispatchOutcome.DISPATCHED
    job_id: Optional[str] = None
    reason: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_autonomy_dispatch(self) -> AutonomyDispatch:
        return AutonomyDispatch(
            source=SOURCE_UNRESOLVED_DISCUSSION,
            outcome=self.outcome,
            session_id=self.session_id,
            executor_role=self.role or "",
            job_id=self.job_id,
            reason=self.reason or f"mode={self.mode} kind={self.kind}",
            payload=dict(self.payload),
        )


# ---------------------------------------------------------------------------
# Marker helpers
# ---------------------------------------------------------------------------


def _normalise_extra(extra: Optional[Mapping[str, Any]]) -> dict:
    return dict(extra or {})


def _read_marker(
    extra: Mapping[str, Any], *, turn_id: str
) -> Mapping[str, Mapping[str, Any]]:
    """Return the per-(role, kind) marker map for *turn_id*.

    Mapping shape is ``{(role, kind): {"job_id": ..., "dispatched_at": ...}}``
    flattened to a dict keyed by ``"{role}:{kind}"`` so we can serialise
    it back into ``session.extra`` without losing structure.
    """

    base = extra.get(DISCUSSION_FOLLOWUP_EXTRA_KEY)
    if not isinstance(base, Mapping):
        return {}
    by_turn = base.get("by_turn")
    if not isinstance(by_turn, Mapping):
        return {}
    bucket = by_turn.get(turn_id)
    if not isinstance(bucket, Mapping):
        return {}
    return {str(k): dict(v) if isinstance(v, Mapping) else {} for k, v in bucket.items()}


def stamp_followup_marker(
    extra: Mapping[str, Any],
    *,
    turn_id: str,
    role: Optional[str],
    kind: str,
    job_id: str,
    when: Optional[datetime] = None,
) -> Mapping[str, Any]:
    """Return a new ``session.extra`` with the follow-up marker recorded.

    Pure: the input is not mutated. Markers age out naturally — the
    map is bounded to the most recent 32 turns so a long-running
    session doesn't grow ``session.extra`` without bound.
    """

    base = dict(extra or {})
    block = base.get(DISCUSSION_FOLLOWUP_EXTRA_KEY)
    if not isinstance(block, Mapping):
        block = {}
    by_turn = dict(block.get("by_turn") or {})
    bucket = dict(by_turn.get(turn_id) or {})
    key = f"{role or ''}:{kind}"
    when_iso = (when or datetime.now(tz=timezone.utc)).replace(microsecond=0).isoformat()
    bucket[key] = {
        "job_id": job_id,
        "dispatched_at": when_iso,
        "kind": kind,
        "role": role,
    }
    by_turn[turn_id] = bucket
    if len(by_turn) > 32:
        # Drop the oldest turn buckets — turn_id is roughly chronological
        # in production (Discord message id ordering), so trimming the
        # smallest keys is a reasonable approximation of "drop oldest".
        ordered = sorted(by_turn.keys())
        for stale in ordered[: len(by_turn) - 32]:
            by_turn.pop(stale, None)
    base[DISCUSSION_FOLLOWUP_EXTRA_KEY] = {
        **block,
        "by_turn": by_turn,
        "last_dispatched_at": when_iso,
    }
    return base


def _already_dispatched(
    extra: Mapping[str, Any], *, turn_id: str, role: Optional[str], kind: str
) -> bool:
    marker = _read_marker(extra, turn_id=turn_id)
    key = f"{role or ''}:{kind}"
    entry = marker.get(key)
    if not entry:
        return False
    return bool(entry.get("job_id"))


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


@dataclass
class DiscussionFollowupDispatcher:
    """Production wiring of the discussion follow-up step.

    ``role_take_worker`` and ``research_worker`` are the only direct
    queue producers — both already idempotent. The dispatcher's job is
    to read the unresolved discussion row (produced by the gateway's
    discussion turn handler) and pick which workers to call.

    *update_session_fn* persists the marker back to the session cache.
    Tests pass a no-op; production wires it through the
    :func:`agents.workflow_state.update_session` call site.

    *load_session_fn* hydrates a fresh :class:`WorkflowSession` so the
    dispatcher's marker writes don't race against another writer.
    Defaults to ``None`` — when None the dispatcher relies on the
    in-memory session passed via the discussion row.
    """

    role_take_worker: Optional[RoleTakeWorker] = None
    research_worker: Optional[ResearchWorker] = None
    update_session_fn: Optional[Callable[..., Any]] = None
    load_session_fn: Optional[Callable[[str], Any]] = None

    def dispatch(
        self,
        *,
        session_id: str,
        discussion_row: Mapping[str, Any],
        now: Optional[datetime] = None,
        decision_port: Optional[Any] = None,
    ) -> Tuple[AutonomyDispatch, ...]:
        """Run one follow-up cycle for *session_id*.

        Returns a tuple of :class:`AutonomyDispatch` rows so the
        producer can append them straight onto its tick report.
        """

        outcomes = list(
            self._compute_outcomes(
                session_id=session_id,
                discussion_row=discussion_row,
                now=now,
                decision_port=decision_port,
            )
        )
        return tuple(o.to_autonomy_dispatch() for o in outcomes)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _compute_outcomes(
        self,
        *,
        session_id: str,
        discussion_row: Mapping[str, Any],
        now: Optional[datetime],
        decision_port: Optional[Any],
    ) -> Iterable[DiscussionFollowupOutcome]:
        if not session_id:
            return ()

        mode = str(discussion_row.get("mode") or _MODE_DISCUSSION).strip().lower()
        turn_id = str(discussion_row.get("turn_id") or discussion_row.get("thread_id") or "default")
        missing_roles = tuple(
            str(r).strip()
            for r in (discussion_row.get("missing_roles") or ())
            if str(r).strip()
        )

        # Optional Claude decision seam — when wired, the runtime can
        # ask "is this discussion truly unresolved?" before we burn
        # role_take rows on a turn that's already settled. Routes the
        # call through the seam's :func:`consult_decision_port` helper
        # so raise / wrong-type / unwired all degrade identically to
        # the retry-guard callsite, and the invocation trace lands on
        # the outcome's payload for audit.
        if decision_port is not None and mode == _MODE_DISCUSSION:
            try:
                from .claude_decision_seam import consult_decision_port
            except Exception:  # noqa: BLE001 - dispatcher must keep running
                consult_decision_port = None  # type: ignore[assignment]
            if consult_decision_port is not None:
                advice, advice_trace = consult_decision_port(
                    decision_port,
                    request=_build_decision_request(
                        session_id=session_id,
                        discussion_row=discussion_row,
                    ),
                )
                if advice.skip:
                    return (
                        DiscussionFollowupOutcome(
                            session_id=session_id,
                            mode=mode,
                            kind=DISCUSSION_FOLLOWUP_KIND_ROLE_TAKE,
                            outcome=DispatchOutcome.SKIPPED,
                            reason=advice.reason or "decision_port_skip",
                            payload={
                                "decision_invocation": dict(
                                    advice_trace.to_payload()
                                ),
                            },
                        ),
                    )

        session = self._resolve_session(session_id)
        extra = _normalise_extra(getattr(session, "extra", None))
        outcomes: List[DiscussionFollowupOutcome] = []

        if mode == _MODE_DISCUSSION:
            if not missing_roles:
                outcomes.append(
                    DiscussionFollowupOutcome(
                        session_id=session_id,
                        mode=mode,
                        kind=DISCUSSION_FOLLOWUP_KIND_ROLE_TAKE,
                        outcome=DispatchOutcome.SKIPPED,
                        reason="no missing roles on row",
                    )
                )
            else:
                for role in missing_roles:
                    outcomes.append(
                        self._dispatch_role_take(
                            session_id=session_id,
                            role=role,
                            extra=extra,
                            turn_id=turn_id,
                            now=now,
                            mode=mode,
                            payload=dict(discussion_row.get("payload") or {}),
                        )
                    )
                    extra = self._maybe_update_extra(
                        session_id=session_id,
                        outcome=outcomes[-1],
                        extra=extra,
                        turn_id=turn_id,
                        now=now,
                    )

        elif mode == _MODE_RESEARCH_ONLY:
            outcomes.append(
                self._dispatch_research(
                    session_id=session_id,
                    extra=extra,
                    turn_id=turn_id,
                    now=now,
                    mode=mode,
                    payload=dict(discussion_row.get("payload") or {}),
                )
            )
            extra = self._maybe_update_extra(
                session_id=session_id,
                outcome=outcomes[-1],
                extra=extra,
                turn_id=turn_id,
                now=now,
            )

        elif mode == _MODE_CLARIFICATION_NEEDED:
            outcomes.append(
                DiscussionFollowupOutcome(
                    session_id=session_id,
                    mode=mode,
                    kind="awaiting_user",
                    outcome=DispatchOutcome.SKIPPED,
                    reason="clarification needed — user prompt pending",
                )
            )

        elif mode == _MODE_IMPLEMENTATION_CANDIDATE:
            outcomes.append(
                DiscussionFollowupOutcome(
                    session_id=session_id,
                    mode=mode,
                    kind="awaiting_approval",
                    outcome=DispatchOutcome.SKIPPED,
                    reason="implementation candidate — owned by approval gate",
                )
            )

        else:
            outcomes.append(
                DiscussionFollowupOutcome(
                    session_id=session_id,
                    mode=mode,
                    kind="unknown",
                    outcome=DispatchOutcome.SKIPPED,
                    reason=f"unrecognised discussion mode: {mode!r}",
                )
            )

        return outcomes

    def _resolve_session(self, session_id: str) -> Any:
        if self.load_session_fn is None:
            return None
        try:
            return self.load_session_fn(session_id)
        except Exception:  # noqa: BLE001
            logger.warning(
                "discussion_followup: load_session_fn raised for %s",
                session_id,
                exc_info=True,
            )
            return None

    def _dispatch_role_take(
        self,
        *,
        session_id: str,
        role: str,
        extra: Mapping[str, Any],
        turn_id: str,
        now: Optional[datetime],
        mode: str,
        payload: Mapping[str, Any],
    ) -> DiscussionFollowupOutcome:
        if self.role_take_worker is None:
            return DiscussionFollowupOutcome(
                session_id=session_id,
                mode=mode,
                kind=DISCUSSION_FOLLOWUP_KIND_ROLE_TAKE,
                role=role,
                outcome=DispatchOutcome.SKIPPED,
                reason="role_take_worker not wired",
            )
        if _already_dispatched(
            extra,
            turn_id=turn_id,
            role=role,
            kind=DISCUSSION_FOLLOWUP_KIND_ROLE_TAKE,
        ):
            return DiscussionFollowupOutcome(
                session_id=session_id,
                mode=mode,
                kind=DISCUSSION_FOLLOWUP_KIND_ROLE_TAKE,
                role=role,
                outcome=DispatchOutcome.DEDUPED,
                reason="marker already present",
            )
        try:
            job, created = self.role_take_worker.enqueue(
                session_id=session_id,
                role=role,
                kind=KIND_TURN,
                payload=dict(payload),
                now=(now.timestamp() if isinstance(now, datetime) else None),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "discussion_followup: role_take enqueue raised", exc_info=True
            )
            return DiscussionFollowupOutcome(
                session_id=session_id,
                mode=mode,
                kind=DISCUSSION_FOLLOWUP_KIND_ROLE_TAKE,
                role=role,
                outcome=DispatchOutcome.ERROR,
                reason=f"role_take enqueue failed: {exc}",
            )
        return DiscussionFollowupOutcome(
            session_id=session_id,
            mode=mode,
            kind=DISCUSSION_FOLLOWUP_KIND_ROLE_TAKE,
            role=role,
            outcome=(
                DispatchOutcome.DISPATCHED
                if created
                else DispatchOutcome.DEDUPED
            ),
            job_id=job.job_id if job is not None else None,
            reason="" if created else "queue dedup",
        )

    def _dispatch_research(
        self,
        *,
        session_id: str,
        extra: Mapping[str, Any],
        turn_id: str,
        now: Optional[datetime],
        mode: str,
        payload: Mapping[str, Any],
    ) -> DiscussionFollowupOutcome:
        if self.research_worker is None:
            return DiscussionFollowupOutcome(
                session_id=session_id,
                mode=mode,
                kind=DISCUSSION_FOLLOWUP_KIND_RESEARCH,
                outcome=DispatchOutcome.SKIPPED,
                reason="research_worker not wired",
            )
        if _already_dispatched(
            extra,
            turn_id=turn_id,
            role=None,
            kind=DISCUSSION_FOLLOWUP_KIND_RESEARCH,
        ):
            return DiscussionFollowupOutcome(
                session_id=session_id,
                mode=mode,
                kind=DISCUSSION_FOLLOWUP_KIND_RESEARCH,
                outcome=DispatchOutcome.DEDUPED,
                reason="marker already present",
            )
        try:
            job, created = self.research_worker.enqueue(
                session_id=session_id,
                payload=dict(payload),
                now=(now.timestamp() if isinstance(now, datetime) else None),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "discussion_followup: research enqueue raised", exc_info=True
            )
            return DiscussionFollowupOutcome(
                session_id=session_id,
                mode=mode,
                kind=DISCUSSION_FOLLOWUP_KIND_RESEARCH,
                outcome=DispatchOutcome.ERROR,
                reason=f"research_collect enqueue failed: {exc}",
            )
        return DiscussionFollowupOutcome(
            session_id=session_id,
            mode=mode,
            kind=DISCUSSION_FOLLOWUP_KIND_RESEARCH,
            job_id=job.job_id if job is not None else None,
            outcome=(
                DispatchOutcome.DISPATCHED
                if created
                else DispatchOutcome.DEDUPED
            ),
            reason="" if created else "queue dedup",
        )

    def _maybe_update_extra(
        self,
        *,
        session_id: str,
        outcome: DiscussionFollowupOutcome,
        extra: Mapping[str, Any],
        turn_id: str,
        now: Optional[datetime],
    ) -> Mapping[str, Any]:
        if not outcome.job_id or outcome.outcome not in {
            DispatchOutcome.DISPATCHED,
            DispatchOutcome.DEDUPED,
        }:
            return extra
        new_extra = stamp_followup_marker(
            extra,
            turn_id=turn_id,
            role=outcome.role,
            kind=outcome.kind,
            job_id=outcome.job_id,
            when=now,
        )
        if self.update_session_fn is None:
            return new_extra
        # We don't know the session shape here — the dispatcher's
        # caller is responsible for hydrating + persisting. We just
        # call update_session_fn with the diff so production wiring
        # can decide how to merge. Failures are swallowed.
        try:
            self.update_session_fn(
                session_id=session_id,
                extra=dict(new_extra),
                now=now,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "discussion_followup: update_session_fn raised", exc_info=True
            )
        return new_extra


# ---------------------------------------------------------------------------
# Decision seam helpers
# ---------------------------------------------------------------------------


def _build_decision_request(
    *,
    session_id: str,
    discussion_row: Mapping[str, Any],
) -> Any:
    """Lift the discussion row into a decision-port request.

    Returns a :class:`DecisionRequest` so a live external port wired
    via :func:`build_decision_port_from_env` can rely on the typed
    shape (``request.kind``, ``request.facts``, …) instead of having
    to duck-type a Mapping. Falls back to the legacy plain-dict shape
    if the seam module fails to import — the dispatcher is supposed
    to be cheap and never block on import errors.
    """

    facts = {
        "mode": discussion_row.get("mode"),
        "turn_id": discussion_row.get("turn_id"),
        "missing_roles": list(discussion_row.get("missing_roles") or ()),
    }
    summary = str(discussion_row.get("summary") or "")
    try:
        from .claude_decision_seam import (
            DECISION_KIND_DISCUSSION_FOLLOWUP,
            DecisionRequest,
        )
    except Exception:  # noqa: BLE001 - dispatcher must keep working
        return {
            "kind": "discussion_followup",
            "session_id": session_id,
            "summary": summary,
            **facts,
        }
    return DecisionRequest(
        kind=DECISION_KIND_DISCUSSION_FOLLOWUP,
        summary=summary,
        facts=facts,
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------


def dispatch_discussion_followup(
    *,
    session_id: str,
    discussion_row: Mapping[str, Any],
    role_take_worker: Optional[RoleTakeWorker] = None,
    research_worker: Optional[ResearchWorker] = None,
    update_session_fn: Optional[Callable[..., Any]] = None,
    load_session_fn: Optional[Callable[[str], Any]] = None,
    now: Optional[datetime] = None,
    decision_port: Optional[Any] = None,
) -> Tuple[AutonomyDispatch, ...]:
    """Functional wrapper around :class:`DiscussionFollowupDispatcher`.

    Lets the autonomy producer plug the dispatcher in via a single
    callable without instantiating the dataclass at every wiring
    site.
    """

    dispatcher = DiscussionFollowupDispatcher(
        role_take_worker=role_take_worker,
        research_worker=research_worker,
        update_session_fn=update_session_fn,
        load_session_fn=load_session_fn,
    )
    return dispatcher.dispatch(
        session_id=session_id,
        discussion_row=discussion_row,
        now=now,
        decision_port=decision_port,
    )


def build_discussion_followup_dispatcher(
    *,
    role_take_worker: Optional[RoleTakeWorker] = None,
    research_worker: Optional[ResearchWorker] = None,
    update_session_fn: Optional[Callable[..., Any]] = None,
    load_session_fn: Optional[Callable[[str], Any]] = None,
) -> Callable[..., Tuple[AutonomyDispatch, ...]]:
    """Factory returning a callable matching ``followup_dispatch``.

    The autonomy producer expects a callable signature
    ``f(*, session_id, discussion_row, now, decision_port)``. This
    factory binds the worker dependencies once and returns that
    callable so the producer doesn't have to know about the
    dispatcher class.
    """

    dispatcher = DiscussionFollowupDispatcher(
        role_take_worker=role_take_worker,
        research_worker=research_worker,
        update_session_fn=update_session_fn,
        load_session_fn=load_session_fn,
    )

    def _call(
        *,
        session_id: str,
        discussion_row: Mapping[str, Any],
        now: Optional[datetime] = None,
        decision_port: Optional[Any] = None,
    ) -> Tuple[AutonomyDispatch, ...]:
        return dispatcher.dispatch(
            session_id=session_id,
            discussion_row=discussion_row,
            now=now,
            decision_port=decision_port,
        )

    return _call
