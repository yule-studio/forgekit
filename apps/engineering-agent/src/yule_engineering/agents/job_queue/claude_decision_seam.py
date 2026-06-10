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
  * :class:`DeterministicDecisionPort` — the safe-default port.
    Always answers ``advance=True`` so the runtime falls through to
    its existing fast-path. Lives at the bottom of every composed
    chain.
  * :class:`RecordOnlyDecisionPort` — a *shadow-mode* port that
    captures every :class:`DecisionRequest` (in memory and optionally
    appended as JSONL to a file) but always returns a non-actionable
    verdict so the chain falls through to the next port. Used to
    audit what an operator-wired live provider *would* be asked
    before granting it real authority.
  * :class:`ExternalDecisionPort` — wraps an externally-injected
    callable (the spot a follow-up PR plugs the live Claude API /
    hosted decision sidecar into). It owns timeout + raise → fallback
    semantics so the runtime callsite never has to. The callable is
    handed in via :func:`build_decision_port_from_env`'s
    ``external_callable_factory`` argument; this module never imports
    a live HTTP client itself.
  * :func:`compose_decision_port` — a small composer that lets a
    runtime stack multiple ports in priority order (external → record
    → deterministic) without each port having to know about its
    neighbours.
  * :func:`build_decision_port_from_env` — env-driven composer used
    by ``run_service.py`` to construct the supervisor's decision
    port without each callsite having to spell the priority chain
    out by hand. The env contract is documented on the function.

Hard rails:

  * No live HTTP / API client lives in this module. The
    :class:`ExternalDecisionPort` is intentionally a thin adapter
    around an injected callable — the live wiring lives in a
    separate file the operator brings in only after auth is set up.
  * The runtime calls the port for *judgement*, never for free-form
    text generation. A misconfigured port that returns garbage falls
    back to the deterministic verdict via :func:`compose_decision_port`.
  * Per-call timeout / retry handling lives at the port level so the
    runtime callsite can stay on the fast-path; production live ports
    must implement those concerns themselves.
  * The env factory will *only* surface the live tier when the
    operator has explicitly opted in via
    ``YULE_CLAUDE_DECISION_PROVIDER`` *and* supplied a callable
    factory. Missing either falls back to deterministic-only.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any,
    Callable,
    List,
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
    "DECISION_KIND_IMPLEMENTATION_CANDIDATE",
    "DECISION_KIND_NEXT_TASK",
    "DECISION_KIND_RETRY_GUARD",
    "DEFAULT_EXTERNAL_TIMEOUT_SECONDS",
    "DEFAULT_RECORD_BUFFER_SIZE",
    "DEFAULT_PROVIDER_CHAIN",
    "DecisionInvocationTrace",
    "DecisionPortBuildTrace",
    "DecisionRequest",
    "DecisionResponse",
    "DeterministicDecisionPort",
    "ENV_CLAUDE_DECISION_EXTERNAL_TIMEOUT",
    "ENV_CLAUDE_DECISION_PROVIDER",
    "ENV_CLAUDE_DECISION_RECORD_BUFFER",
    "ENV_CLAUDE_DECISION_RECORD_PATH",
    "ExternalDecisionPort",
    "PROVIDER_DETERMINISTIC",
    "PROVIDER_EXTERNAL",
    "PROVIDER_RECORD",
    "RecordOnlyDecisionPort",
    "build_decision_port_from_env",
    "coerce_decision_request",
    "compose_decision_port",
    "consult_decision_port",
)


# ---------------------------------------------------------------------------
# Decision-kind vocabulary
# ---------------------------------------------------------------------------


# Kept narrow on purpose — every callsite uses one of these. New kinds
# require a follow-up that updates both this constant *and* the
# deterministic port's branch table so a new kind never silently
# returns ``advance=True`` without a thought-through default.
DECISION_KIND_DISCUSSION_FOLLOWUP: str = "discussion_followup"
DECISION_KIND_IMPLEMENTATION_CANDIDATE: str = "implementation_candidate"
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
# Record-only port (shadow mode)
# ---------------------------------------------------------------------------


