"""LSP-style preflight — role-aware static analysis verdict (#100).

This module is the **judgement seam**. Concrete runners live next
door under :mod:`yule_orchestrator.agents.static_analysis.runners`
and expose :class:`LspRunnerProtocol`. The seam is intentionally
small so the live LLM editor (#91 / F4) and downstream retry loops
can pull a single :class:`PreflightLspVerdict` per attempt.

Dataclass shape:

  * :class:`LspFinding` — one line emitted by a runner.
  * :class:`LspResult` — every finding from one runner invocation
    plus the exit code and a masked ``stderr_tail``.
  * :class:`PreflightLspVerdict` — aggregated verdict carrying the
    overall :class:`PreflightLspLevel`, summarised counts, and the
    ordered chain of ``runner_id`` values consulted.

Role → runner chain (read-only mapping; tests pin):

  * ``backend`` / ``ai``          → ``python_ruff`` → ``python_pyright`` → ``python_mypy``
  * ``frontend``                  → ``typescript``
  * ``qa`` / ``devops``           → ``python_ruff``
  * everything else               → ``python_ruff`` (safe default)

Verdict levelling rules:

  * Any ``error`` finding             → ``block``
  * Otherwise ≥1 ``warning`` finding  → ``warning``
  * Otherwise ``info`` / ``hint``     → ``advisory``
  * Clean / env-off / binary-missing  → ``advisory``

The level is **never** elevated to ``block`` by a missing binary
or by env OFF — that is a hard rail enforced here and pinned in
:mod:`tests.engineering.test_lsp_preflight_governance`.
"""

from __future__ import annotations

import enum
import os
from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


#: Default subprocess timeout. The hard rail is 30s — runners must
#: pass ``timeout`` down to ``subprocess.run`` so a hanging binary
#: cannot block the retry loop indefinitely.
DEFAULT_TIMEOUT_SECONDS: int = 30


#: Env var that gates the preflight. ``false`` (default) means the
#: seam always returns an advisory verdict and **no** subprocess is
#: spawned. Operators flip this to ``true`` when they want the
#: retry loop to consult external LSP / linter / type checker output.
ENV_PREFLIGHT_ENABLED: str = "YULE_LSP_PREFLIGHT_ENABLED"

#: Comma-separated override for the runner chain. Empty (default)
#: → fall back to the role mapping. When set, every runner_id must
#: appear in the registry or it is silently dropped.
ENV_RUNNERS: str = "YULE_LSP_RUNNERS"

#: Subprocess timeout override (seconds). Capped at 30s — values
#: above the cap are clamped because the hard rail wins over env.
ENV_TIMEOUT_SECONDS: str = "YULE_LSP_TIMEOUT_SECONDS"


#: Severity values a runner may emit. Anything else is normalised
#: to ``"info"`` so a runner cannot accidentally introduce an
#: unknown severity that bypasses the levelling table.
SEVERITY_ERROR: str = "error"
SEVERITY_WARNING: str = "warning"
SEVERITY_INFO: str = "info"
SEVERITY_HINT: str = "hint"

