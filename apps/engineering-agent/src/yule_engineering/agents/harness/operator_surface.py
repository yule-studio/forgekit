"""Operator surface — one dashboard that says "what's happening, what to do next".

WT4. The three axes (live-provider runtime, self-improvement loop, eval gate)
each emit their own telemetry; an operator should not have to stitch them
together. :func:`compose_dashboard` folds the already-aggregated signals into a
single view + a **rule-derived "next actions"** list, so the surface answers the
operator's real question: *given the current state, what should I do?*

Pure + deterministic — callers load the inputs (insights usage roll-up, latest
eval comparison, self-improvement proposal count, token insights) and pass them
in. No disk / clock here; the CLI does the loading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class OperatorDashboard:
    provider: Mapping[str, Any]
    self_improvement: Mapping[str, Any]
    eval_summary: Mapping[str, Any]
    token_efficiency: Mapping[str, Any]
    next_actions: Tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "provider": dict(self.provider),
            "self_improvement": dict(self.self_improvement),
            "eval_summary": dict(self.eval_summary),
            "token_efficiency": dict(self.token_efficiency),
            "next_actions": list(self.next_actions),
        }


def derive_next_actions(
    *,
    usage: Optional[Mapping[str, Any]],
    eval_comparison: Optional[Mapping[str, Any]],
    self_improvement: Optional[Mapping[str, Any]],
) -> Tuple[str, ...]:
    """Rule-derived operator hints. Most-actionable first; empty → all-clear."""

    actions: List[str] = []

    si = self_improvement or {}
    waiting = int(si.get("waiting_operator", 0) or 0)
    if waiting > 0:
        actions.append(
            f"{waiting} self-improvement proposal(s) WAITING operator — respond "
            "(승인/반려/보류) in #승인-대기."
        )
    blocked = int(si.get("blocked", 0) or 0)
    if blocked > 0:
        actions.append(f"{blocked} proposal(s) BLOCKED — inspect troubleshooting ledger.")

    u = usage or {}
    if u.get("receipts_with_provider_runtime"):
        fb = float(u.get("provider_fallback_rate_pct", 0) or 0)
        if fb >= 50.0:
            actions.append(
                f"provider fallback rate {fb}% (high) — check provider availability/auth "
                "via `yule runtime status`; live providers may be unconfigured."
            )
        fdist = u.get("provider_failure_distribution") or {}
        if "cli_not_found" in fdist:
            actions.append("a provider CLI is missing on PATH — install or disable it.")
        if "endpoint_unreachable" in fdist:
            actions.append("an Ollama endpoint is unreachable — start it or unset the provider.")

    if not eval_comparison:
        actions.append("no eval evidence found — run `yule harness eval` to baseline the gate.")

    if not actions:
        actions.append("all clear — no operator action required.")
    return tuple(actions)


def _provider_block(usage: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    u = usage or {}
    return {
        "live_provider_runs": u.get("live_provider_runs", 0),
        "runs_with_runtime": u.get("receipts_with_provider_runtime", 0),
        "fallback_rate_pct": u.get("provider_fallback_rate_pct", 0.0),
        "avg_latency_ms": u.get("avg_latency_ms"),
        "total_cost_usd": u.get("total_cost_usd", 0.0),
        "rule_first_resolution_rate_pct": u.get("rule_first_resolution_rate_pct", 0.0),
        "live_llm_avoided_rate_pct": u.get("live_llm_avoided_rate_pct", 0.0),
        "provider_usage": u.get("provider_usage", {}),
        "failure_distribution": u.get("provider_failure_distribution", {}),
    }


def _eval_block(eval_comparison: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    if not eval_comparison:
        return {"available": False}
    rows = eval_comparison.get("comparison", []) or []
    return {
        "available": True,
        "schema_version": eval_comparison.get("schema_version"),
        "variants": [r.get("variant") for r in rows],
        "rows": rows,
    }


def compose_dashboard(
    *,
    usage: Optional[Mapping[str, Any]] = None,
    eval_comparison: Optional[Mapping[str, Any]] = None,
    self_improvement: Optional[Mapping[str, Any]] = None,
    token_insights: Optional[Mapping[str, Any]] = None,
) -> OperatorDashboard:
    si = self_improvement or {}
    return OperatorDashboard(
        provider=_provider_block(usage),
        self_improvement={
            "recent_ticks": si.get("recent_ticks", 0),
            "detected": si.get("detected", 0),
            "delegated": si.get("delegated", 0),
            "waiting_operator": si.get("waiting_operator", 0),
            "blocked": si.get("blocked", 0),
        },
        eval_summary=_eval_block(eval_comparison),
        token_efficiency={
            "runs": (token_insights or {}).get("runs", 0),
            "saved": (token_insights or {}).get("saved", 0),
            "reduction_pct": (token_insights or {}).get("reduction_pct", 0.0),
        },
        next_actions=derive_next_actions(
            usage=usage, eval_comparison=eval_comparison, self_improvement=self_improvement
        ),
    )


def render_dashboard_markdown(dash: OperatorDashboard) -> str:
    p = dash.provider
    e = dash.eval_summary
    si = dash.self_improvement
    t = dash.token_efficiency
    lines = [
        "# Operator dashboard",
        "",
        "## Provider runtime",
        f"- live runs: {p['live_provider_runs']} / {p['runs_with_runtime']}",
        f"- fallback rate: {p['fallback_rate_pct']}%  |  avg latency: {p['avg_latency_ms']} ms  "
        f"|  cost(proxy): ${p['total_cost_usd']}",
        f"- rule-first resolution: {p['rule_first_resolution_rate_pct']}%  |  "
        f"live-LLM avoided: {p['live_llm_avoided_rate_pct']}%",
        "",
        "## Self-improvement loop",
        f"- recent ticks: {si['recent_ticks']}  |  detected: {si['detected']}  |  "
        f"delegated: {si['delegated']}  |  waiting operator: {si['waiting_operator']}  |  "
        f"blocked: {si['blocked']}",
        "",
        "## Eval gate",
    ]
    if e.get("available"):
        lines.append(f"- schema {e.get('schema_version')}  variants: {', '.join(e.get('variants') or [])}")
        for row in e.get("rows", []):
            lines.append(
                f"  · {row.get('variant')}: success={row.get('success_rate_pct')}% "
                f"cost=${row.get('total_cost_usd')} latency={row.get('avg_latency_ms')}ms "
                f"rule-first={row.get('rule_first_ratio_pct')}%"
            )
    else:
        lines.append("- (no eval evidence — run `yule harness eval`)")
    lines += [
        "",
        "## Token efficiency",
        f"- runs: {t['runs']}  saved: {t['saved']}  reduction: {t['reduction_pct']}%",
        "",
        "## What to do next",
    ]
    for action in dash.next_actions:
        lines.append(f"- {action}")
    return "\n".join(lines).rstrip() + "\n"


__all__ = (
    "OperatorDashboard",
    "derive_next_actions",
    "compose_dashboard",
    "render_dashboard_markdown",
)