# A record-only port has to keep some bound on memory growth otherwise a
# long-lived runtime that never opts in to a live provider keeps growing
# its in-memory ring buffer forever. 256 entries is enough for an
# operator to read back "what got asked in the last hour" without making
# the supervisor process noticeable in heap snapshots.
DEFAULT_RECORD_BUFFER_SIZE: int = 256


@dataclass
class RecordOnlyDecisionPort:
    """Capture every :class:`DecisionRequest` without making a verdict.

    Returns a non-actionable :class:`DecisionResponse` so the
    :func:`compose_decision_port` chain falls through to the next port
    (typically :class:`DeterministicDecisionPort`). Useful as a
    *shadow-mode* port: an operator wires it above the deterministic
    fallback to audit which decisions a future live provider would be
    asked, without granting a live provider any authority yet.

    *jsonl_path* (optional): when set, every recorded request is also
    appended to that file as one JSON line. Failures opening / writing
    the file are logged but never raised — record-only is non-critical
    so its bugs must never derail the runtime.
    *buffer_size*: in-memory ring buffer cap; oldest entries drop when
    the buffer overflows.
    """

    name: str = "record-only"
    jsonl_path: Optional[Path] = None
    buffer_size: int = DEFAULT_RECORD_BUFFER_SIZE
    _buffer: List[Mapping[str, Any]] = field(default_factory=list, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def decide(self, *, request: DecisionRequest) -> DecisionResponse:
        coerced = coerce_decision_request(request)
        entry = dict(coerced.to_payload())
        entry["_recorded_at"] = _now_iso()
        with self._lock:
            self._buffer.append(entry)
            overflow = len(self._buffer) - max(1, int(self.buffer_size))
            if overflow > 0:
                del self._buffer[:overflow]
        if self.jsonl_path is not None:
            self._append_jsonl(entry)
        # Non-actionable on purpose — shadow mode hands the verdict
        # back to the next port in the composed chain.
        return DecisionResponse(
            advance=False,
            skip=False,
            reason=f"record-only shadow capture (kind={coerced.kind})",
            confidence="none",
            metadata={"port": self.name, "shadow": True},
            decided_at=_now_iso(),
        )

    def recorded(self) -> Tuple[Mapping[str, Any], ...]:
        """Snapshot of the in-memory ring buffer.

        Returns a tuple so the caller can iterate without risking a
        concurrent mutation from the recorder thread. Operator
        dashboards / tests use this to verify what the runtime *would*
        have asked a live provider.
        """

        with self._lock:
            return tuple(self._buffer)

    def _append_jsonl(self, entry: Mapping[str, Any]) -> None:
        try:
            self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            with self.jsonl_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True))
                fh.write("\n")
        except Exception:  # noqa: BLE001 - record-only must never raise
            logger.warning(
                "record-only decision port: jsonl append failed for %s",
                self.jsonl_path,
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# External (live-ready) port
# ---------------------------------------------------------------------------


# 5 s is comfortably under the autonomy producer's 30 s tick budget so
# even back-to-back stalls leave headroom. Live providers should also
# implement their own timeouts; this is a defence-in-depth bound.
DEFAULT_EXTERNAL_TIMEOUT_SECONDS: float = 5.0


# Type alias for the callable a live provider returns. Kept loose
# (positional Mapping out, keyword DecisionRequest in) so a follow-up
# PR can plug a Claude API client / hosted decision sidecar / unix
# socket client without changing the seam.
ExternalDecisionCallable = Callable[..., Any]


@dataclass
class ExternalDecisionPort:
    """Adapter around an externally-injected decision callable.

    The seam never imports a live HTTP / API client. Instead, this
    port wraps a *callable* the operator (or follow-up PR) hands in
    via :func:`build_decision_port_from_env`'s
    ``external_callable_factory`` argument. The callable must accept
    ``request: DecisionRequest`` as a keyword argument and return one
    of:

      * a :class:`DecisionResponse`
      * a Mapping that :meth:`DecisionResponse` can be constructed from
      * ``None`` (treated as non-actionable so the chain falls through)

    Anything else is logged + treated as non-actionable. The port also
    catches every exception the callable raises so a misbehaving live
    provider can never crash the runtime callsite.

    *timeout_seconds* is documented for the live callable; the port
    itself does not enforce it (we don't want to spawn a watchdog
    thread per call). Live callables MUST honour the timeout — the
    port surfaces it on the request as a fact for the live prompt.
    """

    name: str = "external"
    callable: Optional[ExternalDecisionCallable] = None
    timeout_seconds: float = DEFAULT_EXTERNAL_TIMEOUT_SECONDS

    def decide(self, *, request: DecisionRequest) -> DecisionResponse:
        if self.callable is None:
            return DecisionResponse(
                advance=False,
                skip=False,
                reason="external port not configured",
                confidence="none",
                metadata={"port": self.name, "configured": False},
                decided_at=_now_iso(),
            )
        coerced = coerce_decision_request(request)
        try:
            raw = self.callable(
                request=coerced,
                timeout_seconds=self.timeout_seconds,
            )
        except TypeError:
            # Callable doesn't accept timeout_seconds — fall back to
            # the bare request signature so live adapters don't have
            # to grow a kwarg they don't use.
            try:
                raw = self.callable(request=coerced)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "external decision port %r raised on kind=%s",
                    self.name, coerced.kind, exc_info=True,
                )
                return self._fallback_response(coerced, reason="external_raise")
        except Exception:  # noqa: BLE001
            logger.warning(
                "external decision port %r raised on kind=%s",
                self.name, coerced.kind, exc_info=True,
            )
            return self._fallback_response(coerced, reason="external_raise")

        return self._normalise(raw, coerced)

    def _normalise(
        self,
        raw: Any,
        request: DecisionRequest,
    ) -> DecisionResponse:
        if raw is None:
            return self._fallback_response(request, reason="external_none")
        if isinstance(raw, DecisionResponse):
            return raw
        if isinstance(raw, Mapping):
            try:
                return DecisionResponse(
                    skip=bool(raw.get("skip", False)),
                    advance=bool(raw.get("advance", False)),
                    reason=str(raw.get("reason") or "external"),
                    confidence=str(raw.get("confidence") or "low"),
                    metadata=dict(raw.get("metadata") or {"port": self.name}),
                    decided_at=str(raw.get("decided_at") or _now_iso()),
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "external decision port %r returned malformed mapping for kind=%s",
                    self.name, request.kind, exc_info=True,
                )
                return self._fallback_response(request, reason="external_malformed")
        logger.warning(
            "external decision port %r returned unsupported type %s for kind=%s",
            self.name, type(raw).__name__, request.kind,
        )
        return self._fallback_response(request, reason="external_bad_type")

    def _fallback_response(
        self, request: DecisionRequest, *, reason: str
    ) -> DecisionResponse:
        return DecisionResponse(
            advance=False,
            skip=False,
            reason=reason,
            confidence="none",
            metadata={"port": self.name, "fallback": True},
            decided_at=_now_iso(),
        )


