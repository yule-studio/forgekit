"""Usage rollups (WT2) — aggregate ledger rows by provider / model / mode / basis.

Reads ledger rows (dicts) and produces a :class:`UsageRollup`. The live vs estimate
split is kept SEPARATE (never summed into one "usage" number) so a report can be
honest about what was measured vs estimated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Sequence, Tuple


@dataclass
class UsageRollup:
    scope: str = "today"
    events: int = 0
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    live_tokens: int = 0          # basis=live (measured)
    estimate_tokens: int = 0      # basis=estimate (heuristic) — kept separate
    by_provider: Dict[str, int] = field(default_factory=dict)
    by_model: Dict[str, int] = field(default_factory=dict)
    by_mode: Dict[str, int] = field(default_factory=dict)
    throttled: int = 0
    fallback: int = 0
    cost_usd: float = 0.0         # only summed when a proxy was present

    @property
    def live_ratio(self) -> float:
        denom = self.live_tokens + self.estimate_tokens
        return (self.live_tokens / denom) if denom else 0.0

    def to_dict(self) -> dict:
        return {
            "scope": self.scope, "events": self.events,
            "total_tokens": self.total_tokens, "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens, "live_tokens": self.live_tokens,
            "estimate_tokens": self.estimate_tokens, "live_ratio": round(self.live_ratio, 3),
            "by_provider": self.by_provider, "by_model": self.by_model,
            "by_mode": self.by_mode, "throttled": self.throttled,
            "fallback": self.fallback, "cost_usd": round(self.cost_usd, 4),
        }


def _bump(d: Dict[str, int], key: str, n: int) -> None:
    if key:
        d[key] = d.get(key, 0) + n


def rollup(rows: Sequence[dict], *, scope: str = "today") -> UsageRollup:
    r = UsageRollup(scope=scope)
    for row in rows:
        tot = int(row.get("total_tokens", 0) or 0)
        r.events += 1
        r.total_tokens += tot
        r.input_tokens += int(row.get("input_tokens", 0) or 0)
        r.output_tokens += int(row.get("output_tokens", 0) or 0)
        basis = row.get("usage_basis", "")
        if basis == "live":
            r.live_tokens += tot
        elif basis == "estimate":
            r.estimate_tokens += tot
        _bump(r.by_provider, str(row.get("provider", "") or ""), tot)
        _bump(r.by_model, str(row.get("model", "") or ""), tot)
        _bump(r.by_mode, str(row.get("mode", "") or ""), tot)
        if row.get("throttled"):
            r.throttled += 1
        if row.get("fallback"):
            r.fallback += 1
        if row.get("cost_usd") is not None:
            r.cost_usd += float(row.get("cost_usd") or 0.0)
    return r


def top_by_tokens(rows: Sequence[dict], *, limit: int = 5) -> Tuple[dict, ...]:
    return tuple(sorted(rows, key=lambda x: int(x.get("total_tokens", 0) or 0), reverse=True)[:limit])


__all__ = ("UsageRollup", "rollup", "top_by_tokens")
