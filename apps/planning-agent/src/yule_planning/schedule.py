from __future__ import annotations

from datetime import date, datetime, time, timedelta, tzinfo
import re
from typing import Optional, Sequence

from yule_core.timezone import local_tz
from yule_integrations.calendar.models import CalendarEvent, build_fallback_item_uid
from .models import PlanningCheckpoint, PlanningExecutionBlock, PlanningTaskCandidate, PlanningTimeBlock

PLANNING_DAY_START = time(hour=6, minute=0)
PLANNING_DAY_END = time(hour=23, minute=0)
MINIMUM_FOCUS_BLOCK_MINUTES = 30
MAXIMUM_FOCUS_BLOCK_MINUTES = 120
DEFAULT_CHECKPOINT_LEAD_MINUTES = (10, 5)

DESCRIPTION_BLOCK_PATTERN = re.compile(
    r"^\s*(?:[-*]\s*)?(?P<start>.+?)\s*(?:~|〜|–|-)\s*(?P<end>.+?)\s*:\s*(?P<title>.+?)\s*$"
)
TIME_TOKEN_PATTERN = re.compile(
    r"^(?:(오전|오후))?(?P<hour>\d{1,2})(?:(?:[:시])(?P<minute>\d{1,2}))?시?$"
)


def build_fixed_schedule(events: Sequence[CalendarEvent]) -> list[PlanningTimeBlock]:
    blocks: list[PlanningTimeBlock] = []
    for event in events:
        if event.all_day:
            continue
        blocks.append(
            PlanningTimeBlock(
                start=event.start,
                end=event.end,
                block_type="fixed_event",
                title=event.title,
                task_id=event.item_uid,
                locked=True,
            )
        )
    blocks.sort(key=lambda block: block.start)
    return blocks