# ---------------------------------------------------------------------------
# Env-driven composition
# ---------------------------------------------------------------------------


PROVIDER_EXTERNAL: str = "external"
PROVIDER_RECORD: str = "record"
PROVIDER_DETERMINISTIC: str = "deterministic"


# Deterministic-only by default keeps every installation safe until the
# operator explicitly opts a record / external tier in.
DEFAULT_PROVIDER_CHAIN: Tuple[str, ...] = (PROVIDER_DETERMINISTIC,)


# Env contract — kept narrow on purpose. Each key has exactly one
# observable effect documented next to it.
ENV_CLAUDE_DECISION_PROVIDER: str = "YULE_CLAUDE_DECISION_PROVIDER"
ENV_CLAUDE_DECISION_RECORD_PATH: str = "YULE_CLAUDE_DECISION_RECORD_PATH"
ENV_CLAUDE_DECISION_RECORD_BUFFER: str = "YULE_CLAUDE_DECISION_RECORD_BUFFER"
ENV_CLAUDE_DECISION_EXTERNAL_TIMEOUT: str = (
    "YULE_CLAUDE_DECISION_EXTERNAL_TIMEOUT_SECONDS"
)


@dataclass(frozen=True)
class DecisionPortBuildTrace:
    """Audit trail returned alongside the composed port.

    The supervisor logs this so an operator who reads
    ``yule run-service`` stdout can see exactly which tiers were
    enabled, which ones were skipped (and why), and which deterministic
    fallback owns the bottom of the chain.
    """

    requested: Tuple[str, ...]
    enabled: Tuple[str, ...]
    skipped: Tuple[Tuple[str, str], ...] = ()
    fallback: str = PROVIDER_DETERMINISTIC


