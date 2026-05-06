"""``yule supervisor`` CLI — read-only runtime status surface.

Phase E intentionally adds *no* automatic write/commit/push from the
supervisor. ``supervisor run --once`` walks the persisted workflow
sessions through :func:`diagnose_session` and prints a summary so the
operator can see "왜 멈춰 있는지" without invoking individual CLIs by
hand.

The command is network-free: it reads only from the local workflow
cache via :func:`list_sessions` and never contacts Discord, GitHub, or
the calendar. Tests inject their own ``loader`` to exercise the
formatting against synthetic sessions.
"""

from __future__ import annotations

import sys
from typing import Any, Callable, Iterable, Optional, Sequence, TextIO

from ..agents.session_status import (
    SessionStatusReport,
    diagnose_session,
)


SessionLoader = Callable[..., Sequence[Any]]


def run_supervisor_run_once_command(
    *,
    limit: int = 20,
    only_actionable: bool = False,
    loader: Optional[SessionLoader] = None,
    out_stream: Optional[TextIO] = None,
) -> int:
    """Print a runtime status summary for recent workflow sessions.

    Returns ``0`` whenever the read finishes (even if no sessions exist
    or all are healthy). Returns ``2`` when *loader* raises — surfacing
    the cache failure to the shell without crashing the supervisor.
    Phase E never returns failure for "stuck" sessions; it only reports.
    """

    stream = out_stream if out_stream is not None else sys.stdout
    sessions = _load_sessions(loader=loader, limit=limit, stream=stream)
    if sessions is None:
        return 2

    if not sessions:
        print("info: no workflow sessions found in local cache.", file=stream)
        return 0

    reports = tuple(diagnose_session(session) for session in sessions)
    if only_actionable:
        reports = tuple(
            report
            for report in reports
            if any(signal.severity != "info" for signal in report.signals)
        )

    if not reports:
        print(
            "info: every recent session is at an info-only state — nothing actionable detected.",
            file=stream,
        )
        return 0

    print(
        f"supervisor runtime status — {len(reports)} session(s) inspected",
        file=stream,
    )
    print(
        "(detect/report/propose only — supervisor does not auto-write/commit)",
        file=stream,
    )
    print("", file=stream)

    for index, report in enumerate(reports, start=1):
        for line in render_session_block(report, index=index):
            print(line, file=stream)
        print("", file=stream)

    actionable = sum(
        1
        for report in reports
        if any(signal.severity != "info" for signal in report.signals)
    )
    print(
        f"summary: {actionable} actionable / {len(reports) - actionable} info-only",
        file=stream,
    )
    return 0


def render_session_block(
    report: SessionStatusReport,
    *,
    index: Optional[int] = None,
) -> Iterable[str]:
    """Yield the supervisor-facing lines for a single session report.

    Kept as a separate function so tests assert on the structured
    output without invoking the CLI dispatcher.
    """

    header = f"[{index}] " if index is not None else ""
    session_label = f"`{report.session_id}`" if report.session_id else "(no session)"
    yield f"{header}session={session_label} state={report.state or 'unknown'} type={report.task_type or 'unknown'}"
    if report.prompt:
        prompt_short = " ".join(report.prompt.split())
        if len(prompt_short) > 120:
            prompt_short = prompt_short[:117] + "..."
        yield f"  prompt: {prompt_short}"

    yield (
        "  pipeline: "
        f"research_pack={'있음' if report.has_research_pack else '없음'} · "
        f"forum_thread={'O' if report.forum_thread_id else 'X'} · "
        f"played={len(report.played_roles)}/{len(report.role_sequence)} · "
        f"synthesis={'O' if report.has_synthesis else 'X'} · "
        f"obsidian_proposal={'O' if report.obsidian_proposal_present else 'X'}"
    )

    if report.signals:
        actionable = tuple(s for s in report.signals if s.severity != "info")
        info_only = tuple(s for s in report.signals if s.severity == "info")
        if actionable:
            yield "  signals:"
            for signal in actionable:
                tag = _SEVERITY_TAGS.get(signal.severity, signal.severity)
                yield f"    - {tag} {signal.title}"
                if signal.detail:
                    detail = " ".join(str(signal.detail).split())
                    if len(detail) > 200:
                        detail = detail[:197] + "..."
                    yield f"        원인: {detail}"
                if signal.propose:
                    propose = " ".join(str(signal.propose).split())
                    if len(propose) > 200:
                        propose = propose[:197] + "..."
                    yield f"        제안: {propose}"
        elif info_only:
            yield (
                f"  signals: info-only ({', '.join(s.code for s in info_only)})"
            )
    else:
        yield "  signals: (none)"


def _load_sessions(
    *,
    loader: Optional[SessionLoader],
    limit: int,
    stream: TextIO,
) -> Optional[Sequence[Any]]:
    if loader is None:
        from ..agents.workflow_state import list_sessions as default_loader

        loader = default_loader
    try:
        return tuple(loader(limit=limit))
    except TypeError:
        try:
            return tuple(loader())
        except Exception as exc:  # noqa: BLE001 - surface cache failures
            print(f"error: failed to load sessions: {exc}", file=stream)
            return None
    except Exception as exc:  # noqa: BLE001
        print(f"error: failed to load sessions: {exc}", file=stream)
        return None


_SEVERITY_TAGS = {
    "failed": "[FAILED]",
    "blocked": "[BLOCKED]",
    "stale": "[STALE]",
    "info": "[INFO]",
}


__all__ = [
    "run_supervisor_run_once_command",
    "render_session_block",
]
