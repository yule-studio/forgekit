"""Per-provider / model / mode usage breakdown (WT3). Pure / stdlib-only.

The rollup keeps totals; this adds the operator question "who used how much, live or
estimate?". For each dimension (provider / model / mode) it sums input/output/total and
keeps **live vs estimate separate** (never summed) so `/usage` reads per-provider with an
honest basis. Reads ledger rows (dicts) — no IO.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Sequence, Tuple


@dataclass
class KeyUsage:
    key: str
    events: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    live_tokens: int = 0
    estimate_tokens: int = 0

    @property
    def basis_label(self) -> str:
        if self.live_tokens and self.estimate_tokens:
            return "live+estimate"
        if self.live_tokens:
            return "live"
        if self.estimate_tokens:
            return "estimate"
        return "unknown"

    def to_dict(self) -> dict:
        return {"key": self.key, "events": self.events, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "total_tokens": self.total_tokens,
                "live_tokens": self.live_tokens, "estimate_tokens": self.estimate_tokens,
                "basis": self.basis_label}


def breakdown_by(rows: Sequence[dict], key_field: str) -> Tuple[KeyUsage, ...]:
    """Aggregate ledger rows by *key_field* (provider/model/mode), basis kept separate."""

    acc: Dict[str, KeyUsage] = {}
    for row in rows:
        k = str(row.get(key_field, "") or "-")
        ku = acc.setdefault(k, KeyUsage(k))
        tot = int(row.get("total_tokens", 0) or 0)
        ku.events += 1
        ku.input_tokens += int(row.get("input_tokens", 0) or 0)
        ku.output_tokens += int(row.get("output_tokens", 0) or 0)
        ku.total_tokens += tot
        basis = row.get("usage_basis", "")
        if basis == "live":
            ku.live_tokens += tot
        elif basis == "estimate":
            ku.estimate_tokens += tot
    return tuple(sorted(acc.values(), key=lambda x: (-x.total_tokens, x.key)))


def to_dict(rows: Sequence[dict]) -> dict:
    return {
        "by_provider": [k.to_dict() for k in breakdown_by(rows, "provider")],
        "by_model": [k.to_dict() for k in breakdown_by(rows, "model")],
        "by_mode": [k.to_dict() for k in breakdown_by(rows, "mode")],
    }


def render_lines(rows: Sequence[dict]) -> Tuple[str, ...]:
    """Operator `/usage` breakdown — per provider / model / mode with live vs estimate."""

    if not rows:
        return ("usage breakdown: (오늘 기록 없음)",)
    lines = ["usage breakdown (live / estimate 분리):"]
    for dim, label in (("provider", "by provider"), ("model", "by model"), ("mode", "by mode")):
        rows_k = breakdown_by(rows, dim)
        if not rows_k:
            continue
        lines.append(f"  {label}:")
        for ku in rows_k[:6]:
            lines.append(f"    {ku.key:<16} {ku.total_tokens:>7}tok "
                         f"(in {ku.input_tokens}/out {ku.output_tokens}) "
                         f"· live {ku.live_tokens} / est {ku.estimate_tokens} [{ku.basis_label}]")
    return tuple(lines)


__all__ = ("KeyUsage", "breakdown_by", "to_dict", "render_lines")