def coerce_decision_request(request: Any) -> DecisionRequest:
    """Best-effort conversion of ``request`` into a :class:`DecisionRequest`.

    Lets callsites keep building loose ``Mapping`` payloads (the
    discussion follow-up dispatcher does this for legacy reasons)
    while the seam's typed ports still receive the dataclass shape
    they document. Unknown / partial mappings produce an empty
    ``DecisionRequest`` with whatever fields are present.
    """

    if isinstance(request, DecisionRequest):
        return request
    if isinstance(request, Mapping):
        kind = str(request.get("kind") or "")
        summary = str(request.get("summary") or "")
        # If the mapping has no ``facts`` key but does carry the
        # discussion-row payload, treat the remaining keys as facts so
        # downstream prompts still see them. Drops the well-known
        # envelope keys so they don't double up.
        envelope_keys = {"kind", "summary", "session_id", "job_id", "requested_at", "facts"}
        explicit_facts = request.get("facts")
        if isinstance(explicit_facts, Mapping):
            facts = dict(explicit_facts)
        else:
            facts = {k: v for k, v in request.items() if k not in envelope_keys}
        return DecisionRequest(
            kind=kind,
            summary=summary,
            facts=facts,
            session_id=request.get("session_id"),
            job_id=request.get("job_id"),
            requested_at=str(request.get("requested_at") or ""),
        )
    raise TypeError(
        f"cannot coerce {type(request).__name__!r} into DecisionRequest"
    )


def build_decision_port_from_env(
    *,
    env: Optional[Mapping[str, str]] = None,
    external_callable_factory: Optional[
        Callable[[Mapping[str, str]], Optional[ExternalDecisionCallable]]
    ] = None,
) -> Tuple[ClaudeDecisionPort, DecisionPortBuildTrace]:
    """Compose a decision port from env, returning the port + audit trace.

    Env contract:

      * ``YULE_CLAUDE_DECISION_PROVIDER`` — comma-separated provider
        chain in priority order. Tokens: ``external``, ``record``,
        ``deterministic``. Unset / empty → deterministic-only. The
        deterministic tier is implied as the terminal fallback even
        when the operator omits it from the list.
      * ``YULE_CLAUDE_DECISION_RECORD_PATH`` — optional file the
        record-only port appends a JSONL audit line to. Unset →
        in-memory only.
      * ``YULE_CLAUDE_DECISION_RECORD_BUFFER`` — optional in-memory
        ring buffer cap. Unset → :data:`DEFAULT_RECORD_BUFFER_SIZE`.
        Bounded to a safe range so an operator typo can't blow heap.
      * ``YULE_CLAUDE_DECISION_EXTERNAL_TIMEOUT_SECONDS`` — optional
        timeout the external port surfaces to its callable. Unset →
        :data:`DEFAULT_EXTERNAL_TIMEOUT_SECONDS`.

    *external_callable_factory* is the seam: production wiring (a
    follow-up PR) hands in a callable that talks to the live provider.
    Without a factory the ``external`` tier is logged as skipped and
    the chain still composes (just with one fewer tier).
    """

    source: Mapping[str, str] = env if env is not None else os.environ

    raw_chain = (source.get(ENV_CLAUDE_DECISION_PROVIDER) or "").strip()
    if not raw_chain:
        requested: Tuple[str, ...] = DEFAULT_PROVIDER_CHAIN
    else:
        requested = tuple(
            tok.strip().lower()
            for tok in raw_chain.split(",")
            if tok.strip()
        )

    ports: List[ClaudeDecisionPort] = []
    enabled: List[str] = []
    skipped: List[Tuple[str, str]] = []
    fallback_name = PROVIDER_DETERMINISTIC

    for token in requested:
        if token == PROVIDER_EXTERNAL:
            port = _build_external_from_env(
                source=source, factory=external_callable_factory
            )
            if port is None:
                skipped.append((PROVIDER_EXTERNAL, "no callable factory or factory returned None"))
                continue
            ports.append(port)
            enabled.append(PROVIDER_EXTERNAL)
        elif token == PROVIDER_RECORD:
            ports.append(_build_record_from_env(source))
            enabled.append(PROVIDER_RECORD)
        elif token == PROVIDER_DETERMINISTIC:
            # Skip — the composer's fallback handles deterministic.
            # Recording it keeps the trace honest about operator intent.
            enabled.append(PROVIDER_DETERMINISTIC)
        else:
            skipped.append((token, "unknown provider token"))

    composite = compose_decision_port(
        *ports,
        fallback=DeterministicDecisionPort(name=fallback_name),
        name="env-composed",
    )
    trace = DecisionPortBuildTrace(
        requested=requested,
        enabled=tuple(enabled),
        skipped=tuple(skipped),
        fallback=fallback_name,
    )
    return composite, trace


