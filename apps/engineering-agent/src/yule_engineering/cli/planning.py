from __future__ import annotations

import json
from datetime import date, datetime
from typing import Optional, Sequence

from ..planning import (
    build_daily_plan,
    collect_planning_inputs,
    load_reminder_items,
    render_daily_plan,
    save_daily_plan_snapshot,
    select_due_checkpoints,
)


def run_planning_daily_command(
    date_text: Optional[str],
    github_limit: int,
    reminders_file: Optional[str],
    skip_calendar: bool,
    skip_github: bool,
    reminder_lead_minutes: int | str | Sequence[int],
    use_ollama: Optional[bool],
    ollama_model: Optional[str],
    ollama_endpoint: Optional[str],
    ollama_timeout_seconds: Optional[int],
    json_output: bool,
) -> int:
    plan_date = _parse_plan_date(date_text)
    reminders = load_reminder_items(reminders_file)
    inputs = collect_planning_inputs(
        plan_date=plan_date,
        github_limit=github_limit,
        include_calendar=not skip_calendar,
        include_github=not skip_github,
        reminders=reminders,
        allow_live_calendar_fetch=not skip_calendar,
        allow_live_github_fetch=not skip_github,
    )
    envelope = build_daily_plan(
        inputs,
        reminder_lead_minutes=reminder_lead_minutes,
        use_ollama=use_ollama,
        ollama_model=ollama_model,
        ollama_endpoint=ollama_endpoint,
        ollama_timeout_seconds=ollama_timeout_seconds,
    )

    if json_output:
        print(json.dumps(envelope.to_dict(), ensure_ascii=False, indent=2))
        return 0

    print(render_daily_plan(envelope), end="")
    return 0


def run_planning_checkpoints_command(
    date_text: Optional[str],
    at_text: Optional[str],
    reminder_lead_minutes: int | str | Sequence[int],
    window_minutes: int,
    json_output: bool,
) -> int:
    at = _parse_datetime(at_text)
    plan_date = _parse_plan_date(date_text) if date_text is not None else at.date()
    inputs = collect_planning_inputs(
        plan_date=plan_date,
        include_calendar=True,
        include_github=False,
        reminders=[],
        allow_live_calendar_fetch=True,
    )
    envelope = build_daily_plan(
        inputs,
        reminder_lead_minutes=reminder_lead_minutes,
        use_ollama=False,
    )
    due_checkpoints = select_due_checkpoints(
        envelope.daily_plan.checkpoints,
        at=at,
        window_minutes=window_minutes,
    )

    if json_output:
        print(
            json.dumps(
                {
                    "at": at.isoformat(),
                    "window_minutes": window_minutes,
                    "checkpoint_count": len(due_checkpoints),
                    "checkpoints": [checkpoint.to_dict() for checkpoint in due_checkpoints],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if not due_checkpoints:
        print("no due checkpoints")
        return 0

    for checkpoint in due_checkpoints:
        print(f"- {checkpoint.remind_at} | {checkpoint.prompt}")
    return 0


def run_planning_snapshot_command(
    date_text: Optional[str],
    github_limit: int,
    reminders_file: Optional[str],
    skip_calendar: bool,
    skip_github: bool,
    reminder_lead_minutes: int | str | Sequence[int],
    use_ollama: Optional[bool],
    ollama_model: Optional[str],
    ollama_endpoint: Optional[str],
    ollama_timeout_seconds: Optional[int],
    json_output: bool,
) -> int:
    plan_date = _parse_plan_date(date_text)
    reminders = load_reminder_items(reminders_file)
    inputs = collect_planning_inputs(
        plan_date=plan_date,
        github_limit=github_limit,
        include_calendar=not skip_calendar,
        include_github=not skip_github,
        reminders=reminders,
        allow_live_calendar_fetch=not skip_calendar,
        allow_live_github_fetch=not skip_github,
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
    payload = {
        "action": "planning_snapshot",
        "plan_date": plan_date.isoformat(),
        "generated_at": snapshot.generated_at.isoformat(),
        "is_stale": snapshot.is_stale,
        "cache_key": snapshot.cache_key,
        "summary": envelope.daily_plan.summary.to_dict(),
    }

    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"saved daily-plan snapshot for {plan_date.isoformat()}")
    print(f"generated_at: {snapshot.generated_at.isoformat()}")
    print(f"cache_key: {snapshot.cache_key}")
    print(f"recommended tasks: {envelope.daily_plan.summary.recommended_task_count}")
    return 0

def _parse_plan_date(value: Optional[str]) -> date:
    if value is None:
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("--date must use YYYY-MM-DD format.") from exc


def _parse_datetime(value: Optional[str]) -> datetime:
    if value is None:
        return datetime.now().astimezone()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("--at must use ISO datetime format, for example 2026-04-22T09:55:00+09:00.") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed
