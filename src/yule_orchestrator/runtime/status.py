"""Runtime status surface — A-M6.3.

Read-only snapshot of the always-on engineering runtime so an
operator can see "is everything alive?" with one CLI invocation:

  yule runtime status --profile engineering [--json]

Reports three things, each derivable from the heartbeat store +
job queue without touching Discord or restart parents:

  * Per-service health — heartbeat age vs. deadline, with the
    inventory entries (``ENGINEERING_PROFILE``) annotated as
    ``alive`` / ``stale`` / ``unknown`` / ``reserved``.
  * Per-job-type queue counts — how many rows per state, oldest
    queued age, so a stuck queue is visible.
  * Recent failures — the most recent ``FAILED_RETRYABLE`` /
    ``FAILED_TERMINAL`` rows with their one-line error string so
    the operator can decide whether to requeue or escalate.

Out of scope (per A-M6.3 spec): supervisor restart counters
(in-process only, not persisted), ``#봇-상태`` Discord broadcast,
M7 fallback / circuit-break / degrade.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Tuple

from ..agents.job_queue.heartbeat import (
    DEFAULT_HEARTBEAT_DEADLINE_SECONDS,
    HeartbeatStore,
)
from ..agents.job_queue.state_machine import JobState
from ..agents.job_queue.store import Job, JobQueue
from .services import ServiceKind, ServiceSpec, list_services


# Health labels surfaced in renderer output. String constants so the
# ``--json`` consumer can match exact values without enum import.
HEALTH_ALIVE: str = "alive"
HEALTH_STALE: str = "stale"
HEALTH_UNKNOWN: str = "unknown"
HEALTH_RESERVED: str = "reserved"
# A-M7-final: circuit-open trumps the heartbeat-based labels because
# the supervisor parent has explicitly stopped restarting the service.
# A live ``alive`` heartbeat could otherwise mask the fact that the
# breaker is keeping the worker offline.
HEALTH_CIRCUIT_OPEN: str = "circuit_open"


# Map ServiceKind → expected job_type. ``None`` means "not a queue
# consumer" (supervisor watcher / Discord gateway). Used to annotate
# the per-service queue context in the report.
_KIND_TO_JOB_TYPE: Mapping[ServiceKind, Optional[str]] = {
    ServiceKind.RESEARCH_WORKER: "research_collect",
    ServiceKind.ROLE_WORKER: "role_take",
    ServiceKind.APPROVAL_WORKER: "approval_post",
    ServiceKind.OBSIDIAN_WRITER: "obsidian_write",
    ServiceKind.SUPERVISOR: None,
    ServiceKind.DISCORD_GATEWAY: None,
    ServiceKind.RESERVED_DISCORD_GATEWAY: None,
}


@dataclass(frozen=True)
class ServiceStatus:
    """One service row in the report."""

    service_id: str
    kind: str
    role: Optional[str]
    description: str
    implemented: bool
    health: str
    heartbeat_age_seconds: Optional[float]
    heartbeat_last_beat: Optional[float]
    pid: Optional[int]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    job_type: Optional[str] = None


@dataclass(frozen=True)
class JobTypeSummary:
    """Per-job-type queue snapshot."""

    job_type: str
    queued: int
    in_progress: int
    saved: int
    failed_retryable: int
    failed_terminal: int
    oldest_queued_age_seconds: Optional[float]


@dataclass(frozen=True)
class FailedJobSummary:
    """One recent failed row, condensed for status display."""

    job_id: str
    job_type: str
    role: Optional[str]
    state: str
    attempt: int
    age_seconds: float
    error: Optional[str]


@dataclass(frozen=True)
class RuntimeStatusReport:
    """Top-level snapshot returned by :func:`build_runtime_status`."""

    profile: str
    generated_at: float
    deadline_seconds: float
    services: Tuple[ServiceStatus, ...]
    job_types: Tuple[JobTypeSummary, ...]
    failed_recent: Tuple[FailedJobSummary, ...]
    warnings: Tuple[str, ...]


# States the renderer treats as "in flight" (worker has the row but
# it's not done yet). Kept here so a future state addition only
# touches one tuple.
_IN_PROGRESS_STATES: Tuple[JobState, ...] = (
    JobState.ASSIGNED,
    JobState.IN_PROGRESS,
    JobState.RESEARCHING,
    JobState.PENDING_APPROVAL,
    JobState.READY_FOR_OBSIDIAN,
    JobState.WAITING_FOR_ROLE,
)


def build_runtime_status(
    *,
    profile: str = "engineering",
    queue: JobQueue,
    heartbeats: HeartbeatStore,
    deadline_seconds: float = DEFAULT_HEARTBEAT_DEADLINE_SECONDS,
    failed_limit: int = 10,
    now: Optional[float] = None,
    circuit_snapshots: Optional[Mapping[str, Any]] = None,
) -> RuntimeStatusReport:
    """Snapshot the runtime into a :class:`RuntimeStatusReport`.

    Pure read — no SQL writes, no state transitions. The function
    consults the inventory + heartbeat store + queue read APIs and
    returns a frozen report the renderer can stringify.
    """

    now_ts = now if now is not None else time.time()
    specs = list_services(profile)

    heartbeat_index: dict[str, Any] = {
        record.service_id: record for record in heartbeats.list_all()
    }

    circuit_map = dict(circuit_snapshots) if circuit_snapshots else {}
    services: list[ServiceStatus] = []
    for spec in specs:
        svc = _build_service_status(
            spec=spec,
            heartbeat=heartbeat_index.get(spec.service_id),
            deadline_seconds=deadline_seconds,
            now_ts=now_ts,
        )
        # A-M7-final: persisted circuit-open trumps heartbeat health.
        # Operator must see "supervisor stopped this on purpose"
        # rather than a green "alive" line.
        snap = circuit_map.get(spec.service_id)
        if snap is not None and getattr(snap, "is_open", False):
            svc = ServiceStatus(
                service_id=svc.service_id,
                kind=svc.kind,
                role=svc.role,
                description=svc.description,
                implemented=svc.implemented,
                health=HEALTH_CIRCUIT_OPEN,
                heartbeat_age_seconds=svc.heartbeat_age_seconds,
                heartbeat_last_beat=svc.heartbeat_last_beat,
                pid=svc.pid,
                metadata=dict(svc.metadata),
                job_type=svc.job_type,
            )
        services.append(svc)

    job_types = _build_job_type_summaries(queue=queue, now_ts=now_ts)
    failed_recent = _build_failed_recent(
        queue=queue, limit=failed_limit, now_ts=now_ts
    )
    warnings = _build_warnings(
        services=services,
        job_types=job_types,
        failed_recent=failed_recent,
    )

    return RuntimeStatusReport(
        profile=profile,
        generated_at=now_ts,
        deadline_seconds=float(deadline_seconds),
        services=tuple(services),
        job_types=tuple(job_types),
        failed_recent=tuple(failed_recent),
        warnings=tuple(warnings),
    )


def _build_service_status(
    *,
    spec: ServiceSpec,
    heartbeat: Any,
    deadline_seconds: float,
    now_ts: float,
) -> ServiceStatus:
    job_type = _KIND_TO_JOB_TYPE.get(spec.kind)

    if not spec.is_implemented():
        return ServiceStatus(
            service_id=spec.service_id,
            kind=spec.kind.value,
            role=spec.role,
            description=spec.description,
            implemented=False,
            health=HEALTH_RESERVED,
            heartbeat_age_seconds=None,
            heartbeat_last_beat=None,
            pid=None,
            metadata={},
            job_type=job_type,
        )

    if heartbeat is None:
        return ServiceStatus(
            service_id=spec.service_id,
            kind=spec.kind.value,
            role=spec.role,
            description=spec.description,
            implemented=True,
            health=HEALTH_UNKNOWN,
            heartbeat_age_seconds=None,
            heartbeat_last_beat=None,
            pid=None,
            metadata={},
            job_type=job_type,
        )

    age = max(0.0, now_ts - float(heartbeat.last_beat))
    health = HEALTH_ALIVE if age <= max(1.0, float(deadline_seconds)) else HEALTH_STALE
    return ServiceStatus(
        service_id=spec.service_id,
        kind=spec.kind.value,
        role=spec.role,
        description=spec.description,
        implemented=True,
        health=health,
        heartbeat_age_seconds=age,
        heartbeat_last_beat=float(heartbeat.last_beat),
        pid=heartbeat.pid,
        metadata=dict(heartbeat.metadata or {}),
        job_type=job_type,
    )


def _build_job_type_summaries(
    *,
    queue: JobQueue,
    now_ts: float,
) -> list[JobTypeSummary]:
    counts = queue.count_by_type_and_state()
    oldest = queue.oldest_queued_at_per_type()

    job_types_seen: set[str] = {jt for (jt, _state) in counts.keys()}
    summaries: list[JobTypeSummary] = []
    for job_type in sorted(job_types_seen):
        queued = counts.get((job_type, JobState.QUEUED.value), 0)
        in_progress = sum(
            counts.get((job_type, st.value), 0) for st in _IN_PROGRESS_STATES
        )
        saved = counts.get((job_type, JobState.SAVED.value), 0)
        failed_retryable = counts.get(
            (job_type, JobState.FAILED_RETRYABLE.value), 0
        )
        failed_terminal = counts.get(
            (job_type, JobState.FAILED_TERMINAL.value), 0
        )
        oldest_at = oldest.get(job_type)
        oldest_age = (
            max(0.0, now_ts - float(oldest_at)) if oldest_at is not None else None
        )
        summaries.append(
            JobTypeSummary(
                job_type=job_type,
                queued=queued,
                in_progress=in_progress,
                saved=saved,
                failed_retryable=failed_retryable,
                failed_terminal=failed_terminal,
                oldest_queued_age_seconds=oldest_age,
            )
        )
    return summaries


def _build_failed_recent(
    *,
    queue: JobQueue,
    limit: int,
    now_ts: float,
) -> list[FailedJobSummary]:
    failed: list[FailedJobSummary] = []
    for job in queue.recent_failed(limit=limit):
        error = _extract_error(job)
        failed.append(
            FailedJobSummary(
                job_id=job.job_id,
                job_type=job.job_type,
                role=job.role,
                state=job.state.value,
                attempt=job.attempt,
                age_seconds=max(0.0, now_ts - float(job.updated_at)),
                error=error,
            )
        )
    return failed


def _extract_error(job: Job) -> Optional[str]:
    """Pull the worker's one-line error string out of ``result_json``.

    Workers stamp ``result["error"]`` with a stable constant or the
    ``Type: short message`` shape from ``_short_error``. Detail
    fields (``detail``) follow but stay separate so the status
    renderer can keep the line short.
    """

    if not isinstance(job.result, Mapping):
        return None
    error = job.result.get("error")
    if not error:
        return None
    text = str(error).strip()
    return text or None


def _build_warnings(
    *,
    services: Sequence[ServiceStatus],
    job_types: Sequence[JobTypeSummary],
    failed_recent: Sequence[FailedJobSummary],
) -> list[str]:
    """Surface conditions an operator should act on.

    Kept short — three lines on a 80-col terminal beats a wall of
    text. Each warning is a single sentence.
    """

    warnings: list[str] = []
    circuit_open = [s for s in services if s.health == HEALTH_CIRCUIT_OPEN]
    if circuit_open:
        warnings.append(
            "circuit open (supervisor stopped restarting): "
            + ", ".join(s.service_id for s in circuit_open)
            + " — run `yule runtime circuit reset <service_id>` to clear"
        )
    stale = [s for s in services if s.health == HEALTH_STALE]
    if stale:
        warnings.append(
            "stale heartbeat: " + ", ".join(s.service_id for s in stale)
        )
    unknown_implemented = [
        s for s in services if s.health == HEALTH_UNKNOWN and s.implemented
    ]
    if unknown_implemented:
        warnings.append(
            "no heartbeat (worker may not be running): "
            + ", ".join(s.service_id for s in unknown_implemented)
        )
    failed_terminal_total = sum(j.failed_terminal for j in job_types)
    if failed_terminal_total > 0:
        warnings.append(
            f"{failed_terminal_total} failed_terminal job(s) — "
            "operator review required (no automatic retry)"
        )
    return warnings


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_runtime_status_text(report: RuntimeStatusReport) -> str:
    """Human-readable single-screen render."""

    lines: list[str] = []
    lines.append(f"profile: {report.profile}")
    lines.append(
        f"generated_at: {_fmt_unix(report.generated_at)} "
        f"(heartbeat deadline: {_fmt_seconds(report.deadline_seconds)})"
    )
    lines.append("")

    lines.append("services:")
    if not report.services:
        lines.append("  (none)")
    else:
        for svc in report.services:
            lines.append("  " + _format_service_line(svc))
    lines.append("")

    lines.append("queue:")
    if not report.job_types:
        lines.append("  (no jobs in queue)")
    else:
        for jt in report.job_types:
            lines.append("  " + _format_job_type_line(jt))
    lines.append("")

    lines.append("recent failures:")
    if not report.failed_recent:
        lines.append("  (none)")
    else:
        for fj in report.failed_recent:
            lines.append("  " + _format_failed_line(fj))

    if report.warnings:
        lines.append("")
        lines.append("warnings:")
        for warning in report.warnings:
            lines.append(f"  ! {warning}")

    return "\n".join(lines)


def render_runtime_status_json(report: RuntimeStatusReport) -> str:
    """Stable JSON render for ``--json``.

    Keys mirror the dataclass field names so a downstream consumer
    can parse with minimal mapping. ``ensure_ascii=False`` so
    Korean role labels survive the round trip when redirected to a
    file.
    """

    payload = {
        "profile": report.profile,
        "generated_at": report.generated_at,
        "deadline_seconds": report.deadline_seconds,
        "services": [
            {
                "service_id": s.service_id,
                "kind": s.kind,
                "role": s.role,
                "description": s.description,
                "implemented": s.implemented,
                "health": s.health,
                "heartbeat_age_seconds": s.heartbeat_age_seconds,
                "heartbeat_last_beat": s.heartbeat_last_beat,
                "pid": s.pid,
                "metadata": dict(s.metadata),
                "job_type": s.job_type,
            }
            for s in report.services
        ],
        "job_types": [
            {
                "job_type": j.job_type,
                "queued": j.queued,
                "in_progress": j.in_progress,
                "saved": j.saved,
                "failed_retryable": j.failed_retryable,
                "failed_terminal": j.failed_terminal,
                "oldest_queued_age_seconds": j.oldest_queued_age_seconds,
            }
            for j in report.job_types
        ],
        "failed_recent": [
            {
                "job_id": f.job_id,
                "job_type": f.job_type,
                "role": f.role,
                "state": f.state,
                "attempt": f.attempt,
                "age_seconds": f.age_seconds,
                "error": f.error,
            }
            for f in report.failed_recent
        ],
        "warnings": list(report.warnings),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _format_service_line(svc: ServiceStatus) -> str:
    # circuit_open is one char wider than the other labels; widen the
    # column so columns stay aligned without truncating.
    health = svc.health.upper().ljust(13)
    role_part = f" role={svc.role}" if svc.role else ""
    pid_part = f" pid={svc.pid}" if svc.pid is not None else ""
    age_part = (
        f" beat={_fmt_seconds(svc.heartbeat_age_seconds)} ago"
        if svc.heartbeat_age_seconds is not None
        else " beat=—"
    )
    job_part = f" jt={svc.job_type}" if svc.job_type else ""
    return f"{health} {svc.service_id}{role_part}{job_part}{age_part}{pid_part}"


def _format_job_type_line(jt: JobTypeSummary) -> str:
    oldest = (
        f" oldest_queued={_fmt_seconds(jt.oldest_queued_age_seconds)} ago"
        if jt.oldest_queued_age_seconds is not None
        else ""
    )
    return (
        f"{jt.job_type:<22} queued={jt.queued} "
        f"in_progress={jt.in_progress} saved={jt.saved} "
        f"failed_retryable={jt.failed_retryable} "
        f"failed_terminal={jt.failed_terminal}{oldest}"
    )


def _format_failed_line(fj: FailedJobSummary) -> str:
    role_part = f" role={fj.role}" if fj.role else ""
    error_part = f" — {fj.error}" if fj.error else ""
    return (
        f"[{fj.state}] {fj.job_id} "
        f"job_type={fj.job_type}{role_part} attempt={fj.attempt} "
        f"age={_fmt_seconds(fj.age_seconds)}{error_part}"
    )


def _fmt_seconds(value: Optional[float]) -> str:
    if value is None:
        return "—"
    seconds = float(value)
    if seconds < 1.0:
        return f"{seconds:.2f}s"
    if seconds < 60.0:
        return f"{seconds:.1f}s"
    minutes = seconds / 60.0
    if minutes < 60.0:
        return f"{minutes:.1f}m"
    hours = minutes / 60.0
    if hours < 24.0:
        return f"{hours:.1f}h"
    days = hours / 24.0
    return f"{days:.1f}d"


def _fmt_unix(value: float) -> str:
    """ISO-ish UTC for stable rendering across operator machines."""

    from datetime import datetime, timezone

    return (
        datetime.fromtimestamp(float(value), tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )


__all__ = (
    "FailedJobSummary",
    "HEALTH_ALIVE",
    "HEALTH_RESERVED",
    "HEALTH_STALE",
    "HEALTH_UNKNOWN",
    "JobTypeSummary",
    "RuntimeStatusReport",
    "ServiceStatus",
    "build_runtime_status",
    "render_runtime_status_json",
    "render_runtime_status_text",
)
