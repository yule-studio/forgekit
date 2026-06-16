"""Provider-runtime telemetry — why a provider was selected/failed/bypassed.

WT1 (live-provider hardening) needs the execution receipt to answer, for one
dispatch: *which provider actually ran, was it a live LLM or a fallback, how
long did it take, roughly how many tokens / how much did it cost, and which
providers were tried-and-rejected first (and why)?*

This module is the pure builder for that block. It consumes what the role-runner
dispatch already produces — ``RoleRunnerOutput`` (``provider`` / ``used_fallback``
/ ``metrics``) plus the per-candidate ``attempts`` trace — and emits a
JSON-able :class:`ProviderRuntime`. It owns the **standardized failure
taxonomy** so every surface classifies a degraded provider the same way.

It does not call any provider; it only interprets a finished dispatch. Token
usage is taken from live metrics when present, else estimated (chars/4) and
labelled accordingly, so the cost axis is always populated but never overclaims.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Tuple

from .cost_model import CostEstimate, estimate_cost, estimate_tokens_from_text

# --- Standardized failure taxonomy ------------------------------------------
# A degraded/rejected provider maps to exactly one class. Keep the *strings*
# stable — insights + operator surfaces group on them.
FAIL_NOT_OPTED_IN = "not_opted_in"
FAIL_CLI_MISSING = "cli_not_found"
FAIL_ENDPOINT_UNREACHABLE = "endpoint_unreachable"
FAIL_CONSTRUCTOR = "constructor_raised"
FAIL_AVAILABILITY = "availability_raised"
FAIL_SUBMIT_ERROR = "submit_error"
FAIL_BLOCKED_GRANT = "blocked_grant"
FAIL_UNAVAILABLE_OTHER = "unavailable_other"

_REASON_MARKERS: Tuple[Tuple[str, str], ...] = (
    ("not opted in", FAIL_NOT_OPTED_IN),
    ("cli not found", FAIL_CLI_MISSING),
    ("not found on path", FAIL_CLI_MISSING),
    ("endpoint unreachable", FAIL_ENDPOINT_UNREACHABLE),
    ("constructor raised", FAIL_CONSTRUCTOR),
    ("is_available() raised", FAIL_AVAILABILITY),
    ("availability", FAIL_AVAILABILITY),
    ("blocked", FAIL_BLOCKED_GRANT),
    ("grant", FAIL_BLOCKED_GRANT),
)


def classify_failure(status: str, detail: Optional[str]) -> str:
    """Map a per-candidate (status, detail) to a stable failure class."""

    text = f"{status or ''} {detail or ''}".lower()
    for marker, cls in _REASON_MARKERS:
        if marker in text:
            return cls
    if "error" in (status or "").lower():
        return FAIL_SUBMIT_ERROR
    return FAIL_UNAVAILABLE_OTHER


@dataclass(frozen=True)
class FallbackStep:
    """One provider that was tried and rejected before the winner."""

    provider: str
    status: str
    failure_class: str
    detail: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "status": self.status,
            "failure_class": self.failure_class,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ProviderRuntime:
    """Per-dispatch provider telemetry — selection, liveness, usage, cost."""

    selected_provider: str
    live: bool                      # did a real LLM submit happen?
    used_fallback: bool             # did we land on the deterministic safety net?
    elapsed_ms: Optional[float]
    input_tokens: int
    output_tokens: int
    total_tokens: int
    usage_basis: str                # "live" | "estimate"
    cost: Optional[CostEstimate]
    fallback_from: Tuple[FallbackStep, ...] = ()

    def to_dict(self) -> dict:
        return {
            "selected_provider": self.selected_provider,
            "live": self.live,
            "used_fallback": self.used_fallback,
            "elapsed_ms": self.elapsed_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "usage_basis": self.usage_basis,
            "cost": self.cost.to_dict() if self.cost else None,
            "fallback_from": [s.to_dict() for s in self.fallback_from],
        }


_NON_LLM_PROVIDERS = frozenset({"deterministic", "grant-gate"})


def build_provider_runtime(
    *,
    selected_provider: str,
    used_fallback: bool,
    metrics: Optional[Mapping[str, Any]] = None,
    attempts: Sequence[Mapping[str, Any]] = (),
    prompt_text: str = "",
    output_text: str = "",
    model: Optional[str] = None,
) -> ProviderRuntime:
    """Build a :class:`ProviderRuntime` from a finished dispatch.

    *metrics* is ``RoleRunnerOutput.metrics`` (may carry ``elapsed_ms``,
    ``live``, ``input_tokens`` / ``output_tokens`` when a live provider reports
    them). *attempts* is the dispatcher's per-candidate trace — every entry
    before the winner is recorded as a classified fallback step.
    """

    metrics = dict(metrics or {})
    live = bool(metrics.get("live")) and selected_provider not in _NON_LLM_PROVIDERS
    elapsed_ms = metrics.get("elapsed_ms")
    try:
        elapsed_ms = float(elapsed_ms) if elapsed_ms is not None else None
    except (TypeError, ValueError):
        elapsed_ms = None

    # Usage: prefer live counts; else deterministic chars/4 estimate.
    if "input_tokens" in metrics or "output_tokens" in metrics:
        input_tokens = int(metrics.get("input_tokens") or 0)
        output_tokens = int(metrics.get("output_tokens") or 0)
        usage_basis = "live"
    else:
        input_tokens = estimate_tokens_from_text(prompt_text)
        output_tokens = estimate_tokens_from_text(output_text)
        usage_basis = "estimate"

    cost = estimate_cost(
        selected_provider,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=model or metrics.get("model"),
    )

    # Everything tried before the winning provider is a fallback step.
    steps: list[FallbackStep] = []
    for item in attempts:
        prov = str(item.get("provider") or "")
        if prov == selected_provider:
            continue
        status = str(item.get("status") or "")
        detail = item.get("detail")
        steps.append(
            FallbackStep(
                provider=prov,
                status=status,
                failure_class=classify_failure(status, detail),
                detail=str(detail) if detail is not None else None,
            )
        )

    return ProviderRuntime(
        selected_provider=selected_provider,
        live=live,
        used_fallback=bool(used_fallback),
        elapsed_ms=elapsed_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        usage_basis=usage_basis,
        cost=cost,
        fallback_from=tuple(steps),
    )


__all__ = (
    "FAIL_NOT_OPTED_IN",
    "FAIL_CLI_MISSING",
    "FAIL_ENDPOINT_UNREACHABLE",
    "FAIL_CONSTRUCTOR",
    "FAIL_AVAILABILITY",
    "FAIL_SUBMIT_ERROR",
    "FAIL_BLOCKED_GRANT",
    "FAIL_UNAVAILABLE_OTHER",
    "classify_failure",
    "FallbackStep",
    "ProviderRuntime",
    "build_provider_runtime",
)
