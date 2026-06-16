"""Role-runner interface — A-M11.

Bridges the engineering-agent's per-role speaking turns to a real LLM
backend (Claude / Codex / Ollama) when one is configured, and degrades
to a deterministic template fallback otherwise.

Design:

  * :class:`RoleRunnerInput` packages the four context channels the
    spec requires (role profile, topic memory, source context, previous
    decisions) plus the session id / prompt the deterministic body
    already needs. Pure-Python dataclass — no Discord, no SQLite.

  * :class:`RoleRunner` is an ABC every backend implements: an
    ``is_available`` cheap-check + ``generate`` that produces a
    :class:`RoleRunnerOutput`. Implementations must never raise; on
    failure they return ``status="error"`` and the dispatcher walks
    to the next candidate.

  * :class:`DeterministicRoleRunner` is the always-available terminal
    fallback. The dispatcher always appends one of these so a poorly
    configured environment still produces a take.

  * :func:`build_role_runner_dispatcher` ties everything together. It
    enforces ``session.extra['active_research_roles']`` (so silenced
    roles never invoke a runner), walks Claude → Codex → Ollama →
    deterministic in order, captures which provider produced the
    output, and writes an agent-ops audit entry naming the provider so
    operators can answer "이 take 누가 썼어?".

The dispatcher returns a :class:`RoleRunnerOutput` regardless of
whether the configured providers worked. Callers (standalone_runners /
engineering_team_runtime) splice ``output.text`` into the
:class:`ResearchTurnOutcome` they already produce, and use
``output.provider`` for telemetry.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from .base import (
    AgentRequest,
    AgentResponse,
    AgentRunner,
    RunnerStatus,
)

logger = logging.getLogger(__name__)


PROVIDER_CLAUDE: str = "claude"
PROVIDER_CODEX: str = "codex"
PROVIDER_OLLAMA: str = "ollama"
PROVIDER_DETERMINISTIC: str = "deterministic"

# Provider priority used by :func:`build_role_runner_dispatcher` when
# the caller does not override it. Matches the M11 spec:
# configured Claude → Codex/local → Ollama → deterministic fallback.
DEFAULT_PROVIDER_PRIORITY: Tuple[str, ...] = (
    PROVIDER_CLAUDE,
    PROVIDER_CODEX,
    PROVIDER_OLLAMA,
    PROVIDER_DETERMINISTIC,
)


STATUS_OK: str = "ok"
STATUS_FALLBACK: str = "fallback"
STATUS_UNAVAILABLE: str = "unavailable"
STATUS_ERROR: str = "error"
STATUS_INACTIVE_ROLE: str = "inactive_role"
# A pre-dispatch gate (grant enforcement) blocked this take before any
# provider was contacted. Empty text; caller must surface the block reason.
STATUS_BLOCKED: str = "blocked"


# ---------------------------------------------------------------------------
# I/O dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleRunnerInput:
    """Context handed to a :class:`RoleRunner`.

    The four context channels the M11 spec requires map to attributes:

      * ``role_profile`` — :class:`agents.runtime.RolePolicy`-shaped
        mapping. We accept a generic mapping rather than the dataclass
        so test fakes can supply anything that quacks right.
      * ``topic_memory`` — current topic-ledger record + any prior
        notes the producer wants the runner to consider. Maps cleanly
        from :func:`agents.lifecycle.research_topic.read_topic_ledger`.
      * ``source_context`` — research pack / role pack / collected
        sources. Producer fills with whichever is freshest.
      * ``previous_decisions`` — replay of prior role takes in this
        session, in chronological order. The runner uses these so its
        take builds on (rather than repeats) what others said.
    """

    role: str
    session_id: str
    prompt: str
    role_profile: Mapping[str, Any] = field(default_factory=dict)
    topic_memory: Mapping[str, Any] = field(default_factory=dict)
    source_context: Mapping[str, Any] = field(default_factory=dict)
    previous_decisions: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_agent_request(self) -> AgentRequest:
        """Pack this input into the lower-level :class:`AgentRequest`
        shape so adapters wrapping :class:`AgentRunner` can call
        ``submit`` without re-shaping the payload.
        """

        return AgentRequest(
            prompt=self.prompt,
            role=self.role,
            task_id=self.session_id,
            references=tuple(self.previous_decisions),
            context={
                "role_profile": dict(self.role_profile or {}),
                "topic_memory": dict(self.topic_memory or {}),
                "source_context": dict(self.source_context or {}),
            },
            metadata=dict(self.metadata or {}),
        )


@dataclass(frozen=True)
class RoleRunnerOutput:
    """What a :class:`RoleRunner` produced.

    ``provider`` names which backend handled the take. Always populated
    — even the deterministic fallback writes ``"deterministic"`` so the
    audit log is unambiguous.

    ``status`` is one of:

      * ``"ok"`` — configured runner produced text.
      * ``"fallback"`` — deterministic fallback fired (no configured
        runner produced text).
      * ``"unavailable"`` — runner was skipped (CLI missing / endpoint
        down) — used internally; dispatcher converts these to
        ``"fallback"`` once it walks to the deterministic terminal.
      * ``"error"`` — runner raised; dispatcher walks to the next.
      * ``"inactive_role"`` — caller asked for a role not in
        ``active_research_roles``. Empty text. Caller should stay
        silent.
    """

    provider: str
    status: str
    text: str
    detail: Optional[str] = None
    used_fallback: bool = False
    metrics: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Base interface
# ---------------------------------------------------------------------------


class RoleRunner(ABC):
    """One backend in the role-runner priority chain."""

    provider: str

    @abstractmethod
    def is_available(self) -> bool:
        """Cheap reachability check. Must not raise."""

    @abstractmethod
    def generate(self, input_: RoleRunnerInput) -> RoleRunnerOutput:
        """Produce a take for *input_*. Must not raise.

        Implementations should map any backend exception to a
        ``status="error"`` :class:`RoleRunnerOutput`. The dispatcher
        treats raises and non-ok statuses identically — both walk to
        the next candidate — but catching here means a single bad
        runner never blocks the chain.
        """


# ---------------------------------------------------------------------------
# Adapters around existing AgentRunner backends
# ---------------------------------------------------------------------------


class _AgentRunnerRoleAdapter(RoleRunner):
    """Wrap one of the existing :class:`AgentRunner` (Claude / Codex /
    Ollama / Gemini / GitHubCopilot) backends so it speaks the
    :class:`RoleRunner` interface.

    The adapter is intentionally thin: the real CLI/HTTP plumbing
    stays inside the wrapped runner. We only translate between the
    two request/response shapes and convert ``UNAVAILABLE`` /
    ``ERROR`` into the corresponding :class:`RoleRunnerOutput`.
    """

    def __init__(self, provider: str, runner: AgentRunner) -> None:
        self.provider = provider
        self._runner = runner

    def is_available(self) -> bool:
        try:
            return bool(self._runner.is_available())
        except Exception:  # noqa: BLE001 - cheap-check must not raise
            logger.debug(
                "role-runner availability check raised for provider=%s",
                self.provider,
                exc_info=True,
            )
            return False

    def generate(self, input_: RoleRunnerInput) -> RoleRunnerOutput:
        request = input_.as_agent_request()
        try:
            response: AgentResponse = self._runner.submit(request)
        except Exception as exc:  # noqa: BLE001 - never propagate
            logger.warning(
                "role-runner provider=%s raised; degrade to next candidate",
                self.provider,
                exc_info=True,
            )
            return RoleRunnerOutput(
                provider=self.provider,
                status=STATUS_ERROR,
                text="",
                detail=str(exc) or exc.__class__.__name__,
            )

        status = getattr(response, "status", None)
        text = getattr(response, "text", "") or ""
        if status is RunnerStatus.OK and text.strip():
            return RoleRunnerOutput(
                provider=self.provider,
                status=STATUS_OK,
                text=text,
                detail=getattr(response, "detail", None),
                metrics=dict(getattr(response, "metrics", {}) or {}),
            )
        if status is RunnerStatus.UNAVAILABLE:
            return RoleRunnerOutput(
                provider=self.provider,
                status=STATUS_UNAVAILABLE,
                text="",
                detail=getattr(response, "detail", None)
                or f"{self.provider} runner unavailable",
            )
        # ERROR, DRY_RUN, or OK-with-empty-text → treat as error so
        # the dispatcher walks to the next candidate. dry_run output is
        # *not* a real take, so we explicitly do not bubble it up.
        return RoleRunnerOutput(
            provider=self.provider,
            status=STATUS_ERROR,
            text="",
            detail=getattr(response, "detail", None)
            or f"{self.provider} runner returned no usable text",
        )


def claude_role_runner(runner: AgentRunner) -> RoleRunner:
    return _AgentRunnerRoleAdapter(PROVIDER_CLAUDE, runner)


def codex_role_runner(runner: AgentRunner) -> RoleRunner:
    return _AgentRunnerRoleAdapter(PROVIDER_CODEX, runner)


def ollama_role_runner(runner: AgentRunner) -> RoleRunner:
    return _AgentRunnerRoleAdapter(PROVIDER_OLLAMA, runner)


# ---------------------------------------------------------------------------
# Deterministic terminal fallback
# ---------------------------------------------------------------------------


DeterministicRenderFn = Callable[[RoleRunnerInput], str]


class DeterministicRoleRunner(RoleRunner):
    """Always-available terminal in the priority chain.

    The dispatcher appends one of these so a poorly configured
    environment still produces *some* take. The render callable
    receives the same input every other runner sees; defaults to a
    minimal "no LLM configured — deterministic placeholder" line so a
    caller that forgot to inject anything still gets a non-empty
    response.

    The deterministic path is only meant as a *safety net*. The
    real per-role rendering already lives in the engineering_team
    runtime; this module's job is just to keep the runner contract
    closed (every dispatcher call returns a non-None outcome).
    """

    provider = PROVIDER_DETERMINISTIC

    def __init__(self, render_fn: Optional[DeterministicRenderFn] = None) -> None:
        self._render_fn = render_fn or _default_deterministic_text

    def is_available(self) -> bool:
        return True

    def generate(self, input_: RoleRunnerInput) -> RoleRunnerOutput:
        try:
            text = self._render_fn(input_)
        except Exception as exc:  # noqa: BLE001 - terminal must not raise
            logger.warning(
                "deterministic role runner render raised; emitting "
                "minimal placeholder",
                exc_info=True,
            )
            text = _default_deterministic_text(input_)
            return RoleRunnerOutput(
                provider=self.provider,
                status=STATUS_FALLBACK,
                text=text,
                detail=f"deterministic render raised: {exc}",
                used_fallback=True,
            )
        if not text:
            text = _default_deterministic_text(input_)
        return RoleRunnerOutput(
            provider=self.provider,
            status=STATUS_FALLBACK,
            text=text,
            detail="deterministic fallback (no configured runner produced text)",
            used_fallback=True,
        )


def _default_deterministic_text(input_: RoleRunnerInput) -> str:
    role = input_.role or "role"
    return (
        f"[{role}] deterministic fallback take — Claude/Codex/Ollama 가 "
        "구성되지 않아 결정형 템플릿으로 응답합니다."
    )


# ---------------------------------------------------------------------------
# Active-role gate
# ---------------------------------------------------------------------------


def is_role_active_for_research(
    session: Any,
    role: str,
    *,
    fallback_active: bool = True,
) -> bool:
    """Return True if *role* is in ``session.extra['active_research_roles']``.

    Spec: only active roles ever invoke a configured runner. When the
    list is missing — legacy session, partial install — we follow
    *fallback_active* (default True) so existing flows keep working
    without an explicit role allow-list. Setting *fallback_active* to
    False switches the gate to fail-closed for callers that want
    strict role gating.
    """

    if not role:
        return False
    extra = getattr(session, "extra", None) if session is not None else None
    if not isinstance(extra, Mapping):
        return fallback_active
    raw = extra.get("active_research_roles")
    if not isinstance(raw, (list, tuple)):
        return fallback_active
    short = role.split("/", 1)[-1].strip() or role
    for item in raw:
        if not isinstance(item, str):
            continue
        candidate = item.split("/", 1)[-1].strip()
        if candidate == short:
            return True
    return False


# ---------------------------------------------------------------------------
# Audit hook
# ---------------------------------------------------------------------------


AuditWriter = Callable[[Mapping[str, Any]], None]


def _default_audit_writer(record: Mapping[str, Any]) -> None:
    """Append a role-runner audit row onto the session's
    ``agent_ops_audit`` bucket, mirroring the M10 ``append_agent_ops_audit``
    shape but without the autonomy-decision dependency.

    Production wiring is injected by callers — this stub only logs at
    DEBUG so a forgotten ``audit_writer`` parameter doesn't silently
    swallow data.
    """

    logger.debug("role-runner audit (no writer configured): %s", dict(record))


def build_audit_record(
    *,
    session_id: str,
    role: str,
    output: RoleRunnerOutput,
    attempts: Sequence[Mapping[str, Any]] = (),
    recorded_at: Optional[str] = None,
) -> Mapping[str, Any]:
    """Build a JSON-friendly dict describing one dispatcher invocation.

    *attempts* is the per-provider trace ([{provider, status, detail},
    …]) so an operator can later see *why* the dispatcher fell through
    to a given backend, not just the final winner.
    """

    return {
        "kind": "role_runner_dispatch",
        "session_id": session_id,
        "role": role,
        "provider": output.provider,
        "status": output.status,
        "used_fallback": bool(output.used_fallback),
        "detail": output.detail,
        "attempts": [dict(item) for item in attempts],
        "recorded_at": recorded_at or _utc_now_iso(),
    }


def _utc_now_iso() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CandidateInvocation:
    provider: str
    status: str
    detail: Optional[str]


PreDispatchGate = Callable[[Any, "RoleRunnerInput"], Optional["RoleRunnerOutput"]]


#: Optional per-call reorder hook: ``(input_, [provider_id, ...]) -> [provider_id, ...]``.
#: Returns the desired ordering of the available provider ids (lossless — every
#: input id must appear in the output). Used for capability-aware routing.
ProviderRouter = Callable[["RoleRunnerInput", Sequence[str]], Sequence[str]]


def build_role_runner_dispatcher(
    *,
    candidates: Sequence[RoleRunner],
    audit_writer: Optional[AuditWriter] = None,
    active_role_predicate: Optional[Callable[[Any, str], bool]] = None,
    pre_dispatch_gate: Optional[PreDispatchGate] = None,
    provider_router: Optional[ProviderRouter] = None,
) -> Callable[[Any, RoleRunnerInput], RoleRunnerOutput]:
    """Return a callable ``(session, input_) → RoleRunnerOutput``.

    *candidates* must be ordered by priority (highest first). The
    dispatcher always tries them in order, treating
    ``status in {"unavailable", "error"}`` as "walk to the next" and
    ``status == "ok"`` as a final winner. If every configured candidate
    declines, the dispatcher falls back to the first
    :class:`DeterministicRoleRunner` it sees — and if none are
    supplied, it appends one transparently.

    *active_role_predicate* defaults to :func:`is_role_active_for_research`.
    Tests inject a strict predicate to verify the inactive-role guard.

    *audit_writer* receives one record per call (winning provider +
    per-candidate trace). Defaults to a debug-log stub; production
    wires this to ``append_agent_ops_audit``.
    """

    candidates_with_terminal = _ensure_terminal_fallback(tuple(candidates))
    predicate = active_role_predicate or is_role_active_for_research
    writer = audit_writer or _default_audit_writer

    def _dispatch(session: Any, input_: RoleRunnerInput) -> RoleRunnerOutput:
        if not predicate(session, input_.role):
            output = RoleRunnerOutput(
                provider=PROVIDER_DETERMINISTIC,
                status=STATUS_INACTIVE_ROLE,
                text="",
                detail=f"role {input_.role!r} not in active_research_roles",
                used_fallback=False,
            )
            _safe_audit(
                writer,
                build_audit_record(
                    session_id=input_.session_id,
                    role=input_.role,
                    output=output,
                    attempts=(),
                ),
            )
            return output

        # Pre-dispatch grant gate (hot-path enforcement). When supplied and it
        # returns a non-None output, the take is BLOCKED before any provider is
        # contacted. The gate must never raise; a raise degrades to "no block"
        # so a buggy gate cannot wedge the dispatcher.
        if pre_dispatch_gate is not None:
            try:
                gate_output = pre_dispatch_gate(session, input_)
            except Exception:  # noqa: BLE001 - gate must never break dispatch
                logger.warning(
                    "role-runner pre_dispatch_gate raised; proceeding without block",
                    exc_info=True,
                )
                gate_output = None
            if gate_output is not None:
                _safe_audit(
                    writer,
                    build_audit_record(
                        session_id=input_.session_id,
                        role=input_.role,
                        output=gate_output,
                        attempts=(),
                    ),
                )
                return gate_output

        # Capability-aware routing (optional): reorder candidates per call by
        # the task's preferred backend order. Lossless + never raises — a buggy
        # router degrades to the original priority order.
        dispatch_candidates = candidates_with_terminal
        if provider_router is not None:
            try:
                ordering = list(
                    provider_router(input_, [r.provider for r in candidates_with_terminal])
                )
                dispatch_candidates = _reorder_candidates(candidates_with_terminal, ordering)
            except Exception:  # noqa: BLE001 - routing must never break dispatch
                logger.warning(
                    "role-runner provider_router raised; using default order",
                    exc_info=True,
                )
                dispatch_candidates = candidates_with_terminal

        attempts: list[_CandidateInvocation] = []
        for runner in dispatch_candidates:
            if not _safe_is_available(runner):
                attempts.append(
                    _CandidateInvocation(
                        provider=runner.provider,
                        status=STATUS_UNAVAILABLE,
                        detail=f"{runner.provider} not available",
                    )
                )
                continue
            try:
                output = runner.generate(input_)
            except Exception as exc:  # noqa: BLE001 - belt-and-suspenders
                logger.warning(
                    "role-runner provider=%s.generate raised; "
                    "degrade to next candidate",
                    runner.provider,
                    exc_info=True,
                )
                attempts.append(
                    _CandidateInvocation(
                        provider=runner.provider,
                        status=STATUS_ERROR,
                        detail=str(exc) or exc.__class__.__name__,
                    )
                )
                continue
            attempts.append(
                _CandidateInvocation(
                    provider=output.provider or runner.provider,
                    status=output.status,
                    detail=output.detail,
                )
            )
            if output.status == STATUS_OK:
                _safe_audit(
                    writer,
                    build_audit_record(
                        session_id=input_.session_id,
                        role=input_.role,
                        output=output,
                        attempts=[
                            {
                                "provider": item.provider,
                                "status": item.status,
                                "detail": item.detail,
                            }
                            for item in attempts
                        ],
                    ),
                )
                return output
            if output.status == STATUS_FALLBACK:
                # Terminal deterministic produced text. Stamp the
                # used_fallback flag (in case a custom runner forgot)
                # and surface it as the winner.
                final = replace(output, used_fallback=True)
                _safe_audit(
                    writer,
                    build_audit_record(
                        session_id=input_.session_id,
                        role=input_.role,
                        output=final,
                        attempts=[
                            {
                                "provider": item.provider,
                                "status": item.status,
                                "detail": item.detail,
                            }
                            for item in attempts
                        ],
                    ),
                )
                return final

        # Terminal didn't return ``status == "fallback"`` — should not
        # happen because ``_ensure_terminal_fallback`` always appends a
        # working DeterministicRoleRunner. If it does, surface a clean
        # error rather than crashing so the caller can degrade.
        output = RoleRunnerOutput(
            provider=PROVIDER_DETERMINISTIC,
            status=STATUS_ERROR,
            text="",
            detail="role runner dispatcher exhausted all candidates without a terminal fallback",
            used_fallback=True,
        )
        _safe_audit(
            writer,
            build_audit_record(
                session_id=input_.session_id,
                role=input_.role,
                output=output,
                attempts=[
                    {
                        "provider": item.provider,
                        "status": item.status,
                        "detail": item.detail,
                    }
                    for item in attempts
                ],
            ),
        )
        return output

    return _dispatch


def _reorder_candidates(
    candidates: Tuple[RoleRunner, ...], ordering: Sequence[str]
) -> Tuple[RoleRunner, ...]:
    """Reorder *candidates* by provider-id *ordering* (lossless).

    Runners whose provider appears in *ordering* come first in that order;
    runners not mentioned keep their original relative order at the end. Every
    input runner appears exactly once in the output.
    """

    by_provider: dict[str, list[RoleRunner]] = {}
    for runner in candidates:
        by_provider.setdefault(runner.provider, []).append(runner)

    out: list[RoleRunner] = []
    used: set[int] = set()
    for provider in ordering:
        for runner in by_provider.get(provider, []):
            if id(runner) not in used:
                out.append(runner)
                used.add(id(runner))
    for runner in candidates:  # append anything not covered by ordering
        if id(runner) not in used:
            out.append(runner)
            used.add(id(runner))
    return tuple(out)


def _ensure_terminal_fallback(
    candidates: Tuple[RoleRunner, ...],
) -> Tuple[RoleRunner, ...]:
    for runner in candidates:
        if isinstance(runner, DeterministicRoleRunner):
            return candidates
    return candidates + (DeterministicRoleRunner(),)


def _safe_is_available(runner: RoleRunner) -> bool:
    try:
        return bool(runner.is_available())
    except Exception:  # noqa: BLE001 - is_available must never raise
        logger.debug(
            "role-runner is_available raised for provider=%s; treating as unavailable",
            getattr(runner, "provider", "?"),
            exc_info=True,
        )
        return False


def _safe_audit(writer: AuditWriter, record: Mapping[str, Any]) -> None:
    try:
        writer(record)
    except Exception:  # noqa: BLE001 - audit is observability only
        logger.warning(
            "role-runner audit writer raised; dropping record",
            exc_info=True,
        )


__all__ = (
    "DEFAULT_PROVIDER_PRIORITY",
    "DeterministicRoleRunner",
    "PROVIDER_CLAUDE",
    "PROVIDER_CODEX",
    "PROVIDER_DETERMINISTIC",
    "PROVIDER_OLLAMA",
    "RoleRunner",
    "RoleRunnerInput",
    "RoleRunnerOutput",
    "STATUS_BLOCKED",
    "STATUS_ERROR",
    "STATUS_FALLBACK",
    "STATUS_INACTIVE_ROLE",
    "STATUS_OK",
    "STATUS_UNAVAILABLE",
    "PreDispatchGate",
    "ProviderRouter",
    "build_audit_record",
    "build_role_runner_dispatcher",
    "claude_role_runner",
    "codex_role_runner",
    "is_role_active_for_research",
    "ollama_role_runner",
)
