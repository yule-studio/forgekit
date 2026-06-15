"""Token-efficiency insights — cumulative aggregation across runs (Phase 4).

The benchmark writes one ``delta.json`` per run; execution receipts carry a
``token_efficiency`` block per dispatch. This module rolls those up into a
single cumulative view an operator can read ("how much have we saved overall,
and where"). Pure + deterministic — no live calls.

Two inputs:
  * :func:`scan_token_efficiency_evidence` — read every
    ``runs/token-efficiency/*/delta.json`` and aggregate totals.
  * :func:`aggregate_receipts` — roll up ``token_efficiency`` blocks from a
    list of execution-receipt dicts (the live session path).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class TokenEfficiencyInsights:
    runs: int
    input_before: int
    input_after: int
    saved: int
    per_run: Tuple[Mapping[str, Any], ...] = ()
    warnings: Tuple[str, ...] = ()

    @property
    def reduction_pct(self) -> float:
        if self.input_before <= 0:
            return 0.0
        return round((self.input_before - self.input_after) / self.input_before * 100.0, 1)

    def to_dict(self) -> dict:
        return {
            "runs": self.runs,
            "input_before": self.input_before,
            "input_after": self.input_after,
            "saved": self.saved,
            "reduction_pct": self.reduction_pct,
            "per_run": [dict(r) for r in self.per_run],
            "warnings": list(self.warnings),
        }


def aggregate_delta_dicts(deltas: Sequence[Mapping[str, Any]]) -> TokenEfficiencyInsights:
    """Aggregate a list of benchmark ``delta.json`` payloads."""

    before = after = saved = 0
    per_run: List[Mapping[str, Any]] = []
    warnings: List[str] = []
    for d in deltas:
        totals = d.get("totals") if isinstance(d, Mapping) else None
        if not isinstance(totals, Mapping):
            warnings.append(f"delta missing totals: {d.get('slug') if isinstance(d, Mapping) else '?'}")
            continue
        b = int(totals.get("input_tokens_before", 0) or 0)
        a = int(totals.get("input_tokens_after", 0) or 0)
        before += b
        after += a
        saved += b - a
        per_run.append(
            {
                "slug": d.get("slug", "?"),
                "before": b,
                "after": a,
                "saved": b - a,
                "reduction_pct": totals.get("input_reduction_pct", 0.0),
            }
        )
    return TokenEfficiencyInsights(
        runs=len(per_run),
        input_before=before,
        input_after=after,
        saved=saved,
        per_run=tuple(per_run),
        warnings=tuple(warnings),
    )


def scan_token_efficiency_evidence(runs_dir: Path) -> TokenEfficiencyInsights:
    """Read every ``<runs_dir>/*/delta.json`` and aggregate."""

    runs_dir = Path(runs_dir)
    deltas: List[Mapping[str, Any]] = []
    warnings: List[str] = []
    if not runs_dir.exists():
        return TokenEfficiencyInsights(0, 0, 0, 0, warnings=(f"no runs dir: {runs_dir}",))
    for delta_file in sorted(runs_dir.glob("*/delta.json")):
        try:
            deltas.append(json.loads(delta_file.read_text(encoding="utf-8")))
        except (ValueError, OSError) as exc:
            warnings.append(f"unreadable {delta_file.name}: {exc}")
    agg = aggregate_delta_dicts(deltas)
    return TokenEfficiencyInsights(
        runs=agg.runs,
        input_before=agg.input_before,
        input_after=agg.input_after,
        saved=agg.saved,
        per_run=agg.per_run,
        warnings=agg.warnings + tuple(warnings),
    )


