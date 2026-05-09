"""Claude decision seam — Round 4 of #73.

The autonomy producer / discussion follow-up / CI retry orchestrator
all run on a deterministic fast-path by default. But the master plan
calls for a *short-lived Claude invocation seam* — a place where the
runtime can hand a small, structured judgement off to an external
decision layer (live Claude API, hosted service, sidecar process)
*without* embedding the live provider in this repository.

This module is that seam. It defines:

  * :class:`DecisionRequest` — the strongly-typed payload the runtime
    builds and hands the port. Carries a ``kind`` so the live provider
    can pick the right prompt template.
  * :class:`DecisionResponse` — the verdict the port returns. Always
    has ``skip`` / ``advance`` / ``reason``; optional structured
    ``metadata`` for audit + downstream reasoning.
  * :class:`ClaudeDecisionPort` — the Protocol the runtime calls
    against.
  * :class:`DeterministicDecisionPort` — the *only* implementation
    landed in this PR. Always answers ``advance=True`` so the
    runtime falls through to its existing fast-path. Live providers
    are wired in a follow-up PR with explicit operator authorization.
  * :func:`compose_decision_port` — a small composer that lets a
    runtime stack multiple ports in priority order (live → cached →
    deterministic) without each port having to know about its
    neighbours.

Hard rails:

  * No live HTTP / API client lives in this module. The deterministic
    port is the default and the live wiring point is a separate file
    that the operator brings in only after auth is set up.
  * The runtime calls the port for *judgement*, never for free-form
    text generation. A misconfigured port that returns garbage falls
    back to the deterministic verdict via :func:`compose_decision_port`.
  * Per-call timeout / retry handling lives at the port level so the
    runtime callsite can stay on the fast-path; production live ports
    must implement those concerns themselves.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (
    Any,
    Callable,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    runtime_checkable,
)


logger = logging.getLogger(__name__)


__all__ = (
    "ClaudeDecisionPort",
    "DECISION_KIND_DISCUSSION_FOLLOWUP",
    "DECISION_KIND_NEXT_TASK",
    "DECISION_KIND_RETRY_GUARD",
    "DecisionRequest",
    "DecisionResponse",
    "DeterministicDecisionPort",
    "compose_decision_port",
)


# ---------------------------------------------------------------------------
# Decision-kind vocabulary
# ---------------------------------------------------------------------------


# Kept narrow on purpose — every callsite uses one of these. New kinds
# require a follow-up that updates both this constant *and* the
# deterministic port's branch table so a new kind never silently
# returns ``advance=True`` without a thought-through default.
DECISION_KIND_DISCUSSION_FOLLOWUP: str = "discussion_followup"
DECISION_KIND_NEXT_TASK: str = "next_task"
DECISION_KIND_RETRY_GUARD: str = "retry_guard"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionRequest:
    """A single judgement question handed to the decision port.

    *kind* selects the live provider's prompt template; *summary* is a
    short human-readable description of the situation; *facts* is a
    mapping the live provider can read structured fields out of (e.g.
    ``missing_roles`` for the discussion follow-up kind, ``attempt``
    for the retry guard kind).
    """

    kind: str
    summary: str
    facts: Mapping[str, Any] = field(default_factory=dict)
    session_id: Optional[str] = None
    job_id: Optional[str] = None
    requested_at: str = ""

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "kind": self.kind,
            "summary": self.summary,
            "facts": dict(self.facts),
            "session_id": self.session_id,
            "job_id": self.job_id,
            "requested_at": self.requested_at,
        }


@dataclass(frozen=True)
class DecisionResponse:
    """Verdict returned by a decision port.

    Either ``advance`` is True (runtime should keep going on the
    fast-path) or ``skip`` is True (runtime should pause / dedup /
    skip this turn). Both can be False if the port wants the runtime
    to fall back to its caller-side default — that's how the
    :class:`DeterministicDecisionPort` signals "I don't know".

    *reason* is mandatory for audit; *metadata* is freeform
    structured fields the runtime stamps on session.extra so the
    operator can see what the port decided.
    """

    skip: bool = False
    advance: bool = False
    reason: str = ""
    confidence: str = "low"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    decided_at: str = ""

    def is_actionable(self) -> bool:
        return bool(self.skip) or bool(self.advance)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ClaudeDecisionPort(Protocol):
    """Surface the runtime calls against.

    Production live ports should implement timeout / retry / circuit
    breaker semantics internally. The runtime's callsite assumes
    :meth:`decide` returns within a small, bounded time — the live
    port is the one responsible for that bound.
    """

    def decide(
        self, *, request: DecisionRequest
    ) -> DecisionResponse:  # pragma: no cover - Protocol
        ...


# ---------------------------------------------------------------------------
# Deterministic port (default)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeterministicDecisionPort:
    """The default port: never blocks, never short-circuits.

    Always answers ``advance=True`` so the runtime falls through to
    its existing deterministic logic. Live providers compose *above*
    this port via :func:`compose_decision_port` so a misconfigured
    or slow live provider falls back here without changing call-site
    code.
    """

    name: str = "deterministic"

    def decide(self, *, request: DecisionRequest) -> DecisionResponse:
        return DecisionResponse(
            advance=True,
            reason=f"deterministic fallback (kind={request.kind})",
            confidence="medium",
            metadata={"port": self.name},
            decided_at=_now_iso(),
        )


# ---------------------------------------------------------------------------
# Composer
# ---------------------------------------------------------------------------


def compose_decision_port(
    *ports: ClaudeDecisionPort,
    fallback: Optional[ClaudeDecisionPort] = None,
    name: str = "composite",
) -> ClaudeDecisionPort:
    """Stack ports in priority order; first actionable verdict wins.

    Each port is queried in order. The first port that returns an
    *actionable* :class:`DecisionResponse` (``skip`` or ``advance``
    set) wins. Ports that raise are logged + skipped. When every port
    returns a non-actionable answer, *fallback* (default:
    :class:`DeterministicDecisionPort`) provides the verdict.

    The returned port is itself a :class:`ClaudeDecisionPort`, so
    callers can compose composites recursively.
    """

    fallback_port = fallback or DeterministicDecisionPort(name=f"{name}.fallback")

    @dataclass(frozen=True)
    class _Composite:
        ports: Tuple[ClaudeDecisionPort, ...]
        fallback: ClaudeDecisionPort
        name_: str

        def decide(self, *, request: DecisionRequest) -> DecisionResponse:
            for p in self.ports:
                try:
                    response = p.decide(request=request)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "claude decision port %r raised on kind=%s",
                        getattr(p, "name", type(p).__name__),
                        request.kind,
                        exc_info=True,
                    )
                    continue
                if isinstance(response, DecisionResponse) and response.is_actionable():
                    return response
            return self.fallback.decide(request=request)

    return _Composite(
        ports=tuple(ports), fallback=fallback_port, name_=name
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()