_ALL_SEVERITIES: Tuple[str, ...] = (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    SEVERITY_INFO,
    SEVERITY_HINT,
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LspFinding:
    """A single finding emitted by a runner.

    ``file`` is the path the runner reported (relative or absolute
    — the seam does not normalise so audit consumers can decide).
    ``line`` and ``column`` are 1-based; both default to 1 when the
    runner does not pinpoint a position (e.g. project-level config
    errors).

    ``severity`` is normalised on construction: anything outside
    :data:`_ALL_SEVERITIES` is coerced to ``"info"`` so a runner
    cannot smuggle an unknown level past the verdict aggregator.
    """

    file: str
    line: int
    column: int
    severity: str
    code: str
    message: str

    def __post_init__(self) -> None:  # pragma: no cover - trivial
        normalised = _normalise_severity(self.severity)
        if normalised != self.severity:
            object.__setattr__(self, "severity", normalised)

    @property
    def is_error(self) -> bool:
        return self.severity == SEVERITY_ERROR

    @property
    def is_warning(self) -> bool:
        return self.severity == SEVERITY_WARNING


@dataclass(frozen=True)
class LspResult:
    """One runner invocation's result.

    ``language`` is the human-readable label (python / typescript /
    go / rust). ``runner_id`` is the registry key (``python_ruff``
    etc.) used to drive the role chain. ``findings`` is a tuple so
    callers can treat it as an immutable audit record. ``exit_code``
    is ``None`` when the runner returned an advisory result without
    spawning a subprocess (binary missing / env OFF). ``stderr_tail``
    must already be PasteGuard-masked before being attached.
    """

    language: str
    runner_id: str
    findings: Tuple[LspFinding, ...]
    exit_code: Optional[int]
    stderr_tail: str
    advisory: bool = False
    note: str = ""

    @property
    def has_errors(self) -> bool:
        return any(f.is_error for f in self.findings)

    @property
    def has_warnings(self) -> bool:
        return any(f.is_warning for f in self.findings)


class PreflightLspLevel(str, enum.Enum):
    """Aggregated verdict level.

    Mirrors :class:`~yule_orchestrator.agents.learning.mistake_ledger.BlockerLevel`
    naming so downstream consumers (retry loop / mistake ledger
    surface) can switch on a single string without translation.
    """

    ADVISORY = "advisory"
    WARNING = "warning"
    BLOCK = "block"


@dataclass(frozen=True)
class PreflightLspVerdict:
    """Aggregated LSP preflight verdict for a retry-loop iteration.

    ``level`` reflects the harshest finding across all consulted
    runners (block > warning > advisory). ``blocker_count`` counts
    the number of error-severity findings — the retry loop uses it
    to decide whether to escalate to ``needs_approval``.

    ``runner_id_chain`` mirrors the order the runners executed so a
    failed run can be reproduced step-by-step. ``findings_summary``
    is the per-runner roll-up: ``{runner_id: {severity: count}}``.

    ``advisory_reason`` carries a short Korean string for the
    operator surface (env OFF / binary missing / no findings).
    """

    level: PreflightLspLevel
    findings_summary: Mapping[str, Mapping[str, int]]
    blocker_count: int
    runner_id_chain: Tuple[str, ...]
    results: Tuple[LspResult, ...]
    role: str
    advisory_reason: str = ""

    @property
    def is_block(self) -> bool:
        return self.level == PreflightLspLevel.BLOCK

    @property
    def is_advisory(self) -> bool:
        return self.level == PreflightLspLevel.ADVISORY

    @property
    def recommend_needs_approval(self) -> bool:
        """True iff the caller must route to the approval lane."""

        return self.is_block

    def to_payload(self) -> Mapping[str, Any]:
        """Serialise to a payload safe to attach to job metadata.

        Findings are flattened to a list of dicts so JSON encoders
        can round-trip them. ``stderr_tail`` is already masked at
        the runner boundary so no further redaction is needed here.
        """

        return {
            "level": self.level.value,
            "blocker_count": self.blocker_count,
            "runner_id_chain": list(self.runner_id_chain),
            "findings_summary": {
                runner_id: dict(counts)
                for runner_id, counts in self.findings_summary.items()
            },
            "role": self.role,
            "advisory_reason": self.advisory_reason,
            "results": [
                {
                    "language": r.language,
                    "runner_id": r.runner_id,
                    "exit_code": r.exit_code,
                    "advisory": r.advisory,
                    "note": r.note,
                    "stderr_tail": r.stderr_tail,
                    "findings": [
                        {
                            "file": f.file,
                            "line": f.line,
                            "column": f.column,
                            "severity": f.severity,
                            "code": f.code,
                            "message": f.message,
                        }
                        for f in r.findings
                    ],
                }
                for r in self.results
            ],
        }


# ---------------------------------------------------------------------------
# Runner protocol
# ---------------------------------------------------------------------------


class LspRunnerProtocol(Protocol):
    """Common contract for every runner under :mod:`.runners`.

    Implementations must:

      1. Honour ``timeout`` and clamp it at :data:`DEFAULT_TIMEOUT_SECONDS`.
      2. Return an advisory :class:`LspResult` (``advisory=True``,
         ``exit_code=None``) when the binary is missing — never raise.
      3. Pass any subprocess ``stderr`` through PasteGuard
         (``channel=VAULT``) before stuffing it into ``stderr_tail``.
      4. Accept ``subprocess_runner`` so tests can inject a fake.
    """

    runner_id: str
    language: str

    def run(
        self,
        paths: Sequence[str],
        *,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        subprocess_runner: Optional[Callable[..., Any]] = None,
    ) -> LspResult:  # pragma: no cover - protocol stub
        ...


# ---------------------------------------------------------------------------
# Role → runner chain mapping
# ---------------------------------------------------------------------------


LSP_ROLE_RUNNER_CHAINS: Mapping[str, Tuple[str, ...]] = {
    "backend": ("python_ruff", "python_pyright", "python_mypy"),
    "ai": ("python_ruff", "python_pyright", "python_mypy"),
    "ai_engineer": ("python_ruff", "python_pyright", "python_mypy"),
    "backend_engineer": ("python_ruff", "python_pyright", "python_mypy"),
    "frontend": ("typescript",),
    "frontend_engineer": ("typescript",),
    "qa": ("python_ruff",),
    "qa_engineer": ("python_ruff",),
    "devops": ("python_ruff",),
    "devops_engineer": ("python_ruff",),
}

#: Default fallback chain used when the role is unknown.
LSP_DEFAULT_RUNNER_CHAIN: Tuple[str, ...] = ("python_ruff",)


def resolve_runner_chain(
    *,
    role: str,
    runners: Any = "AUTO",
) -> Tuple[str, ...]:
    """Pick the runner chain for ``role``.

    ``runners="AUTO"`` (default) consults the role mapping; the env
    override :data:`ENV_RUNNERS` is honoured only when ``runners`` is
    AUTO so callers can pin a chain regardless of operator env.

    Returns an empty tuple when no resolvable runner remains.
    """

    if isinstance(runners, str) and runners.upper() == "AUTO":
        env_value = os.getenv(ENV_RUNNERS, "")
        if env_value.strip():
            chain = tuple(
                token.strip() for token in env_value.split(",") if token.strip()
            )
            return chain
        normalised = (role or "").strip().lower()
        return LSP_ROLE_RUNNER_CHAINS.get(normalised, LSP_DEFAULT_RUNNER_CHAIN)

    if isinstance(runners, str):
        chain = tuple(
            token.strip() for token in runners.split(",") if token.strip()
        )
        return chain
    if isinstance(runners, Iterable):
        return tuple(str(r).strip() for r in runners if str(r).strip())
    return ()


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------


def is_lsp_preflight_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """Return True when the env var is set to a truthy string.

    Truthy values mirror typical operator habits: ``1`` / ``true`` /
    ``yes`` / ``on`` (case-insensitive). Anything else — including
    the unset case — is False so the preflight stays *advisory-only*
    by default.
    """

    source = env if env is not None else os.environ
    raw = str(source.get(ENV_PREFLIGHT_ENABLED, "")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _resolve_timeout(env: Optional[Mapping[str, str]] = None) -> int:
    source = env if env is not None else os.environ
    raw = str(source.get(ENV_TIMEOUT_SECONDS, "")).strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    if value <= 0:
        return DEFAULT_TIMEOUT_SECONDS
    # Hard rail: cap at 30s regardless of operator override so a
    # mistyped env value cannot wedge the retry loop.
    return min(value, DEFAULT_TIMEOUT_SECONDS)


# ---------------------------------------------------------------------------
# Runner registry
# ---------------------------------------------------------------------------


def _load_runner_registry() -> Mapping[str, "LspRunnerProtocol"]:
    """Lazy-load the runner registry.

    Concrete runner modules are imported lazily so the seam stays
    light-weight when env is OFF (no need to compile every regex /
    parser in :mod:`.runners`).
    """

    from .runners import build_runner_registry  # local import — see docstring

    return build_runner_registry()


# ---------------------------------------------------------------------------
# Judgement entry point
# ---------------------------------------------------------------------------


def judge_lsp_preflight(
    *,
    paths: Sequence[str],
    role: str,
    runners: Any = "AUTO",
    subprocess_runner: Optional[Callable[..., Any]] = None,
    env: Optional[Mapping[str, str]] = None,
    timeout: Optional[int] = None,
    registry: Optional[Mapping[str, "LspRunnerProtocol"]] = None,
) -> PreflightLspVerdict:
    """Run the configured runner chain and aggregate the verdict.

    Parameters mirror the operator surface — ``paths`` is the list
    of files / dirs to lint, ``role`` drives the chain selection,
    ``runners`` lets callers override the chain.

    When the env var :data:`ENV_PREFLIGHT_ENABLED` is False (default)
    the function short-circuits and returns an advisory verdict
    *without* invoking any runner — this is the safe-by-default
    posture pinned in governance tests.

    ``subprocess_runner`` is forwarded to every runner so tests can
    inject a fake without monkeypatching :mod:`subprocess`.
    """

    role_value = (role or "").strip()
    enabled = is_lsp_preflight_enabled(env=env)
    chain = resolve_runner_chain(role=role_value, runners=runners)
    resolved_timeout = timeout if timeout is not None else _resolve_timeout(env=env)
    # Hard rail: timeout is capped even when the caller passes a
    # larger value explicitly — defence in depth against a misuse
    # in a producer that loads from operator-controlled config.
    resolved_timeout = max(1, min(resolved_timeout, DEFAULT_TIMEOUT_SECONDS))

    if not enabled:
        return PreflightLspVerdict(
            level=PreflightLspLevel.ADVISORY,
            findings_summary={},
            blocker_count=0,
            runner_id_chain=tuple(chain),
            results=(),
            role=role_value,
            advisory_reason="env OFF — preflight 비활성, advisory only",
        )

    if not chain:
        return PreflightLspVerdict(
            level=PreflightLspLevel.ADVISORY,
            findings_summary={},
            blocker_count=0,
            runner_id_chain=(),
            results=(),
            role=role_value,
            advisory_reason="role 매핑 / runners 인자 결과 빈 chain",
        )

    if not paths:
        return PreflightLspVerdict(
            level=PreflightLspLevel.ADVISORY,
            findings_summary={},
            blocker_count=0,
            runner_id_chain=tuple(chain),
            results=(),
            role=role_value,
            advisory_reason="paths 비어있음 — 분석 대상 없음",
        )

    active_registry: Mapping[str, LspRunnerProtocol]
    active_registry = registry if registry is not None else _load_runner_registry()

    executed_chain: List[str] = []
    results: List[LspResult] = []
    for runner_id in chain:
        runner = active_registry.get(runner_id)
        if runner is None:
            # Unknown runner_id — skip silently. Operators see the
            # gap in ``runner_id_chain`` for the audit trail.
            continue
        executed_chain.append(runner_id)
        result = runner.run(
            list(paths),
            timeout=resolved_timeout,
            subprocess_runner=subprocess_runner,
        )
        results.append(result)

    return _aggregate(
        role=role_value,
        chain=tuple(executed_chain),
        results=tuple(results),
    )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _aggregate(
    *,
    role: str,
    chain: Tuple[str, ...],
    results: Tuple[LspResult, ...],
) -> PreflightLspVerdict:
    summary: dict[str, dict[str, int]] = {}
    blocker_count = 0
    has_error = False
    has_warning = False
    advisory_notes: List[str] = []

    for result in results:
        counts: dict[str, int] = {sev: 0 for sev in _ALL_SEVERITIES}
        for finding in result.findings:
            counts[finding.severity] = counts.get(finding.severity, 0) + 1
        if result.advisory and result.note:
            advisory_notes.append(f"{result.runner_id}: {result.note}")
        summary[result.runner_id] = counts
        if counts.get(SEVERITY_ERROR, 0):
            has_error = True
            blocker_count += counts[SEVERITY_ERROR]
        if counts.get(SEVERITY_WARNING, 0):
            has_warning = True

    if has_error:
        level = PreflightLspLevel.BLOCK
    elif has_warning:
        level = PreflightLspLevel.WARNING
    else:
        level = PreflightLspLevel.ADVISORY

    advisory_reason = ""
    if level == PreflightLspLevel.ADVISORY:
        if advisory_notes:
            advisory_reason = "; ".join(advisory_notes)
        else:
            advisory_reason = "clean — runner 출력에 error/warning 없음"

    return PreflightLspVerdict(
        level=level,
        findings_summary=summary,
        blocker_count=blocker_count,
        runner_id_chain=chain,
        results=results,
        role=role,
        advisory_reason=advisory_reason,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _normalise_severity(value: str) -> str:
    lowered = str(value or "").strip().lower()
    if lowered in _ALL_SEVERITIES:
        return lowered
    # Common runner aliases — keep the table tiny and defensive.
    if lowered in {"err", "fatal", "critical"}:
        return SEVERITY_ERROR
    if lowered in {"warn"}:
        return SEVERITY_WARNING
    if lowered in {"note", "information"}:
        return SEVERITY_INFO
    return SEVERITY_INFO


__all__ = (
    "DEFAULT_TIMEOUT_SECONDS",
    "ENV_PREFLIGHT_ENABLED",
    "ENV_RUNNERS",
    "ENV_TIMEOUT_SECONDS",
    "LSP_DEFAULT_RUNNER_CHAIN",
    "LSP_ROLE_RUNNER_CHAINS",
    "LspFinding",
    "LspResult",
    "LspRunnerProtocol",
    "PreflightLspLevel",
    "PreflightLspVerdict",
    "SEVERITY_ERROR",
    "SEVERITY_HINT",
    "SEVERITY_INFO",
    "SEVERITY_WARNING",
    "is_lsp_preflight_enabled",
    "judge_lsp_preflight",
    "resolve_runner_chain",
)