def _build_record_from_env(source: Mapping[str, str]) -> RecordOnlyDecisionPort:
    raw_path = (source.get(ENV_CLAUDE_DECISION_RECORD_PATH) or "").strip()
    jsonl_path: Optional[Path] = Path(raw_path) if raw_path else None

    raw_buffer = (source.get(ENV_CLAUDE_DECISION_RECORD_BUFFER) or "").strip()
    buffer_size = DEFAULT_RECORD_BUFFER_SIZE
    if raw_buffer:
        try:
            parsed = int(raw_buffer)
        except ValueError:
            parsed = DEFAULT_RECORD_BUFFER_SIZE
        # Clamp to a sane band: at least 1 entry, at most 4k so a typo
        # can't pin a few MB of JSON in memory.
        buffer_size = max(1, min(4096, parsed))
    return RecordOnlyDecisionPort(
        jsonl_path=jsonl_path,
        buffer_size=buffer_size,
    )


def _build_external_from_env(
    *,
    source: Mapping[str, str],
    factory: Optional[
        Callable[[Mapping[str, str]], Optional[ExternalDecisionCallable]]
    ],
) -> Optional[ExternalDecisionPort]:
    if factory is None:
        return None
    try:
        callable_ = factory(source)
    except Exception:  # noqa: BLE001 - factory misconfig must not crash
        logger.warning(
            "external_callable_factory raised; external decision port disabled",
            exc_info=True,
        )
        return None
    if callable_ is None:
        return None

    raw_timeout = (source.get(ENV_CLAUDE_DECISION_EXTERNAL_TIMEOUT) or "").strip()
    timeout = DEFAULT_EXTERNAL_TIMEOUT_SECONDS
    if raw_timeout:
        try:
            parsed_t = float(raw_timeout)
        except ValueError:
            parsed_t = DEFAULT_EXTERNAL_TIMEOUT_SECONDS
        # Clamp 0.1 ≤ t ≤ 30.0 — anything wider is operator typo
        # territory; live providers should not stall an autonomy tick
        # for more than a handful of seconds.
        timeout = max(0.1, min(30.0, parsed_t))
    return ExternalDecisionPort(callable=callable_, timeout_seconds=timeout)


# ---------------------------------------------------------------------------
# Per-call invocation helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionInvocationTrace:
    """Per-call audit trace returned by :func:`consult_decision_port`.

    Where :class:`DecisionPortBuildTrace` answers "which tiers were
    composed at startup", this answers "what happened on *this* call":
    which port the runtime asked, what verdict came back, whether the
    callsite is going to act on it, and the exact reason / metadata
    surface the operator sees.

    Callers stamp this onto :class:`AutonomyDispatch` payload (or the
    discussion follow-up outcome) so a dashboard reading the dispatch
    log can answer "why did the runtime skip this retry?" without
    having to re-run the port.
    """

    kind: str
    actionable: bool
    skip: bool
    advance: bool
    reason: str
    confidence: str
    provider: str
    fell_through: bool
    raised: bool
    raised_type: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    decided_at: str = ""

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "kind": self.kind,
            "actionable": self.actionable,
            "skip": self.skip,
            "advance": self.advance,
            "reason": self.reason,
            "confidence": self.confidence,
            "provider": self.provider,
            "fell_through": self.fell_through,
            "raised": self.raised,
            "raised_type": self.raised_type,
            "metadata": dict(self.metadata),
            "decided_at": self.decided_at,
        }


