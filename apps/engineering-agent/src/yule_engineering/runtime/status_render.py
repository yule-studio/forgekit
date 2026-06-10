"""Runtime status — renderers (text / JSON / compact / markdown).

Extracted from :mod:`runtime.status` (split axis ``renderer``). Owns the
formatting of a :class:`RuntimeStatusReport` into the operator-facing
surfaces:

  * ``render_runtime_status_text`` — human-readable single-screen view.
  * ``render_runtime_status_json`` — stable ``--json`` payload.
  * ``render_runtime_status_compact`` — ≤6-line digest for log lines.
  * ``render_autonomy_summary_markdown`` — the autonomy / funnel
    markdown sections for the ``#봇-상태`` Discord post.
  * the live-smoke checklist + the per-row formatting helpers.

All renderers are pure reads over the report + the operator-action
projection; no state is mutated here.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional, Sequence, Tuple

from .status import (
    AUTONOMY_OUTCOME_DEDUPED,
    AUTONOMY_OUTCOME_DISPATCHED,
    AUTONOMY_OUTCOME_ERROR,
    AUTONOMY_OUTCOME_LOCKED,
    AUTONOMY_OUTCOME_SKIPPED,
    AutonomyDispatchSummary,
    AutonomyTickSummary,
    CompletionFunnelSummary,
    FailedJobSummary,
    JobTypeSummary,
    RuntimeStatusReport,
    ServiceStatus,
)
from .status_operator_actions import (
    CompactStatusSummary,
    OperatorAction,
    build_compact_status_summary,
    summarize_operator_actions,
)


def render_runtime_status_compact(
    report: "RuntimeStatusReport",
    *,
    actions: Optional[Sequence[OperatorAction]] = None,
) -> str:
    """Return the ≤6-line compact digest for ``yule runtime status --compact``.

    Designed to be safe to log every supervisor watch tick — short,
    no Discord-only markdown, copy-pasteable into Slack/journalctl
    without further shaping.
    """

    summary = build_compact_status_summary(report, actions=actions)
    lines: list[str] = []
    lines.append(f"🛰 runtime[{summary.profile}] @ {_fmt_unix(report.generated_at)}")
    lines.append(
        f"  services: {summary.services_alive} alive · "
        f"{summary.services_stale} stale · "
        f"{summary.services_unknown} unknown · "
        f"{summary.services_circuit_open} circuit_open"
    )
    lines.append(
        f"  queue: {summary.queue_in_progress} in_progress · "
        f"{summary.queue_failed_retryable} failed_retryable · "
        f"{summary.queue_failed_terminal} failed_terminal"
    )
    lines.append(
        f"  autonomy: {summary.autonomy_ticks_recent} ticks · "
        f"{summary.autonomy_ticks_errored} errored · "
        f"{summary.autonomy_locked_dispatches} locked"
    )
    lines.append(
        f"  funnel: {summary.funnel_done} done · "
        f"{summary.funnel_retry_ready} retry_ready · "
        f"{summary.funnel_needs_approval} needs_approval · "
        f"{summary.funnel_blocked} blocked"
    )
    if summary.top_action is None:
        lines.append("  next: ✅ no operator action required")
    else:
        action = summary.top_action
        more = (
            f" (+{summary.actions_total - 1} more)"
            if summary.actions_total > 1
            else ""
        )
        lines.append(
            f"  next: {action.icon or '!'} [{action.severity}] "
            f"{action.headline} → {action.next_step}{more}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Live smoke checklist
# ---------------------------------------------------------------------------
#
# A short, deterministic "what to check next" block appended to the
# text render. Operators see the same screen they used to derive
# health from, plus the exact commands they should run to confirm a
# real Discord smoke pass (see docs/discord.md §10).


_LIVE_SMOKE_CHECKLIST: Tuple[str, ...] = (
    "1. `yule runtime up --dry-run` — confirm 12 services planned "
    "(1 supervisor + 1 research + 7 role + approval + obsidian + "
    "gateway).",
    "2. `yule runtime up` (this terminal) or `systemctl start "
    "yule.target` (systemd) — start the runtime parent / units.",
    "3. `yule runtime status --profile engineering` — every service "
    "should be ALIVE; STALE/UNKNOWN warnings list the exact restart "
    "command.",
    "4. `#업무-접수` test message → eng-discord-gateway enqueues "
    "research_collect → eng-research-worker pulls it → role workers "
    "produce takes (queue counts move through queued→saved).",
    "5. Reply `이대로 저장` in `#승인-대기` → eng-approval-worker "
    "ingests reply → eng-obsidian-writer writes vault note. Verify "
    "with `yule runtime status` (obsidian_write saved += 1) + the "
    "new file under OBSIDIAN_VAULT_PATH.",
    "6. Trip a worker on purpose (kill `eng-role-tech-lead`) → status "
    "must show STALE → restart hint above must list the exact unit "
    "id. Failure here means the operator hint regressed.",
)


def render_live_smoke_checklist(
    report: Optional[RuntimeStatusReport] = None,
) -> str:
    """Return the live-smoke checklist as a numbered text block.

    *report* is accepted but currently unused — passing it lets a
    future revision tailor lines to the actual deployment (e.g. omit
    the `systemctl` line on macOS dev hosts). Today the block is
    deployment-agnostic so the operator gets the same checklist
    whichever environment they run from.
    """

    lines = ["live smoke checklist:"]
    for item in _LIVE_SMOKE_CHECKLIST:
        lines.append(f"  {item}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_runtime_status_text(report: RuntimeStatusReport) -> str:
    """Human-readable single-screen render.

    Section order:

    1. ``profile`` / ``generated_at`` header.
    2. ``services`` — one line per service: health, id, role, queue
       job_type, heartbeat age, pid + a short description sub-line so
       the operator sees what the service actually does.
    3. ``queue`` — per-job-type summary.
    4. ``recent failures`` — most recent first.
    5. ``warnings`` — actionable next-step (with exact commands for
       STALE/UNKNOWN/circuit-open).
    6. ``live smoke checklist`` — deterministic 6-step verification
       block so the operator can copy commands from one screen.
    """

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
            if svc.description:
                lines.append("    handles: " + svc.description)
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

    lines.append("")
    lines.append("autonomy producer:")
    if not report.autonomy_recent:
        lines.append("  (no recent ticks recorded)")
    else:
        for tick in report.autonomy_recent:
            lines.append("  " + _format_autonomy_tick_line(tick))
            for d in tick.dispatches:
                lines.append("    " + _format_dispatch_line(d))
    if report.autonomy_locks_held:
        lines.append(
            "  locks held: " + ", ".join(report.autonomy_locks_held)
        )

    lines.append("")
    lines.append("completion funnel:")
    if not report.completion_funnel_recent:
        lines.append("  (no recent completions)")
    else:
        for c in report.completion_funnel_recent:
            lines.append("  " + _format_funnel_line(c))

    actions = summarize_operator_actions(report)
    lines.append("")
    lines.append("operator actions:")
    if not actions:
        lines.append("  ✅ no operator action required")
    else:
        for action in actions:
            lines.append("  " + _format_operator_action_line(action))

    if report.warnings:
        lines.append("")
        lines.append("warnings:")
        for warning in report.warnings:
            lines.append(f"  ! {warning}")

    lines.append("")
    lines.append(render_live_smoke_checklist(report))

    return "\n".join(lines)


def _format_operator_action_line(action: OperatorAction) -> str:
    icon = action.icon or "!"
    affected = ""
    if action.affected:
        head = ", ".join(action.affected[:3])
        if len(action.affected) > 3:
            head += f" (+{len(action.affected) - 3} more)"
        affected = f" affected={head}"
    return (
        f"{icon} [{action.severity}] {action.kind} — {action.headline}"
        f"{affected}\n      → {action.next_step}"
    )


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
        "autonomy_recent": [
            {
                "tick_id": t.tick_id,
                "started_at": t.started_at,
                "finished_at": t.finished_at,
                "next_task_source": t.next_task_source,
                "summary_line": t.summary_line,
                "error": t.error,
                "dispatches": [
                    {
                        "source": d.source,
                        "outcome": d.outcome,
                        "session_id": d.session_id,
                        "executor_role": d.executor_role,
                        "job_id": d.job_id,
                        "branch_hint": d.branch_hint,
                        "reason": d.reason,
                    }
                    for d in t.dispatches
                ],
                "locks_held": list(t.locks_held),
            }
            for t in report.autonomy_recent
        ],
        "completion_funnel_recent": [
            {
                "session_id": c.session_id,
                "job_id": c.job_id,
                "job_type": c.job_type,
                "completion_status": c.completion_status,
                "ticked": c.ticked,
                "reason": c.reason,
                "recommended_source": c.recommended_source,
                "producer_summary": c.producer_summary,
                "at": c.at,
            }
            for c in report.completion_funnel_recent
        ],
        "autonomy_locks_held": list(report.autonomy_locks_held),
        "warnings": list(report.warnings),
        "operator_actions": [a.to_payload() for a in summarize_operator_actions(report)],
        "compact": _compact_summary_payload(build_compact_status_summary(report)),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _compact_summary_payload(summary: CompactStatusSummary) -> Mapping[str, Any]:
    return {
        "profile": summary.profile,
        "services_alive": summary.services_alive,
        "services_stale": summary.services_stale,
        "services_unknown": summary.services_unknown,
        "services_circuit_open": summary.services_circuit_open,
        "queue_in_progress": summary.queue_in_progress,
        "queue_failed_terminal": summary.queue_failed_terminal,
        "queue_failed_retryable": summary.queue_failed_retryable,
        "autonomy_ticks_recent": summary.autonomy_ticks_recent,
        "autonomy_ticks_errored": summary.autonomy_ticks_errored,
        "autonomy_locked_dispatches": summary.autonomy_locked_dispatches,
        "funnel_done": summary.funnel_done,
        "funnel_retry_ready": summary.funnel_retry_ready,
        "funnel_needs_approval": summary.funnel_needs_approval,
        "funnel_blocked": summary.funnel_blocked,
        "actions_total": summary.actions_total,
        "top_action": (
            summary.top_action.to_payload() if summary.top_action else None
        ),
        "is_clean": summary.is_clean(),
    }


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


def _format_autonomy_tick_line(tick: AutonomyTickSummary) -> str:
    error_part = f" ERROR={tick.error}" if tick.error else ""
    next_part = f" next={tick.next_task_source}" if tick.next_task_source else ""
    summary = f" — {tick.summary_line}" if tick.summary_line else ""
    return f"[{tick.tick_id}]{next_part}{error_part}{summary}"


def _format_dispatch_line(d: AutonomyDispatchSummary) -> str:
    parts: list[str] = [f"{d.source}={d.outcome}"]
    if d.executor_role:
        parts.append(f"role={d.executor_role}")
    if d.session_id:
        parts.append(f"session={d.session_id}")
    if d.job_id:
        parts.append(f"job={d.job_id}")
    if d.branch_hint:
        parts.append(f"branch={d.branch_hint}")
    if d.reason:
        parts.append(f"why={d.reason}")
    return " ".join(parts)


def _format_funnel_line(c: CompletionFunnelSummary) -> str:
    tick_part = "ticked" if c.ticked else "no_tick"
    rec_part = (
        f" rec={c.recommended_source}" if c.recommended_source else ""
    )
    reason_part = f" — {c.reason}" if c.reason else ""
    return (
        f"[{c.completion_status}] session={c.session_id or '—'} "
        f"job={c.job_id} job_type={c.job_type} {tick_part}{rec_part}"
        f"{reason_part}"
    )


# ---------------------------------------------------------------------------
# Markdown renderer for the autonomy / funnel sections of the
# ``#봇-상태`` post. The base ``runtime.status_summary`` module owns
# the heartbeat / circuit / failed_terminal / fallback sections; this
# helper appends Round 4's autonomy + funnel rows so the operator
# sees what the runtime decided to do next inside the same post.
# ---------------------------------------------------------------------------


_AUTONOMY_OUTCOME_ICON: Mapping[str, str] = {
    AUTONOMY_OUTCOME_DISPATCHED: "✅",
    AUTONOMY_OUTCOME_DEDUPED: "♻️",
    AUTONOMY_OUTCOME_LOCKED: "🔒",
    AUTONOMY_OUTCOME_SKIPPED: "⏭",
    AUTONOMY_OUTCOME_ERROR: "⚠️",
}


_FUNNEL_STATUS_ICON: Mapping[str, str] = {
    "done": "✅",
    "retry_ready": "🔁",
    "needs_approval": "🙋",
    "blocked": "⛔",
}


_FUNNEL_STATUS_HINT: Mapping[str, str] = {
    "done": "completed → producer ticked",
    "retry_ready": "transient failure → producer ticked (retry path)",
    "needs_approval": "waiting on `#승인-대기` reply",
    "blocked": "blocked — operator review required",
}


def render_autonomy_summary_markdown(
    report: RuntimeStatusReport,
    *,
    max_ticks: int = 3,
    max_funnel: int = 5,
    max_actions: int = 5,
) -> str:
    """Return the autonomy / funnel markdown sections for ``#봇-상태``.

    Returns the empty string when nothing operator-actionable is
    showing — the caller (status_poster) appends the result to the
    base markdown output, so an "all clear" snapshot doesn't grow
    the post.

    The Round 4 마무리 layout puts the operator-action checklist at
    the top so the most urgent next-step is visible above the fold,
    then producer ticks, then funnel rows. Each section renders
    independently and is omitted when empty.
    """

    sections: list[str] = []

    actions_section = _render_operator_actions_section(
        summarize_operator_actions(report)[:max_actions]
    )
    if actions_section:
        sections.append(actions_section)

    autonomy_section = _render_autonomy_section(
        report.autonomy_recent[:max_ticks],
        locks_held=report.autonomy_locks_held,
    )
    if autonomy_section:
        sections.append(autonomy_section)

    funnel_section = _render_funnel_section(
        report.completion_funnel_recent[:max_funnel]
    )
    if funnel_section:
        sections.append(funnel_section)

    if not sections:
        return ""
    return "\n\n".join(sections)


def _render_operator_actions_section(
    actions: Sequence[OperatorAction],
) -> Optional[str]:
    """Render the "what should the operator do next" markdown block.

    Returns ``None`` when *actions* is empty so the parent renderer
    can skip the section and keep the post compact when the runtime
    is healthy.
    """

    if not actions:
        return None
    lines = ["### Operator actions"]
    for action in actions:
        icon = action.icon or "•"
        affected_part = ""
        if action.affected:
            head = ", ".join(f"`{a}`" for a in action.affected[:3])
            if len(action.affected) > 3:
                head += f" (+{len(action.affected) - 3} more)"
            affected_part = f" · affected: {head}"
        lines.append(
            f"- {icon} **[{action.severity}] {action.headline}**{affected_part}"
        )
        lines.append(f"  · 다음 단계: `{action.next_step}`")
    return "\n".join(lines)


def _render_autonomy_section(
    ticks: Sequence[AutonomyTickSummary],
    *,
    locks_held: Sequence[str],
) -> Optional[str]:
    """Render the autonomy producer section, or ``None`` if quiet.

    Quiet = no ticks recorded at all. We DO render when the most recent
    tick was idle so an operator can confirm "the producer ran and
    found nothing", which is different from "the producer never ran".
    """

    if not ticks and not locks_held:
        return None
    lines = ["### Autonomy producer"]
    if not ticks:
        lines.append("- _no ticks recorded yet_")
    else:
        for tick in ticks:
            head = _format_tick_markdown_head(tick)
            lines.append(head)
            for dispatch in tick.dispatches:
                lines.append(
                    "  - " + _format_dispatch_markdown(dispatch)
                )
            if tick.error:
                lines.append(f"  - ⚠️ tick error: `{tick.error}`")
    if locks_held:
        joined = ", ".join(f"`{s}`" for s in locks_held)
        lines.append(f"- 🔒 locks held: {joined}")
    return "\n".join(lines)


def _format_tick_markdown_head(tick: AutonomyTickSummary) -> str:
    next_part = (
        f" next=`{tick.next_task_source}`"
        if tick.next_task_source
        else ""
    )
    summary = f" — {tick.summary_line}" if tick.summary_line else ""
    icon = "⚠️" if tick.error else "🛞"
    return f"- {icon} `{tick.tick_id}`{next_part}{summary}"


def _format_dispatch_markdown(d: AutonomyDispatchSummary) -> str:
    icon = _AUTONOMY_OUTCOME_ICON.get(d.outcome, "•")
    parts: list[str] = [f"{icon} `{d.source}` → **{d.outcome}**"]
    if d.executor_role:
        parts.append(f"role=`{d.executor_role}`")
    if d.session_id:
        parts.append(f"session=`{d.session_id}`")
    if d.job_id:
        parts.append(f"job=`{d.job_id}`")
    if d.branch_hint:
        parts.append(f"branch=`{d.branch_hint}`")
    line = " · ".join(parts)
    if d.reason:
        line += f" — {d.reason}"
    return line


def _render_funnel_section(
    funnel: Sequence[CompletionFunnelSummary],
) -> Optional[str]:
    if not funnel:
        return None
    lines = ["### Completion funnel"]
    for c in funnel:
        icon = _FUNNEL_STATUS_ICON.get(c.completion_status, "•")
        hint = _FUNNEL_STATUS_HINT.get(
            c.completion_status, c.reason or "(no reason)"
        )
        rec_part = (
            f" → producer source `{c.recommended_source}`"
            if c.recommended_source
            else ""
        )
        ticked_part = " (ticked)" if c.ticked else ""
        sess = c.session_id or "—"
        lines.append(
            f"- {icon} **{c.completion_status}** session=`{sess}` "
            f"job_type=`{c.job_type}`{rec_part}{ticked_part} — {hint}"
        )
        if c.reason and c.reason != hint:
            lines.append(f"  · 사유: {c.reason}")
    return "\n".join(lines)


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
