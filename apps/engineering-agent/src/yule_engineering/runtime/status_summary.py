"""Status summary markdown formatter — A-M7 foundation for `#봇-상태`.

Produces the markdown blob a future M7.x ``#봇-상태`` poster can
hand to Discord without further shaping. Today this module is
*formatter only* — no Discord client, no posting. Splitting the
formatter from the poster keeps the rendered output testable and
lets the M6.3 status CLI also surface a markdown view if the
operator asks for one.

Inputs are read-only views from M6.3 + M7 modules:

  * :class:`~runtime.status.RuntimeStatusReport` — heartbeat /
    queue / failed_recent.
  * :class:`~runtime.circuit_breaker.CircuitSnapshot` — per-service
    breaker state.
  * Recent fallback audit entries — usually pulled from
    ``session.extra['fallback_audits']`` by the caller.

The formatter returns markdown that's safe to paste into Discord
(no Discord-specific markup beyond fences) and intentionally
**short**: a single message-sized blob, four sections, one bullet
per item with stable ordering so the operator can diff between
two snapshots.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional, Sequence

from yule_runtime.circuit_breaker import CircuitSnapshot
from .fallback import FallbackAuditRecord
from .status import (
    HEALTH_STALE,
    HEALTH_UNKNOWN,
    RuntimeStatusReport,
    ServiceStatus,
)


_SECTION_HEADERS = {
    "stale": "### Stale services",
    "circuit": "### Circuit-open services",
    "failed_terminal": "### Failed-terminal jobs",
    "fallback": "### Fallback / degrade events",
}


def render_status_summary_markdown(
    *,
    report: RuntimeStatusReport,
    circuits: Mapping[str, CircuitSnapshot] = (),  # type: ignore[assignment]
    fallbacks: Sequence[FallbackAuditRecord] = (),
    profile_label: Optional[str] = None,
    max_failed_jobs: int = 5,
    max_fallbacks: int = 5,
) -> str:
    """Format a single-message markdown summary.

    Returns a multi-line markdown string. When nothing is wrong
    (no stale, no circuit-open, no failed_terminal, no fallback)
    the body is a single "all clear" line so a poster can decide
    whether to skip the post.
    """

    lines: list[str] = []
    label = profile_label or report.profile
    lines.append(f"## 🛰️ runtime status — `{label}`")
    lines.append(f"_generated_at: {_iso(report.generated_at)}_")

    stale_section = _render_stale_services(report.services)
    circuit_section = _render_circuits(circuits)
    failed_terminal_section = _render_failed_terminal(
        report.failed_recent, limit=max_failed_jobs
    )
    fallback_section = _render_fallbacks(fallbacks, limit=max_fallbacks)

    body_sections = [
        s
        for s in (
            stale_section,
            circuit_section,
            failed_terminal_section,
            fallback_section,
        )
        if s
    ]
    if not body_sections:
        lines.append("")
        lines.append("✅ 모든 서비스 alive · 큐 정상 · fallback 없음")
        return "\n".join(lines)

    for section in body_sections:
        lines.append("")
        lines.append(section)
    return "\n".join(lines)


def _render_stale_services(
    services: Sequence[ServiceStatus],
) -> Optional[str]:
    """List ``stale`` + ``unknown`` services as one section.

    Both states deserve operator attention but for different
    reasons (stale = was alive, went quiet; unknown = never
    reported). The bullets carry the distinction so the operator
    knows whether to journalctl or check whether the service was
    ever started.
    """

    interesting = [
        s
        for s in services
        if s.health in (HEALTH_STALE, HEALTH_UNKNOWN) and s.implemented
    ]
    if not interesting:
        return None
    lines = [_SECTION_HEADERS["stale"]]
    for svc in interesting:
        if svc.health == HEALTH_STALE:
            age = _fmt_age(svc.heartbeat_age_seconds)
            lines.append(
                f"- `{svc.service_id}` — stale (last beat {age})"
            )
        else:
            lines.append(
                f"- `{svc.service_id}` — unknown (no heartbeat ever)"
            )
    return "\n".join(lines)


def _render_circuits(
    circuits: Mapping[str, CircuitSnapshot],
) -> Optional[str]:
    if not circuits:
        return None
    open_circuits = [c for c in circuits.values() if c.is_open]
    if not open_circuits:
        return None
    lines = [_SECTION_HEADERS["circuit"]]
    for snap in sorted(open_circuits, key=lambda s: s.service_id):
        reason = snap.last_reason or "threshold tripped"
        opened_at_text = (
            f" (opened {_iso(snap.opened_at)})"
            if snap.opened_at is not None
            else ""
        )
        lines.append(
            f"- `{snap.service_id}` — circuit OPEN · "
            f"{snap.restart_count_in_window} restarts in window · "
            f"{reason}{opened_at_text}"
        )
    return "\n".join(lines)


def _render_failed_terminal(
    failed_recent: Sequence,
    *,
    limit: int,
) -> Optional[str]:
    terminal = [f for f in failed_recent if f.state == "failed_terminal"]
    if not terminal:
        return None
    lines = [_SECTION_HEADERS["failed_terminal"]]
    for fj in terminal[:limit]:
        role_part = f" role=`{fj.role}`" if fj.role else ""
        error_part = f" — {fj.error}" if fj.error else ""
        lines.append(
            f"- `{fj.job_id}` job_type=`{fj.job_type}`"
            f"{role_part} attempt={fj.attempt}{error_part}"
        )
    if len(terminal) > limit:
        lines.append(f"- … +{len(terminal) - limit}건 더")
    return "\n".join(lines)


def _render_fallbacks(
    fallbacks: Sequence[FallbackAuditRecord],
    *,
    limit: int,
) -> Optional[str]:
    if not fallbacks:
        return None
    lines = [_SECTION_HEADERS["fallback"]]
    for record in list(fallbacks)[:limit]:
        approval_flag = (
            " ⚠ human approval required"
            if record.human_approval_required
            else ""
        )
        roles_summary = (
            ", ".join(record.failed_roles)
            if record.failed_roles
            else "—"
        )
        lines.append(
            f"- `{record.fallback_id}` session=`{record.session_id}` "
            f"authority=`{record.fallback_authority}` "
            f"failed_roles=[{roles_summary}]"
            f"{approval_flag}"
        )
        lines.append(f"  · {record.reason}")
    if len(fallbacks) > limit:
        lines.append(f"- … +{len(fallbacks) - limit}건 더")
    return "\n".join(lines)


def _fmt_age(value: Optional[float]) -> str:
    if value is None:
        return "—"
    seconds = float(value)
    if seconds < 60.0:
        return f"{seconds:.0f}s ago"
    minutes = seconds / 60.0
    if minutes < 60.0:
        return f"{minutes:.1f}m ago"
    hours = minutes / 60.0
    return f"{hours:.1f}h ago"


def _iso(value: Optional[float]) -> str:
    if value is None:
        return "—"
    from datetime import datetime, timezone

    return (
        datetime.fromtimestamp(float(value), tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )


__all__ = ("render_status_summary_markdown",)
