from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from yule_integrations.calendar import list_naver_calendar_items
from yule_integrations.github.issues import list_open_issues
from yule_integrations.github.pulls import list_open_pull_requests
from yule_planning import (
    build_daily_plan,
    collect_planning_inputs,
    load_reminder_items,
    save_daily_plan_snapshot,
)

ON_DEMAND_GITHUB_LIMIT = 30


@dataclass(frozen=True)
class SnapshotRefreshResult:
    ok: bool
    plan_date: date
    recommended_task_count: int = 0
    checkpoint_count: int = 0
    error: str | None = None


def regenerate_today_snapshot(plan_date: date) -> SnapshotRefreshResult:
    try:
        calendar_result = list_naver_calendar_items(plan_date, plan_date)
        github_issues = list(list_open_issues(ON_DEMAND_GITHUB_LIMIT))
        try:
            github_pull_requests = list(list_open_pull_requests(ON_DEMAND_GITHUB_LIMIT))
        except Exception as exc:
            print(f"warning: github pulls fetch failed during snapshot regenerate: {exc}")
            github_pull_requests = []
        reminders = load_reminder_items(None)
        inputs = collect_planning_inputs(
            plan_date=plan_date,
            github_limit=ON_DEMAND_GITHUB_LIMIT,
            include_calendar=True,
            include_github=True,
            reminders=reminders,
            prefetched_calendar_result=calendar_result,
            prefetched_github_issues=github_issues,
            prefetched_github_pull_requests=github_pull_requests,
            allow_live_calendar_fetch=False,
            allow_live_github_fetch=False,
        )
        envelope = build_daily_plan(inputs)
        save_daily_plan_snapshot(envelope)
    except Exception as exc:
        return SnapshotRefreshResult(
            ok=False,
            plan_date=plan_date,
            error=str(exc),
        )

    return SnapshotRefreshResult(
        ok=True,
        plan_date=plan_date,
        recommended_task_count=envelope.daily_plan.summary.recommended_task_count,
        checkpoint_count=len(envelope.daily_plan.checkpoints),
    )
