from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Protocol, Sequence


class RunnerCapability(str, Enum):
    """High-level capabilities a backend can claim.

    The dispatcher uses these to filter the participants pool before applying
    role-weights. They are intentionally coarse — fine-grained selection is
    expressed as ranking signals via :class:`RunnerHooks`.
    """

    EXECUTE = "execute"
    ADVISE = "advise"
    REVIEW = "review"
    PATCH_PROPOSE = "patch_propose"
    LONG_CONTEXT = "long_context"
    LOCAL_PRIVATE = "local_private"
    GITHUB_NATIVE = "github_native"


class RunnerStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    UNAVAILABLE = "unavailable"
    DRY_RUN = "dry_run"


class RunnerError(RuntimeError):
    """Base class for runner-side failures."""


class RunnerUnavailableError(RunnerError):
    """Raised when the backend (CLI, network, model) cannot be used right now."""


@dataclass(frozen=True)
class AgentRequest:
    """Single unit of work handed to a runner.

    The shape is intentionally narrow: prompt + structured context. Files,
    diffs, and reference packs are passed as opaque dicts so the contract
    does not depend on Discord, planning-agent, or GitHub schemas.
    """

    prompt: str
    role: str
    task_id: str
    repository: Optional[str] = None
    write_allowed: bool = False
    references: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    context: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentResponse:
    runner_id: str
    status: RunnerStatus
    text: str
    detail: Optional[str] = None
    proposed_patch: Optional[str] = None
    artifacts: Mapping[str, Any] = field(default_factory=dict)
    metrics: Mapping[str, Any] = field(default_factory=dict)


class ReferenceCollector(Protocol):
    """Pulls reference packs (Pinterest, Notefolio, Mobbin, ...) for a request.

    Implementations are added in a later milestone — runners only know about
    the protocol so they can call it when references=() and the role policy
    requires them.
    """

    def collect(self, request: AgentRequest) -> Sequence[Mapping[str, Any]]:
        ...


class RankingSignal(Protocol):
    """Reports a (runner_id, score) signal for a request.

    The dispatcher combines these with role-weights-v0.md to pick a runner.
    Runners themselves do not consume rankings; they only forward the hook
    so the dispatcher can call it before submit().
    """

    def score(self, runner_id: str, request: AgentRequest) -> float:
        ...


class PerformanceTracker(Protocol):
    """Records runner outcomes for later analysis.

    Called by :meth:`AgentRunner.submit` regardless of success so we can build
    per-runner success rate, latency, and cost dashboards.
    """

    def record(self, runner_id: str, request: AgentRequest, response: AgentResponse) -> None:
        ...


@dataclass(frozen=True)
class RunnerHooks:
    """Optional pluggable extension points injected at registry-build time."""

    reference_collector: Optional[ReferenceCollector] = None
    ranking_signal: Optional[RankingSignal] = None
    performance_tracker: Optional[PerformanceTracker] = None


class AgentRunner(ABC):
    """Common contract for every backend in the engineering-agent pool.

    Concrete runners (Claude, Codex, Gemini, Ollama, GitHub Copilot) wrap a
    CLI, HTTP API, or workflow integration. They do **not** know about roles,
    Discord, or planning-agent — those concerns live in the dispatcher.
    """

    runner_id: str
    provider: str
    capabilities: Sequence[RunnerCapability] = ()

    def __init__(self, *, config: Optional[Mapping[str, Any]] = None, hooks: Optional[RunnerHooks] = None) -> None:
        self.config: Dict[str, Any] = dict(config or {})
        self.hooks: RunnerHooks = hooks or RunnerHooks()

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this runner can handle a real request right now.

        Cheap check only (e.g., shutil.which for CLI, env var presence). The
        registry calls this at boot and the doctor command surfaces the result.
        """

    @abstractmethod
    def submit(self, request: AgentRequest) -> AgentResponse:
        """Execute *request* and return the structured response.

        Implementations must always return an :class:`AgentResponse`; raising
        is reserved for unrecoverable wiring bugs. Use ``RunnerStatus.ERROR``
        for backend-side failures and ``RunnerStatus.UNAVAILABLE`` when the
        backend disappeared between :meth:`is_available` and :meth:`submit`.
        """

    def dry_run(self, request: AgentRequest) -> AgentResponse:
        """Return a deterministic response without contacting the backend.

        Used by tests and by the ``--dry-run`` operator path to verify that
        wiring (registry, role-weights, hooks) is correct without consuming
        any LLM tokens or hitting the network.
        """

        return AgentResponse(
            runner_id=self.runner_id,
            status=RunnerStatus.DRY_RUN,
            text=f"[dry-run] {self.runner_id} would handle role={request.role} task={request.task_id}",
            detail="dry-run: backend was not contacted",
        )

    def run(self, request: AgentRequest, *, dry_run: bool = False) -> AgentResponse:
        """Public entry point that applies hooks around :meth:`submit`.

        - Calls ``reference_collector`` when the request has no references.
        - Routes to :meth:`dry_run` when *dry_run* is True.
        - Records elapsed time and forwards to ``performance_tracker``.

        ``ranking_signal`` is intentionally not used here — it is consumed by
        the dispatcher to pick *which* runner to call, not by the runner itself.
        """

        enriched = self._collect_references(request)
        start = time.monotonic()
        if dry_run:
            response = self.dry_run(enriched)
        else:
            response = self.submit(enriched)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        if "elapsed_ms" not in response.metrics:
            response = replace(response, metrics={**response.metrics, "elapsed_ms": elapsed_ms})
        tracker = self.hooks.performance_tracker
        if tracker is not None:
            tracker.record(self.runner_id, enriched, response)
        return response

    def _collect_references(self, request: AgentRequest) -> AgentRequest:
        collector = self.hooks.reference_collector
        if collector is None or request.references:
            return request
        gathered = tuple(collector.collect(request))
        if not gathered:
            return request
        return replace(request, references=gathered)
