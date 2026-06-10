from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
import json
import time
from typing import Any, Callable, Optional, Sequence, TypeVar

from ..integrations.calendar import list_naver_calendar_items
from ..integrations.calendar.models import CalendarQueryResult
from ..integrations.github.issues import list_open_issues
from ..integrations.github.issues import GitHubIssue
from ..observability import RuntimeStepMetric, save_runtime_metric_run
from ..planning import (
    build_daily_plan,
    build_planning_inputs,
    load_reminder_items,
    PlanningSourceStatus,
    save_daily_plan_snapshot,
)
from ..storage import local_cache_database_path

T = TypeVar("T")


@dataclass(frozen=True)
class WarmupFetchPayload:
    calendar_result: Optional[CalendarQueryResult]
    github_issues: Optional[Sequence[GitHubIssue]]
    source_statuses: Sequence[dict[str, Any]]
    warnings: Sequence[str]


def run_daily_warmup_command(
    date_text: Optional[str],
    github_limit: int,
    reminders_file: Optional[str],
    skip_calendar: bool,
    skip_github: bool,
    force_refresh: bool,
    reminder_lead_minutes: int | str | Sequence[int],
    json_output: bool,
    use_ollama: Optional[bool] = None,
    ollama_model: Optional[str] = None,
    ollama_endpoint: Optional[str] = None,
    ollama_timeout_seconds: Optional[int] = None,
) -> int:
    plan_date = _parse_date(date_text)
    started_at = datetime.now().astimezone()
    steps: list[RuntimeStepMetric] = []
    payload: dict[str, Any] = {
        "action": "daily_warmup",
        "plan_date": plan_date.isoformat(),
        "force_refresh": force_refresh,
        "database_path": str(local_cache_database_path()),
    }

    (
        fetch_payload,
        calendar_metric,
        github_metric,
    ) = _fetch_warmup_sources(
        plan_date=plan_date,
        github_limit=github_limit,
        skip_calendar=skip_calendar,
        skip_github=skip_github,
        force_refresh=force_refresh,
    )
    steps.extend([calendar_metric, github_metric])
    if fetch_payload.calendar_result is not None:
        payload["calendar"] = {
            "event_count": len(fetch_payload.calendar_result.events),
            "todo_count": len(fetch_payload.calendar_result.todos),
            "metrics": dict(getattr(fetch_payload.calendar_result, "metrics", {}) or {}),
        }
    if fetch_payload.github_issues is not None:
        payload["github"] = {"issue_count": len(fetch_payload.github_issues)}
    if fetch_payload.source_statuses:
        payload["source_statuses"] = list(fetch_payload.source_statuses)
    if fetch_payload.warnings:
        payload["warnings"] = list(fetch_payload.warnings)

    snapshot_payload = None
    snapshot, metric = _measure_step(
        "planning_build",
        lambda: _build_and_save_snapshot(
            plan_date=plan_date,
            reminders_file=reminders_file,
            fetch_payload=fetch_payload,
            reminder_lead_minutes=reminder_lead_minutes,
            use_ollama=use_ollama,
            ollama_model=ollama_model,
            ollama_endpoint=ollama_endpoint,
            ollama_timeout_seconds=ollama_timeout_seconds,
        ),
        metadata=lambda result: {
            "recommended_task_count": result["recommended_task_count"],
            "checkpoint_count": result["checkpoint_count"],
        },
    )
    steps.append(metric)
    if snapshot is not None:
        snapshot_payload = snapshot
        payload["snapshot"] = snapshot_payload

    ended_at = datetime.now().astimezone()
    metric_payload = save_runtime_metric_run(
        workflow="daily-warmup",
        started_at=started_at,
        ended_at=ended_at,
        steps=steps,
        metadata={
            "plan_date": plan_date.isoformat(),
            "skip_calendar": skip_calendar,
            "skip_github": skip_github,
            "force_refresh": force_refresh,
        },
    )
    payload["metrics"] = metric_payload

    has_error = any(not step.ok for step in steps)
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1 if has_error else 0

    print(f"daily warmup for {plan_date.isoformat()}")
    for step in steps:
        status = "ok" if step.ok else "failed"
        if step.metadata.get("skipped"):
            status = "skipped"
        print(f"- {step.name}: {status} ({step.duration_seconds:.3f}s)")
        if step.error:
            print(f"  error: {step.error}")
    if snapshot_payload is not None:
        print(f"snapshot: {snapshot_payload['cache_key']}")
    print(f"metrics: {metric_payload['run_id']}")
    return 1 if has_error else 0


def _build_and_save_snapshot(
    *,
    plan_date: date,
    reminders_file: Optional[str],
    fetch_payload: WarmupFetchPayload,
    reminder_lead_minutes: int | str | Sequence[int],
    use_ollama: Optional[bool],
    ollama_model: Optional[str],
    ollama_endpoint: Optional[str],
    ollama_timeout_seconds: Optional[int],
) -> dict[str, Any]:
    reminders = load_reminder_items(reminders_file)
    inputs = build_planning_inputs(
        plan_date=plan_date,
        reminders=reminders,
        source_statuses=[
            PlanningSourceStatus.from_dict(item) for item in fetch_payload.source_statuses
        ],
        warnings=fetch_payload.warnings,
        calendar_events=fetch_payload.calendar_result.events if fetch_payload.calendar_result is not None else [],
        calendar_todos=fetch_payload.calendar_result.todos if fetch_payload.calendar_result is not None else [],
        github_issues=fetch_payload.github_issues or [],
    )
    envelope = build_daily_plan(
        inputs,
        reminder_lead_minutes=reminder_lead_minutes,
        use_ollama=use_ollama,
        ollama_model=ollama_model,
        ollama_endpoint=ollama_endpoint,
        ollama_timeout_seconds=ollama_timeout_seconds,
    )
    snapshot = save_daily_plan_snapshot(envelope)
    return {
        "cache_key": snapshot.cache_key,
        "generated_at": snapshot.generated_at.isoformat(),
        "recommended_task_count": envelope.daily_plan.summary.recommended_task_count,
        "checkpoint_count": len(envelope.daily_plan.checkpoints),
    }


