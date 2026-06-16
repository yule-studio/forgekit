"""Status loaders — read-only adapters over the existing yule surfaces.

The console owns no business logic: it *reads* the runtime/harness/doctor
surfaces that already exist and shapes them into :class:`StatusSummary`. The
shaping helpers (``summarize_*``) are pure and unit-tested; the ``load_*``
functions do the best-effort IO (lazy ``yule_engineering`` imports wrapped in
try/except) so a missing runtime / partial install degrades to an alert instead
of crashing the console.

Reuse map:
  * operator dashboard → ``agents.harness.operator_surface.compose_dashboard``
  * runtime status     → ``runtime.status.build_runtime_status`` + text renderer
  * doctor             → ``diagnostics.doctor.run_doctor`` + report renderer
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional

from ..models import (
    LEVEL_ERROR,
    LEVEL_INFO,
    LEVEL_WARN,
    Alert,
    StatusSection,
    StatusSummary,
)

DEFAULT_AGENT_ID = "engineering-agent"


def _degraded(title: str, error: str) -> StatusSummary:
    return StatusSummary(
        title=title,
        available=False,
        error=error,
        alerts=(Alert(LEVEL_WARN, f"{title} 사용 불가: {error}"),),
    )


# --- pure shapers -----------------------------------------------------------


def summarize_operator_dashboard(dashboard: Mapping[str, Any]) -> StatusSummary:
    """Shape an ``operator_surface`` dashboard dict into a StatusSummary."""

    provider = dashboard.get("provider", {}) or {}
    si = dashboard.get("self_improvement", {}) or {}
    ev = dashboard.get("eval_summary", {}) or {}
    token = dashboard.get("token_efficiency", {}) or {}
    next_actions = tuple(dashboard.get("next_actions", []) or ())

    sections = [
        StatusSection("provider runtime", (
            f"live runs: {provider.get('live_provider_runs', 0)} / {provider.get('runs_with_runtime', 0)}",
            f"fallback: {provider.get('fallback_rate_pct', 0)}%  cost(proxy): ${provider.get('total_cost_usd', 0)}",
            f"rule-first: {provider.get('rule_first_resolution_rate_pct', 0)}%  "
            f"live-LLM avoided: {provider.get('live_llm_avoided_rate_pct', 0)}%",
        )),
        StatusSection("self-improvement", (
            f"detected: {si.get('detected', 0)}  delegated: {si.get('delegated', 0)}  "
            f"waiting: {si.get('waiting_operator', 0)}  blocked: {si.get('blocked', 0)}",
        )),
        StatusSection("eval gate", (
            ", ".join(ev.get("variants", []) or []) or "(no eval evidence)",
        )),
        StatusSection("token efficiency", (
            f"runs: {token.get('runs', 0)}  saved: {token.get('saved', 0)}  "
            f"reduction: {token.get('reduction_pct', 0)}%",
        )),
    ]

    alerts = []
    if int(si.get("waiting_operator", 0) or 0) > 0:
        alerts.append(Alert(LEVEL_WARN, f"{si['waiting_operator']} proposal(s) waiting operator"))
    if int(si.get("blocked", 0) or 0) > 0:
        alerts.append(Alert(LEVEL_ERROR, f"{si['blocked']} proposal(s) blocked"))
    try:
        if float(provider.get("fallback_rate_pct", 0) or 0) >= 50.0:
            alerts.append(Alert(LEVEL_WARN, f"provider fallback {provider['fallback_rate_pct']}% (high)"))
    except (TypeError, ValueError):
        pass
    if not alerts:
        alerts.append(Alert(LEVEL_INFO, "no anomalies detected"))

    return StatusSummary(
        title="operator dashboard",
        sections=tuple(sections),
        alerts=tuple(alerts),
        next_actions=next_actions,
    )


def summarize_text(title: str, text: str, *, max_lines: int = 40) -> StatusSummary:
    """Shape an arbitrary rendered surface (doctor/runtime text) into a summary."""

    lines = tuple(l.rstrip() for l in (text or "").splitlines() if l.strip())
    truncated = lines[:max_lines]
    extra = ()
    if len(lines) > max_lines:
        extra = (Alert(LEVEL_INFO, f"... {len(lines) - max_lines} more lines (truncated)"),)
    return StatusSummary(
        title=title,
        sections=(StatusSection(title, truncated),),
        alerts=extra,
    )


# --- best-effort IO loaders -------------------------------------------------


def load_operator_summary(repo_root: Path) -> StatusSummary:
    """Compose the harness/operator dashboard, reusing the existing surface."""

    try:
        from yule_engineering.agents.harness.operator_surface import compose_dashboard
        from yule_engineering.agents.harness.insights import scan_token_efficiency_evidence
    except Exception as exc:  # noqa: BLE001
        return _degraded("operator dashboard", f"import 실패: {exc}")
    try:
        token = scan_token_efficiency_evidence(Path(repo_root) / "runs" / "token-efficiency")
        eval_comparison = _load_latest_eval_comparison(Path(repo_root))
        self_improvement = _load_self_improvement_counts()
        dash = compose_dashboard(
            usage=None,
            eval_comparison=eval_comparison,
            self_improvement=self_improvement,
            token_insights=token.to_dict(),
        )
        return summarize_operator_dashboard(dash.to_dict())
    except Exception as exc:  # noqa: BLE001
        return _degraded("operator dashboard", str(exc))


def load_doctor_summary(repo_root: Path, agent_id: str = DEFAULT_AGENT_ID) -> StatusSummary:
    try:
        from yule_engineering.diagnostics.doctor import render_doctor_report, run_doctor
    except Exception as exc:  # noqa: BLE001
        return _degraded("doctor", f"import 실패: {exc}")
    try:
        checks = run_doctor(repo_root=Path(repo_root), agent_id=agent_id)
        return summarize_text("doctor", render_doctor_report(checks))
    except Exception as exc:  # noqa: BLE001
        return _degraded("doctor", str(exc))


def load_runtime_summary(repo_root: Path, *, db_path: Optional[Path] = None) -> StatusSummary:
    try:
        from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
        from yule_engineering.agents.job_queue.store import JobQueue
        from yule_engineering.runtime.status import (
            build_runtime_status,
            render_runtime_status_text,
        )
    except Exception as exc:  # noqa: BLE001
        return _degraded("runtime status", f"import 실패: {exc}")
    try:
        queue = JobQueue(db_path=db_path)
        heartbeats = HeartbeatStore(db_path=db_path)
        report = build_runtime_status(queue=queue, heartbeats=heartbeats)
        return summarize_text("runtime status", render_runtime_status_text(report))
    except Exception as exc:  # noqa: BLE001
        return _degraded("runtime status", str(exc))


def _load_latest_eval_comparison(repo_root: Path):
    evals = Path(repo_root) / "runs" / "evals"
    if not evals.exists():
        return None
    candidates = sorted(evals.glob("*/comparison.json"))
    if not candidates:
        return None
    try:
        return json.loads(candidates[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _load_self_improvement_counts():
    try:
        from yule_engineering.agents.lifecycle.problem_ledger import (
            ProblemLedger,
            default_ledger_path,
        )

        path = default_ledger_path()
        if not Path(path).exists():
            return None
        problems = ProblemLedger(ledger_path=Path(path)).all()
    except Exception:  # noqa: BLE001
        return None
    return {
        "recent_ticks": 0,
        "detected": len(problems),
        "delegated": sum(1 for p in problems if p.approval_scope == "delegated_ok"),
        "waiting_operator": sum(1 for p in problems if p.status.value == "waiting_operator"),
        "blocked": sum(1 for p in problems if p.status.value == "blocked"),
    }


__all__ = (
    "DEFAULT_AGENT_ID",
    "summarize_operator_dashboard",
    "summarize_text",
    "load_operator_summary",
    "load_doctor_summary",
    "load_runtime_summary",
)
