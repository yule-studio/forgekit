"""Vendor-neutral token → cost proxy.

A deterministic, table-driven estimator that turns ``(provider, model,
input_tokens, output_tokens)`` into a USD cost *proxy*. It is intentionally a
proxy, not a billing source — prices are coarse public list-price approximations
kept in one table so the eval gate (WT3) and provider-runtime receipt (WT1) can
compare runs on a *cost* axis without wiring a real billing API.

Design:
  * Prices are **data** (``DEFAULT_PRICING``) so a caller can override per-call
    or via a future config without touching logic.
  * Local backends (Ollama) are zero-cost by construction (``basis="local"``).
  * An unknown provider/model falls back to a conservative default and is
    labelled ``basis="fallback"`` so a consumer can tell estimate quality.
  * Pure / deterministic — no clock, no network. Safe in tests and offline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

# USD per 1K tokens, (input, output). Coarse public-list-price PROXIES — update
# as a data edit, never inline. Local inference is modelled as zero cost.
DEFAULT_PRICING: Mapping[str, Mapping[str, tuple]] = {
    "claude": {
        "_default": (0.003, 0.015),
        "opus": (0.015, 0.075),
        "sonnet": (0.003, 0.015),
        "haiku": (0.0008, 0.004),
    },
    "codex": {
        "_default": (0.0025, 0.010),
    },
    "gemini": {
        "_default": (0.00125, 0.005),
    },
    "ollama": {
        "_default": (0.0, 0.0),  # local inference — no marginal cost
    },
    "deterministic": {
        "_default": (0.0, 0.0),  # rule path — no LLM spend
    },
}

# Provider/model that isn't in the table: conservative mid-tier proxy.
_FALLBACK_PRICE: tuple = (0.003, 0.015)
_LOCAL_PROVIDERS = frozenset({"ollama", "deterministic", "grant-gate"})


@dataclass(frozen=True)
class CostEstimate:
    """A token-derived cost proxy. ``basis`` flags estimate quality."""

    provider: str
    model: Optional[str]
    input_tokens: int
    output_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    total_cost_usd: float
    basis: str  # "table" | "fallback" | "local"

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "input_cost_usd": round(self.input_cost_usd, 6),
            "output_cost_usd": round(self.output_cost_usd, 6),
            "total_cost_usd": round(self.total_cost_usd, 6),
            "basis": self.basis,
        }


def _resolve_price(
    provider: str, model: Optional[str], pricing: Mapping[str, Mapping[str, tuple]]
) -> tuple:
    """Return ((in, out), basis) for *provider*/*model*."""

    table = pricing.get(provider)
    if table is None:
        return _FALLBACK_PRICE, ("local" if provider in _LOCAL_PROVIDERS else "fallback")
    if model:
        # match the first model key that is a case-insensitive substring
        low = model.lower()
        for key, price in table.items():
            if key != "_default" and key in low:
                return price, "table"
    if "_default" in table:
        basis = "local" if provider in _LOCAL_PROVIDERS else "table"
        return table["_default"], basis
    return _FALLBACK_PRICE, "fallback"


def estimate_cost(
    provider: str,
    *,
    input_tokens: int,
    output_tokens: int,
    model: Optional[str] = None,
    pricing: Optional[Mapping[str, Mapping[str, tuple]]] = None,
) -> CostEstimate:
    """Return a deterministic USD cost proxy for one provider call."""

    pricing = pricing or DEFAULT_PRICING
    inp = max(0, int(input_tokens))
    out = max(0, int(output_tokens))
    (price_in, price_out), basis = _resolve_price(provider, model, pricing)
    input_cost = inp / 1000.0 * price_in
    output_cost = out / 1000.0 * price_out
    return CostEstimate(
        provider=provider,
        model=model,
        input_tokens=inp,
        output_tokens=out,
        input_cost_usd=input_cost,
        output_cost_usd=output_cost,
        total_cost_usd=input_cost + output_cost,
        basis=basis,
    )


def estimate_tokens_from_text(text: str) -> int:
    """Deterministic chars/4 token estimate (same basis as the harness)."""

    if not text:
        return 0
    return max(1, len(text) // 4)


__all__ = (
    "DEFAULT_PRICING",
    "CostEstimate",
    "estimate_cost",
    "estimate_tokens_from_text",
)
