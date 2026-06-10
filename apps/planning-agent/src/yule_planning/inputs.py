from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
from typing import Optional, Sequence

from yule_engineering.integrations.calendar import CalendarIntegrationError, list_naver_calendar_items
from yule_engineering.integrations.calendar.models import CalendarEvent, CalendarQueryResult, CalendarTodo
from yule_engineering.integrations.github.issues import GitHubIssue, GitHubIssueError, list_open_issues
from yule_engineering.integrations.github.pulls import GitHubPullRequest, list_open_pull_requests
from yule_engineering.storage import list_calendar_state_records
from .models import PlanningInputs, PlanningSourceStatus, ReminderItem


def load_reminder_items(path_text: Optional[str]) -> Sequence[ReminderItem]:
    if not path_text:
        return []

    path = Path(path_text)
    if not path.exists():
        raise ValueError(f"Reminder file was not found: {path}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Reminder file must contain valid JSON: {path}") from exc

    if isinstance(payload, dict):
        items = payload.get("items", [])
    else:
        items = payload

    if not isinstance(items, list):
        raise ValueError("Reminder file must contain a JSON array or an object with an `items` array.")

    reminders: list[ReminderItem] = []
    for item in items:
        if isinstance(item, dict):
            reminders.append(ReminderItem.from_dict(item))
    return reminders


def collect_planning_inputs(
    plan_date: date,
    github_limit: int = 20,
    include_calendar: bool = True,
    include_github: bool = True,
    reminders: Optional[Sequence[ReminderItem]] = None,
    prefetched_calendar_result: Optional[CalendarQueryResult] = None,
    prefetched_github_issues: Optional[Sequence[GitHubIssue]] = None,
    prefetched_github_pull_requests: Optional[Sequence[GitHubPullRequest]] = None,
    allow_live_calendar_fetch: bool = False,
    allow_live_github_fetch: bool = False,
) -> PlanningInputs:
    warnings: list[str] = []
    source_statuses: list[PlanningSourceStatus] = []
    calendar_events: Sequence[CalendarEvent] = []
    calendar_todos: Sequence[CalendarTodo] = []
    github_issues: Sequence[GitHubIssue] = []
    github_pull_requests: Sequence[GitHubPullRequest] = []
    reminder_items = list(reminders or [])

    if include_calendar:
        if prefetched_calendar_result is not None:
            calendar_events = prefetched_calendar_result.events
            calendar_todos = prefetched_calendar_result.todos
            source_statuses.append(
                PlanningSourceStatus(
                    source_id="calendar-prefetched",
                    source_type="calendar",
                    ok=True,
                    item_count=len(calendar_events) + len(calendar_todos),
                )
            )
        else:
            state_events, state_todos = _load_calendar_items_from_state(plan_date)
            calendar_events = state_events
            calendar_todos = state_todos
            if state_events or state_todos:
                source_statuses.append(
                    PlanningSourceStatus(
                        source_id="calendar-state",
                        source_type="calendar",
                        ok=True,
                        item_count=len(calendar_events) + len(calendar_todos),
                    )
                )
            else:
                if allow_live_calendar_fetch:
                    try:
                        result = list_naver_calendar_items(plan_date, plan_date)
                        calendar_events = result.events
                        calendar_todos = result.todos
                        source_statuses.append(
                            PlanningSourceStatus(
                                source_id="calendar-live",
                                source_type="calendar",
                                ok=True,
                                item_count=len(calendar_events) + len(calendar_todos),
                            )
                        )
                    except CalendarIntegrationError as exc:
                        warning = exc.details.message
                        warnings.append(f"calendar: {warning}")
                        source_statuses.append(
                            PlanningSourceStatus(
                                source_id="calendar-live",
                                source_type="calendar",
                                ok=False,
                                item_count=0,
                                warning=warning,
                            )
                        )
                else:
                    warning = (
                        "no calendar state for the requested date; run `yule daily-warmup` to populate."
                    )
                    warnings.append(f"calendar: {warning}")
                    source_statuses.append(
                        PlanningSourceStatus(
                            source_id="calendar-state",
                            source_type="calendar",
                            ok=False,
                            item_count=0,
                            warning=warning,
                        )
                    )

    if include_github:
        if prefetched_github_issues is not None:
            github_issues = list(prefetched_github_issues)
            source_statuses.append(
                PlanningSourceStatus(
                    source_id="github-issues-prefetched",
                    source_type="github",
                    ok=True,
                    item_count=len(github_issues),
                )
            )
        else:
            if allow_live_github_fetch:
                try:
                    github_issues = list_open_issues(limit=github_limit)
                    source_statuses.append(
                        PlanningSourceStatus(
                            source_id="github-issues-live",
                            source_type="github",
                            ok=True,
                            item_count=len(github_issues),
                        )
                    )
                except GitHubIssueError as exc:
                    warning = str(exc)
                    warnings.append(f"github: {warning}")
                    source_statuses.append(
                        PlanningSourceStatus(
                            source_id="github-issues-live",
                            source_type="github",
                            ok=False,
                            item_count=0,
                            warning=warning,
                        )
                    )
            else:
                warning = (
                    "github issues are only available via warmup; supply `prefetched_github_issues`."
                )
                warnings.append(f"github: {warning}")
                source_statuses.append(
                    PlanningSourceStatus(
                        source_id="github-issues",
                        source_type="github",
                        ok=False,
                        item_count=0,
                        warning=warning,
                    )
                )

        if prefetched_github_pull_requests is not None:
            github_pull_requests = list(prefetched_github_pull_requests)
            source_statuses.append(
                PlanningSourceStatus(
                    source_id="github-pulls-prefetched",
                    source_type="github",
                    ok=True,
                    item_count=len(github_pull_requests),
                )
            )
        elif allow_live_github_fetch:
            try:
                github_pull_requests = list_open_pull_requests(limit=github_limit)
                source_statuses.append(
                    PlanningSourceStatus(
                        source_id="github-pulls-live",
                        source_type="github",
                        ok=True,
                        item_count=len(github_pull_requests),
                    )
                )
            except GitHubIssueError as exc:
                warning = str(exc)
                warnings.append(f"github pulls: {warning}")
                source_statuses.append(
                    PlanningSourceStatus(
                        source_id="github-pulls-live",
                        source_type="github",
                        ok=False,
                        item_count=0,
                        warning=warning,
                    )
                )

    source_statuses.append(
        PlanningSourceStatus(
            source_id="reminders",
            source_type="reminder",
            ok=True,
            item_count=len(reminder_items),
        )
    )

    return build_planning_inputs(
        plan_date=plan_date,
        source_statuses=source_statuses,
        warnings=warnings,
        calendar_events=calendar_events,
        calendar_todos=calendar_todos,
        github_issues=github_issues,
        github_pull_requests=github_pull_requests,
        reminders=reminder_items,
    )