def consult_decision_port(
    port: Optional[ClaudeDecisionPort],
    *,
    request: DecisionRequest,
) -> Tuple[DecisionResponse, DecisionInvocationTrace]:
    """Single entry point every callsite uses to ask the seam.

    Centralises the call → coerce → guard pattern so a callsite never
    has to spell out "try / on raise warn / on non-DecisionResponse
    fall through" by hand. Returns a *pair*:

      * the :class:`DecisionResponse` the chain produced (always typed,
        never None) — callers branch on ``.is_actionable()`` and
        ``.skip`` / ``.advance``.
      * a :class:`DecisionInvocationTrace` capturing what happened so
        the callsite can stamp it on its outcome row for audit.

    Hard guarantees:

      * Never raises. A None port, a port that raises, and a port that
        returns the wrong type all surface as a non-actionable
        :class:`DecisionResponse` plus a trace with ``raised`` /
        ``fell_through`` flagged.
      * The trace's ``provider`` field comes from the response's
        ``metadata['port']`` (or ``metadata['provider']``) so an
        operator reading the audit log can tell which tier — the live
        subprocess, the record-only shadow, or the deterministic
        fallback — actually answered.

    The seam composer (:func:`compose_decision_port`) is the right
    place to assemble the chain; this helper is the right place to
    *call* it.
    """

    coerced = coerce_decision_request(request)

    if port is None:
        response = DecisionResponse(
            advance=False,
            skip=False,
            reason="decision_port_unwired",
            confidence="none",
            metadata={"port": "unwired", "fallback": True},
            decided_at=_now_iso(),
        )
        return response, _build_trace(
            response=response,
            kind=coerced.kind,
            fell_through=True,
            raised=False,
            raised_type="",
        )

    try:
        response = port.decide(request=coerced)
    except Exception as exc:  # noqa: BLE001 - never crash the callsite
        logger.warning(
            "consult_decision_port: port raised on kind=%s",
            coerced.kind,
            exc_info=True,
        )
        response = DecisionResponse(
            advance=False,
            skip=False,
            reason=f"decision_port_raised:{type(exc).__name__}",
            confidence="none",
            metadata={"port": "raised", "fallback": True},
            decided_at=_now_iso(),
        )
        return response, _build_trace(
            response=response,
            kind=coerced.kind,
            fell_through=True,
            raised=True,
            raised_type=type(exc).__name__,
        )

    if not isinstance(response, DecisionResponse):
        # Defensive: the Protocol promises a typed response but we
        # never want to take an arbitrary object's word for it.
        response = DecisionResponse(
            advance=False,
            skip=False,
            reason="decision_port_bad_type",
            confidence="none",
            metadata={"port": "bad_type", "fallback": True},
            decided_at=_now_iso(),
        )
        return response, _build_trace(
            response=response,
            kind=coerced.kind,
            fell_through=True,
            raised=False,
            raised_type="",
        )

    return response, _build_trace(
        response=response,
        kind=coerced.kind,
        fell_through=not response.is_actionable(),
        raised=False,
        raised_type="",
    )


def _build_trace(
    *,
    response: DecisionResponse,
    kind: str,
    fell_through: bool,
    raised: bool,
    raised_type: str,
) -> DecisionInvocationTrace:
    metadata = dict(response.metadata or {})
    provider = str(
        metadata.get("port")
        or metadata.get("provider")
        or "unknown"
    )
    return DecisionInvocationTrace(
        kind=kind,
        actionable=response.is_actionable(),
        skip=bool(response.skip),
        advance=bool(response.advance),
        reason=str(response.reason or ""),
        confidence=str(response.confidence or ""),
        provider=provider,
        fell_through=fell_through,
        raised=raised,
        raised_type=raised_type,
        metadata=metadata,
        decided_at=str(response.decided_at or _now_iso()),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()
