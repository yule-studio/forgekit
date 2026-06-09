from __future__ import annotations

from datetime import date
from typing import Sequence

from .briefings import (
    build_scheduled_briefings,
    build_time_block_briefings,
    normalize_paragraph_spacing,
    render_daily_plan,
    render_discord_briefing,
    render_evening_briefing,
    render_lunch_briefing,
    render_morning_briefing,
    render_work_start_briefing,
)
from .day_profile import load_day_profile, load_work_mode_enabled
from .models import DailyPlan, DailyPlanEnvelope, DailyPlanSummary, PlanningInputs
from .ollama import generate_human_briefing
from .ollama_config import load_ollama_planning_config
from .schedule import (
    DEFAULT_CHECKPOINT_LEAD_MINUTES,
    build_checkpoints,
    build_execution_blocks,
    build_event_rebriefing_checkpoints,
    build_fixed_schedule,
    build_focus_blocks,
    build_missing_event_plan_checkpoints,
    select_due_checkpoints,
)
from .tasks import build_task_candidates


def build_daily_plan(
    inputs: PlanningInputs,
    reminder_lead_minutes: int | str | Sequence[int] = DEFAULT_CHECKPOINT_LEAD_MINUTES,
    use_ollama: bool | None = None,
    ollama_model: str | None = None,
    ollama_endpoint: str | None = None,
    ollama_timeout_seconds: int | None = None,
) -> DailyPlanEnvelope:
    ollama_config = load_ollama_planning_config()
    resolved_use_ollama = ollama_config.enabled if use_ollama is None else use_ollama
    resolved_ollama_model = ollama_model or ollama_config.model
    resolved_ollama_endpoint = ollama_endpoint or ollama_config.endpoint
    resolved_ollama_timeout_seconds = (
        ollama_timeout_seconds
        if ollama_timeout_seconds is not None
        else ollama_config.timeout_seconds
    )

    fixed_schedule = build_fixed_schedule(inputs.calendar_events)
    execution_blocks = build_execution_blocks(inputs.calendar_events)
    tasks = build_task_candidates(inputs)
    day_profile = load_day_profile()
    work_mode_enabled = load_work_mode_enabled()
    suggested_blocks, available_focus_minutes = build_focus_blocks(
        inputs.plan_date,
        fixed_schedule,
        tasks,
        focus_start_time=day_profile.work_start_time,
        work_mode_enabled=work_mode_enabled,
        lunch_start_time=day_profile.lunch_start_time,
        lunch_duration_minutes=day_profile.lunch_duration_minutes,
    )
    checkpoints = sorted(
        [
            *build_missing_event_plan_checkpoints(inputs.calendar_events, lead_minutes=10),
            *build_event_rebriefing_checkpoints(inputs.calendar_events, lead_minutes=10),
            *build_checkpoints(execution_blocks, lead_minutes=reminder_lead_minutes),
        ],
        key=lambda checkpoint: checkpoint.remind_at,
    )
    coding_agent_handoff = [task for task in tasks if task.coding_candidate][:3]
    warnings = list(inputs.warnings)

    summary = DailyPlanSummary(
        fixed_event_count=len([event for event in inputs.calendar_events if not event.all_day]),
        all_day_event_count=len([event for event in inputs.calendar_events if event.all_day]),
        todo_count=len(inputs.calendar_todos),
        github_issue_count=len(inputs.github_issues),
        reminder_count=len(inputs.reminders),
        recommended_task_count=len(tasks),
        available_focus_minutes=available_focus_minutes,
    )

    discord_briefing = render_discord_briefing(
        summary,
        fixed_schedule,
        tasks,
        suggested_blocks,
        coding_agent_handoff,
        checkpoints,
    )
    time_block_briefings = build_time_block_briefings(
        fixed_schedule=fixed_schedule,
        execution_blocks=execution_blocks,
        suggested_time_blocks=suggested_blocks,
        tasks=tasks,
    )
    morning_briefing = render_morning_briefing(
        plan_date=inputs.plan_date,
        summary=summary,
        fixed_schedule=fixed_schedule,
        prioritized_tasks=tasks,
        suggested_time_blocks=suggested_blocks,
        time_block_briefings=time_block_briefings,
        coding_agent_handoff=coding_agent_handoff,
        checkpoints=checkpoints,
        day_profile=day_profile,
    )
    morning_briefing_source = "rules"
    discord_briefing_source = "rules"

    if resolved_use_ollama:
        try:
            morning_briefing = generate_human_briefing(
                plan_date=inputs.plan_date.isoformat(),
                summary_line=discord_briefing,
                fixed_schedule=fixed_schedule,
                prioritized_tasks=tasks,
                time_block_briefings=time_block_briefings,
                checkpoints=checkpoints,
                model=resolved_ollama_model,
                endpoint=resolved_ollama_endpoint,
                timeout_seconds=resolved_ollama_timeout_seconds,
                work_mode_enabled=work_mode_enabled,
                fallback_model=ollama_config.fallback_model,
                retry_count=ollama_config.retry_count,
            )
            morning_briefing = normalize_paragraph_spacing(morning_briefing)
            morning_briefing_source = "ollama"
        except ValueError as exc:
            warnings.append(f"ollama: {exc}")

    work_start_briefing = render_work_start_briefing(
        plan_date=inputs.plan_date,
        summary=summary,
        fixed_schedule=fixed_schedule,
        prioritized_tasks=tasks,
        suggested_time_blocks=suggested_blocks,
        checkpoints=checkpoints,
        day_profile=day_profile,
    )
    lunch_briefing = render_lunch_briefing(
        plan_date=inputs.plan_date,
        summary=summary,
        fixed_schedule=fixed_schedule,
        prioritized_tasks=tasks,
        checkpoints=checkpoints,
        day_profile=day_profile,
    )
    evening_briefing = render_evening_briefing(
        plan_date=inputs.plan_date,
        summary=summary,
        prioritized_tasks=tasks,
        coding_agent_handoff=coding_agent_handoff,
        warnings=warnings,
        day_profile=day_profile,
    )
    briefings = build_scheduled_briefings(
        plan_date=inputs.plan_date,
        day_profile=day_profile,
        discord_briefing=discord_briefing,
        morning_briefing=morning_briefing,
        work_start_briefing=work_start_briefing,
        lunch_briefing=lunch_briefing,
        evening_briefing=evening_briefing,
        morning_source=morning_briefing_source,
    )

    daily_plan = DailyPlan(
        plan_date=inputs.plan_date,
        timezone=inputs.timezone,
        source_statuses=inputs.source_statuses,
        warnings=warnings,
        summary=summary,
        fixed_schedule=fixed_schedule,
        execution_blocks=execution_blocks,
        prioritized_tasks=tasks,
        suggested_time_blocks=suggested_blocks,
        morning_briefing=morning_briefing,
        time_block_briefings=time_block_briefings,
        checkpoints=checkpoints,
        briefings=briefings,
        coding_agent_handoff=coding_agent_handoff,
        discord_briefing=discord_briefing,
        morning_briefing_source=morning_briefing_source,
        discord_briefing_source=discord_briefing_source,
    )
    return DailyPlanEnvelope(inputs=inputs, daily_plan=daily_plan)