def build_planning_inputs(
    *,
    plan_date: date,
    timezone: Optional[str] = None,
    source_statuses: Optional[Sequence[PlanningSourceStatus]] = None,
    warnings: Optional[Sequence[str]] = None,
    calendar_events: Optional[Sequence[CalendarEvent]] = None,
    calendar_todos: Optional[Sequence[CalendarTodo]] = None,
    github_issues: Optional[Sequence[GitHubIssue]] = None,
    github_pull_requests: Optional[Sequence[GitHubPullRequest]] = None,
    reminders: Optional[Sequence[ReminderItem]] = None,
) -> PlanningInputs:
    from yule_engineering.core import local_tz_name

    resolved_timezone = timezone or local_tz_name()
    return PlanningInputs(
        plan_date=plan_date,
        timezone=resolved_timezone,
        source_statuses=list(source_statuses or []),
        warnings=list(warnings or []),
        calendar_events=list(calendar_events or []),
        calendar_todos=list(calendar_todos or []),
        github_issues=list(github_issues or []),
        reminders=list(reminders or []),
        github_pull_requests=list(github_pull_requests or []),
    )


def _load_calendar_items_from_state(plan_date: date) -> tuple[list[CalendarEvent], list[CalendarTodo]]:
    records = list_calendar_state_records(
        start_date=plan_date,
        end_date=plan_date,
        include_completed=True,
    )
    events: list[CalendarEvent] = []
    todos: list[CalendarTodo] = []

    for record in records:
        try:
            if record.item_type == "event":
                events.append(CalendarEvent.from_dict(record.payload))
            elif record.item_type == "todo":
                todos.append(CalendarTodo.from_dict(record.payload))
        except Exception:
            continue

    return (
        sorted(events, key=lambda event: event.sort_key()),
        sorted(todos, key=lambda todo: todo.sort_key()),
    )