def _measure_step(
    name: str,
    callback: Callable[[], T],
    metadata: Optional[Callable[[T], dict[str, Any]]] = None,
) -> tuple[Optional[T], RuntimeStepMetric]:
    started_at = datetime.now().astimezone()
    started = time.perf_counter()
    try:
        result = callback()
    except Exception as exc:
        ended_at = datetime.now().astimezone()
        return None, RuntimeStepMetric(
            name=name,
            duration_seconds=time.perf_counter() - started,
            ok=False,
            started_at=started_at.isoformat(),
            ended_at=ended_at.isoformat(),
            metadata={},
            error=str(exc),
        )

    ended_at = datetime.now().astimezone()
    return result, RuntimeStepMetric(
        name=name,
        duration_seconds=time.perf_counter() - started,
        ok=True,
        started_at=started_at.isoformat(),
        ended_at=ended_at.isoformat(),
        metadata=metadata(result) if metadata is not None else {},
    )


def _fetch_warmup_sources(
    *,
    plan_date: date,
    github_limit: int,
    skip_calendar: bool,
    skip_github: bool,
    force_refresh: bool,
) -> tuple[
    WarmupFetchPayload,
    RuntimeStepMetric,
    RuntimeStepMetric,
]:
    if skip_calendar and skip_github:
        return (
            WarmupFetchPayload(
                calendar_result=None,
                github_issues=None,
                source_statuses=[],
                warnings=[],
            ),
            _skipped_step("calendar_fetch"),
            _skipped_step("github_fetch"),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        calendar_future = None
        github_future = None

        if not skip_calendar:
            calendar_future = executor.submit(
                _measure_step,
                "calendar_fetch",
                lambda: list_naver_calendar_items(plan_date, plan_date, force_refresh=force_refresh),
                lambda result: {
                    "event_count": len(result.events),
                    "todo_count": len(result.todos),
                    "calendar_metrics": dict(getattr(result, "metrics", {}) or {}),
                    "force_refresh": force_refresh,
                },
            )
        if not skip_github:
            github_future = executor.submit(
                _measure_step,
                "github_fetch",
                lambda: list_open_issues(limit=github_limit, force_refresh=force_refresh),
                lambda result: {
                    "issue_count": len(result),
                    "limit": github_limit,
                    "force_refresh": force_refresh,
                },
            )

        calendar_result, calendar_metric = (
            calendar_future.result() if calendar_future is not None else (None, _skipped_step("calendar_fetch"))
        )
        github_issues, github_metric = (
            github_future.result() if github_future is not None else (None, _skipped_step("github_fetch"))
        )
        return (
            WarmupFetchPayload(
                calendar_result=calendar_result,
                github_issues=github_issues,
                source_statuses=_build_source_statuses(
                    skip_calendar=skip_calendar,
                    calendar_result=calendar_result,
                    calendar_metric=calendar_metric,
                    skip_github=skip_github,
                    github_issues=github_issues,
                    github_metric=github_metric,
                ),
                warnings=_build_source_warnings(calendar_metric=calendar_metric, github_metric=github_metric),
            ),
            calendar_metric,
            github_metric,
        )


def _skipped_step(name: str) -> RuntimeStepMetric:
    now = datetime.now().astimezone().isoformat()
    return RuntimeStepMetric(
        name=name,
        duration_seconds=0.0,
        ok=True,
        started_at=now,
        ended_at=now,
        metadata={"skipped": True},
    )


def _build_source_statuses(
    *,
    skip_calendar: bool,
    calendar_result: Optional[CalendarQueryResult],
    calendar_metric: RuntimeStepMetric,
    skip_github: bool,
    github_issues: Optional[Sequence[GitHubIssue]],
    github_metric: RuntimeStepMetric,
) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    if not skip_calendar:
        statuses.append(
            PlanningSourceStatus(
                source_id="calendar-prefetched",
                source_type="calendar",
                ok=calendar_metric.ok,
                item_count=(
                    len(calendar_result.events) + len(calendar_result.todos)
                    if calendar_result is not None
                    else 0
                ),
                warning=calendar_metric.error,
            ).to_dict()
        )
    if not skip_github:
        statuses.append(
            PlanningSourceStatus(
                source_id="github-issues-prefetched",
                source_type="github",
                ok=github_metric.ok,
                item_count=len(github_issues) if github_issues is not None else 0,
                warning=github_metric.error,
            ).to_dict()
        )
    return statuses


def _build_source_warnings(
    *,
    calendar_metric: RuntimeStepMetric,
    github_metric: RuntimeStepMetric,
) -> list[str]:
    warnings: list[str] = []
    if not calendar_metric.ok and calendar_metric.error:
        warnings.append(f"calendar: {calendar_metric.error}")
    if not github_metric.ok and github_metric.error:
        warnings.append(f"github: {github_metric.error}")
    return warnings


def _parse_date(value: Optional[str]) -> date:
    if value is None:
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("--date must use YYYY-MM-DD format.") from exc