def build_focus_blocks(
    plan_date: date,
    fixed_schedule: Sequence[PlanningTimeBlock],
    tasks: Sequence[PlanningTaskCandidate],
    focus_start_time: time = PLANNING_DAY_START,
    work_mode_enabled: bool = True,
    lunch_start_time: Optional[time] = None,
    lunch_duration_minutes: int = 0,
) -> tuple[list[PlanningTimeBlock], int]:
    timezone = _derive_schedule_timezone(fixed_schedule)
    lunch_block = _build_lunch_block(plan_date, lunch_start_time, lunch_duration_minutes, timezone)

    schedulable_tasks = [task for task in tasks if not getattr(task, "flexible", False)]

    if not work_mode_enabled:
        non_work_schedule = [block for block in fixed_schedule if "업무 수행" not in block.title]
        if lunch_block is not None:
            non_work_schedule = list(non_work_schedule) + [lunch_block]
        windows = _available_windows(plan_date, non_work_schedule, day_start_time=focus_start_time)
        available_focus_minutes = sum(int((end - start).total_seconds() // 60) for start, end in windows)
        if not windows:
            return [], available_focus_minutes
        working_windows = list(windows)
        focus_blocks: list[PlanningTimeBlock] = []
        for task in schedulable_tasks[:6]:
            assigned = _assign_task_block(task, working_windows)
            if assigned is not None:
                focus_blocks.append(assigned)
        return focus_blocks, available_focus_minutes

    work_event_blocks = [block for block in fixed_schedule if "업무 수행" in block.title]
    work_priority_tasks = [task for task in schedulable_tasks if task.category_label == "회사 업무"]
    other_tasks = [task for task in schedulable_tasks if task.category_label != "회사 업무"]

    work_windows = sorted(
        (
            (datetime.fromisoformat(block.start), datetime.fromisoformat(block.end))
            for block in work_event_blocks
        ),
        key=lambda item: item[0],
    )

    schedule_with_lunch: list[PlanningTimeBlock] = list(fixed_schedule)
    if lunch_block is not None:
        schedule_with_lunch.append(lunch_block)
    non_work_windows = _available_windows(
        plan_date, schedule_with_lunch, day_start_time=focus_start_time
    )

    available_focus_minutes = sum(
        int((end - start).total_seconds() // 60)
        for start, end in (list(work_windows) + list(non_work_windows))
    )

    work_focus_blocks: list[PlanningTimeBlock] = []
    work_window_buffer = list(work_windows)
    for task in work_priority_tasks[:6]:
        assigned = _assign_task_block(task, work_window_buffer)
        if assigned is not None:
            work_focus_blocks.append(assigned)

    other_focus_blocks: list[PlanningTimeBlock] = []
    non_work_window_buffer = list(non_work_windows)
    for task in other_tasks[:6]:
        assigned = _assign_task_block(task, non_work_window_buffer)
        if assigned is not None:
            other_focus_blocks.append(assigned)

    combined = sorted(work_focus_blocks + other_focus_blocks, key=lambda block: block.start)
    return combined, available_focus_minutes


def _build_lunch_block(
    plan_date: date,
    lunch_start_time: Optional[time],
    lunch_duration_minutes: int,
    timezone: Optional[tzinfo],
) -> Optional[PlanningTimeBlock]:
    if lunch_start_time is None or lunch_duration_minutes <= 0:
        return None
    lunch_start = datetime.combine(plan_date, lunch_start_time, tzinfo=timezone)
    lunch_end = lunch_start + timedelta(minutes=lunch_duration_minutes)
    return PlanningTimeBlock(
        start=lunch_start.isoformat(),
        end=lunch_end.isoformat(),
        block_type="fixed_event",
        title="점심 산책",
        task_id="planning-lunch-window",
        locked=True,
    )


def build_execution_blocks(events: Sequence[CalendarEvent]) -> list[PlanningExecutionBlock]:
    blocks: list[PlanningExecutionBlock] = []
    for event in events:
        if event.all_day or not event.description.strip():
            continue

        try:
            event_start = datetime.fromisoformat(event.start)
            event_end = datetime.fromisoformat(event.end)
        except ValueError:
            continue

        blocks.extend(
            _parse_execution_blocks_from_description(
                event=event,
                event_start=event_start,
                event_end=event_end,
            )
        )

    blocks.sort(key=lambda block: block.start)
    return blocks


def build_checkpoints(
    execution_blocks: Sequence[PlanningExecutionBlock],
    lead_minutes: int | str | Sequence[int],
) -> list[PlanningCheckpoint]:
    lead_minute_values = normalize_checkpoint_lead_minutes(lead_minutes)
    if not lead_minute_values:
        return []

    checkpoints: list[PlanningCheckpoint] = []
    sorted_blocks = sorted(execution_blocks, key=lambda block: block.start)

    for index, block in enumerate(sorted_blocks):
        block_start = datetime.fromisoformat(block.start)
        block_end = datetime.fromisoformat(block.end)

        next_block = sorted_blocks[index + 1] if index + 1 < len(sorted_blocks) else None
        for lead_minute in lead_minute_values:
            remind_at = block_end - timedelta(minutes=lead_minute)
            if remind_at <= block_start:
                continue

            prompt = _build_checkpoint_prompt(block, next_block, remind_at, lead_minute)
            checkpoint_id = build_fallback_item_uid(
                "planning-checkpoint",
                block.block_id,
                remind_at.isoformat(),
                str(lead_minute),
            )
            checkpoints.append(
                PlanningCheckpoint(
                    checkpoint_id=checkpoint_id,
                    remind_at=remind_at.isoformat(),
                    source_event_uid=block.source_event_uid,
                    source_event_title=block.source_event_title,
                    block_id=block.block_id,
                    block_title=block.title,
                    block_start=block.start,
                    block_end=block.end,
                    prompt=prompt,
                )
            )

    checkpoints.sort(key=lambda checkpoint: checkpoint.remind_at)
    return checkpoints


def normalize_checkpoint_lead_minutes(lead_minutes: int | str | Sequence[int]) -> tuple[int, ...]:
    if isinstance(lead_minutes, int):
        raw_values: list[int | str] = [lead_minutes]
    elif isinstance(lead_minutes, str):
        raw_values = [part.strip() for part in lead_minutes.split(",") if part.strip()]
    else:
        raw_values = list(lead_minutes)

    normalized: list[int] = []
    for raw_value in raw_values:
        try:
            value = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("--reminder-lead-minutes must be a positive integer or comma-separated integers.") from exc
        if value <= 0:
            continue
        if value not in normalized:
            normalized.append(value)

    return tuple(sorted(normalized, reverse=True))


def build_missing_event_plan_checkpoints(
    events: Sequence[CalendarEvent],
    lead_minutes: int = 10,
) -> list[PlanningCheckpoint]:
    if lead_minutes <= 0:
        return []

    checkpoints: list[PlanningCheckpoint] = []
    for event in events:
        if event.all_day or event.description.strip():
            continue

        try:
            event_start = datetime.fromisoformat(event.start)
            event_end = datetime.fromisoformat(event.end)
        except ValueError:
            continue

        remind_at = event_start - timedelta(minutes=lead_minutes)
        checkpoint_id = build_fallback_item_uid(
            "planning-event-plan-checkpoint",
            event.item_uid,
            remind_at.isoformat(),
        )
        prompt = (
            f"일정 '{event.title}'의 세부 계획이 비어 있습니다. "
            "이 일정을 위한 세부 계획을 작성하셔야 합니다."
        )
        checkpoints.append(
            PlanningCheckpoint(
                checkpoint_id=checkpoint_id,
                remind_at=remind_at.isoformat(),
                source_event_uid=event.item_uid,
                source_event_title=event.title,
                block_id=event.item_uid,
                block_title=event.title,
                block_start=event.start,
                block_end=event_end.isoformat(),
                prompt=prompt,
                kind="missing_event_plan",
            )
        )

    checkpoints.sort(key=lambda checkpoint: checkpoint.remind_at)
    return checkpoints


def build_event_rebriefing_checkpoints(
    events: Sequence[CalendarEvent],
    lead_minutes: int = 10,
) -> list[PlanningCheckpoint]:
    if lead_minutes <= 0:
        return []

    checkpoints: list[PlanningCheckpoint] = []
    for event in events:
        if event.all_day or not event.description.strip():
            continue

        try:
            event_start = datetime.fromisoformat(event.start)
            event_end = datetime.fromisoformat(event.end)
        except ValueError:
            continue

        remind_at = event_start - timedelta(minutes=lead_minutes)
        checkpoint_id = build_fallback_item_uid(
            "planning-event-rebriefing",
            event.item_uid,
            remind_at.isoformat(),
        )
        prompt = (
            f"10분 뒤 '{event.title}' 일정이 시작됩니다. "
            "지금 하던 일을 정리하고, 일정 설명에 적어둔 세부 흐름으로 전환할 준비를 해주세요."
        )
        checkpoints.append(
            PlanningCheckpoint(
                checkpoint_id=checkpoint_id,
                remind_at=remind_at.isoformat(),
                source_event_uid=event.item_uid,
                source_event_title=event.title,
                block_id=event.item_uid,
                block_title=event.title,
                block_start=event.start,
                block_end=event_end.isoformat(),
                prompt=prompt,
                kind="event_rebriefing",
            )
        )

    checkpoints.sort(key=lambda checkpoint: checkpoint.remind_at)
    return checkpoints


def select_due_checkpoints(
    checkpoints: Sequence[PlanningCheckpoint],
    at: datetime,
    window_minutes: int = 10,
) -> list[PlanningCheckpoint]:
    if window_minutes < 0:
        window_minutes = 0

    window_end = at + timedelta(minutes=window_minutes)
    return [
        checkpoint
        for checkpoint in checkpoints
        if at <= datetime.fromisoformat(checkpoint.remind_at) <= window_end
    ]


def _available_windows(
    plan_date: date,
    fixed_schedule: Sequence[PlanningTimeBlock],
    day_start_time: time = PLANNING_DAY_START,
) -> list[tuple[datetime, datetime]]:
    timezone = _derive_schedule_timezone(fixed_schedule)
    day_start = datetime.combine(plan_date, day_start_time, tzinfo=timezone)
    day_end = datetime.combine(plan_date, PLANNING_DAY_END, tzinfo=timezone)
    cursor = day_start
    windows: list[tuple[datetime, datetime]] = []

    timed_blocks = []
    for block in fixed_schedule:
        try:
            block_start = datetime.fromisoformat(block.start)
            block_end = datetime.fromisoformat(block.end)
        except ValueError:
            continue
        timed_blocks.append((block_start, block_end))

    timed_blocks.sort(key=lambda item: item[0])
    for block_start, block_end in timed_blocks:
        if block_end <= day_start or block_start >= day_end:
            continue
        clipped_start = max(block_start, day_start)
        clipped_end = min(block_end, day_end)
        if clipped_start > cursor and int((clipped_start - cursor).total_seconds() // 60) >= MINIMUM_FOCUS_BLOCK_MINUTES:
            windows.append((cursor, clipped_start))
        if clipped_end > cursor:
            cursor = clipped_end

    if day_end > cursor and int((day_end - cursor).total_seconds() // 60) >= MINIMUM_FOCUS_BLOCK_MINUTES:
        windows.append((cursor, day_end))

    return windows


def _derive_schedule_timezone(fixed_schedule: Sequence[PlanningTimeBlock]) -> Optional[tzinfo]:
    for block in fixed_schedule:
        try:
            parsed = datetime.fromisoformat(block.start)
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            return parsed.tzinfo

    # CI/UTC 환경에서도 YULE_TIMEZONE env 가 있으면 그것을 우선 사용.
    # 명시 env 가 없으면 시스템 tz 폴백.
    return local_tz()


def _assign_task_block(
    task: PlanningTaskCandidate,
    windows: list[tuple[datetime, datetime]],
) -> Optional[PlanningTimeBlock]:
    for index, (window_start, window_end) in enumerate(windows):
        duration_minutes = int((window_end - window_start).total_seconds() // 60)
        if duration_minutes < MINIMUM_FOCUS_BLOCK_MINUTES:
            continue

        block_minutes = min(duration_minutes, max(MINIMUM_FOCUS_BLOCK_MINUTES, task.estimated_minutes), MAXIMUM_FOCUS_BLOCK_MINUTES)
        block_end = window_start + timedelta(minutes=block_minutes)

        windows[index] = (block_end, window_end)
        return PlanningTimeBlock(
            start=window_start.isoformat(),
            end=block_end.isoformat(),
            block_type="focus",
            title=task.title,
            task_id=task.task_id,
            locked=False,
        )

    return None


def _parse_execution_blocks_from_description(
    event: CalendarEvent,
    event_start: datetime,
    event_end: datetime,
) -> list[PlanningExecutionBlock]:
    blocks: list[PlanningExecutionBlock] = []
    lines = event.description.splitlines()
    for line in lines:
        match = DESCRIPTION_BLOCK_PATTERN.match(line.strip())
        if match is None:
            continue

        start_text = match.group("start").strip()
        end_text = match.group("end").strip()
        title = match.group("title").strip()

        block_start = _resolve_description_time(
            token=start_text,
            event_start=event_start,
            event_end=event_end,
            reference=None,
        )
        if block_start is None:
            continue

        block_end = _resolve_description_time(
            token=end_text,
            event_start=event_start,
            event_end=event_end,
            reference=block_start,
        )
        if block_end is None or block_end <= block_start:
            continue

        block_id = build_fallback_item_uid(
            "planning-block",
            event.item_uid,
            block_start.isoformat(),
            block_end.isoformat(),
            title,
        )
        blocks.append(
            PlanningExecutionBlock(
                block_id=block_id,
                source_event_uid=event.item_uid,
                source_event_title=event.title,
                start=block_start.isoformat(),
                end=block_end.isoformat(),
                title=title,
                description=line.strip(),
            )
        )

    blocks.sort(key=lambda block: block.start)
    return blocks


def _resolve_description_time(
    token: str,
    event_start: datetime,
    event_end: datetime,
    reference: Optional[datetime],
) -> Optional[datetime]:
    match = TIME_TOKEN_PATTERN.match(token.strip().replace(" ", "").replace("분", ""))
    if match is None:
        return None

    meridian = match.group(1)
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or "0")

    if meridian == "오전":
        hour = 0 if hour == 12 else hour
        candidate_hours = [hour]
    elif meridian == "오후":
        hour = hour if hour == 12 else hour + 12
        candidate_hours = [hour]
    else:
        candidate_hours = [hour]
        if hour < 12:
            candidate_hours.append(hour + 12)
        candidate_hours.append(hour + 24)

    candidates: list[datetime] = []
    for candidate_hour in candidate_hours:
        day_offset, resolved_hour = divmod(candidate_hour, 24)
        candidate_date = event_start.date() + timedelta(days=day_offset)
        candidates.append(
            datetime.combine(
                candidate_date,
                time(hour=resolved_hour, minute=minute),
                tzinfo=event_start.tzinfo,
            )
        )

    if reference is None:
        window_candidates = [
            candidate
            for candidate in candidates
            if event_start - timedelta(minutes=5) <= candidate <= event_end + timedelta(minutes=5)
        ]
        if window_candidates:
            return min(window_candidates, key=lambda candidate: abs((candidate - event_start).total_seconds()))
        return min(candidates, key=lambda candidate: abs((candidate - event_start).total_seconds()))

    after_reference = [candidate for candidate in candidates if candidate > reference]
    window_candidates = [
        candidate
        for candidate in after_reference
        if candidate <= event_end + timedelta(minutes=5)
    ]
    if window_candidates:
        return min(window_candidates)
    if after_reference:
        return min(after_reference)
    return None


def _build_checkpoint_prompt(
    block: PlanningExecutionBlock,
    next_block: Optional[PlanningExecutionBlock],
    remind_at: datetime,
    lead_minutes: int,
) -> str:
    remind_label = remind_at.strftime("%H:%M")
    lead_message = f"'{block.title}' 마감까지 {lead_minutes}분 남았습니다."
    if next_block is not None and next_block.source_event_uid == block.source_event_uid:
        next_start = datetime.fromisoformat(next_block.start).strftime("%H:%M")
        return (
            f"{remind_label} 체크: {lead_message} "
            f"끝났다면 {next_start}부터 '{next_block.title}'로 넘어가고, "
            "아직 안 끝났다면 남은 핵심 한 가지만 정리해서 다음 블록으로 이월해 주세요."
        )
    return (
        f"{remind_label} 체크: {lead_message} "
        f"'{block.source_event_title}' 일정 종료 전 정리할 시간입니다. "
        "완료 여부와 남은 한 가지를 짧게 남겨두면 다음 판단이 쉬워집니다."
    )