def aggregate_receipts(receipts: Sequence[Mapping[str, Any]]) -> dict:
    """Roll up token_efficiency + LLM-minimization usage from receipt dicts.

    Beyond token savings, counts *how many LLM calls were avoided* — the core
    "LLM minimization" metric: rule-resolved runs, llm-used runs, bypassed runs,
    resolution_mode distribution, and provider usage.
    """

    prev_saved = src_saved = applied = with_eff = 0
    total_runs = 0
    rule_resolved = llm_used = llm_bypassed = with_opt = 0
    mode_dist: dict[str, int] = {}
    provider_usage: dict[str, int] = {}

    for r in receipts:
        if not isinstance(r, Mapping):
            continue
        total_runs += 1
        te = r.get("token_efficiency")
        if isinstance(te, Mapping):
            with_eff += 1
            prev_saved += int(te.get("previous_decisions_saved", 0) or 0)
            src_saved += int(te.get("source_context_saved", 0) or 0)
            if te.get("compaction_applied"):
                applied += 1
        opt = r.get("optimization")
        if isinstance(opt, Mapping):
            with_opt += 1
            mode = str(opt.get("resolution_mode") or "unknown")
            mode_dist[mode] = mode_dist.get(mode, 0) + 1
            if mode == "rule_first":
                rule_resolved += 1
            if opt.get("llm_used"):
                llm_used += 1
            if opt.get("bypassed_live_llm"):
                llm_bypassed += 1
            prov = opt.get("selected_provider")
            if prov:
                provider_usage[str(prov)] = provider_usage.get(str(prov), 0) + 1

    llm_avoid_rate = round(llm_bypassed / with_opt * 100.0, 1) if with_opt else 0.0
    rule_first_rate = round(rule_resolved / with_opt * 100.0, 1) if with_opt else 0.0
    return {
        "total_runs": total_runs,
        # token savings
        "receipts_with_token_efficiency": with_eff,
        "previous_decisions_saved": prev_saved,
        "source_context_saved": src_saved,
        "compaction_applied_runs": applied,
        "total_saved": prev_saved + src_saved,
        # LLM minimization usage
        "receipts_with_optimization": with_opt,
        "rule_resolved_runs": rule_resolved,
        "llm_used_runs": llm_used,
        "llm_bypassed_runs": llm_bypassed,
        "rule_first_resolution_rate_pct": rule_first_rate,
        "live_llm_avoided_rate_pct": llm_avoid_rate,
        "resolution_mode_distribution": mode_dist,
        "provider_usage": provider_usage,
    }


def render_usage_markdown(usage: Mapping[str, Any]) -> str:
    lines = [
        "# LLM minimization usage",
        "",
        f"- runs with optimization data: {usage.get('receipts_with_optimization', 0)}",
        f"- **live LLM avoided: {usage.get('llm_bypassed_runs', 0)} runs "
        f"({usage.get('live_llm_avoided_rate_pct', 0)}%)**",
        f"- rule-first resolved: {usage.get('rule_resolved_runs', 0)} "
        f"({usage.get('rule_first_resolution_rate_pct', 0)}%)",
        f"- llm used: {usage.get('llm_used_runs', 0)} runs",
        f"- token saved (input hot path): {usage.get('total_saved', 0)}",
        "",
        "## resolution_mode distribution",
    ]
    for mode, n in sorted((usage.get("resolution_mode_distribution") or {}).items()):
        lines.append(f"- {mode}: {n}")
    lines.append("")
    lines.append("## provider usage")
    for prov, n in sorted((usage.get("provider_usage") or {}).items()):
        lines.append(f"- {prov}: {n}")
    return "\n".join(lines).rstrip() + "\n"


def render_markdown(insights: TokenEfficiencyInsights) -> str:
    lines = [
        "# Token efficiency insights (cumulative)",
        "",
        f"- runs: {insights.runs}",
        f"- **total input tokens: {insights.input_before} → {insights.input_after} "
        f"(−{insights.saved}, −{insights.reduction_pct}%)**",
        "",
        "| run | before | after | saved | reduction |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for r in insights.per_run:
        lines.append(
            f"| {r['slug']} | {r['before']} | {r['after']} | {r['saved']} | −{r['reduction_pct']}% |"
        )
    if insights.warnings:
        lines.append("")
        lines.append("## Warnings")
        for w in insights.warnings:
            lines.append(f"- {w}")
    return "\n".join(lines).rstrip() + "\n"


__all__ = (
    "TokenEfficiencyInsights",
    "aggregate_delta_dicts",
    "scan_token_efficiency_evidence",
    "aggregate_receipts",
    "render_markdown",
    "render_usage_markdown",
)
