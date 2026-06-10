from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from yule_core.timezone import now_local
from yule_planning.briefings import normalize_paragraph_spacing
from yule_planning.models import DailyPlanEnvelope, PlanningCheckpoint, PlanningScheduledBriefing
from yule_planning.snapshots import DailyPlanSnapshot

DISCORD_MESSAGE_LIMIT = 1900


def format_plan_today_message(
    envelope: DailyPlanEnvelope,
    mention_user_id: Optional[int] = None,
    snapshot: Optional[DailyPlanSnapshot] = None,
    slot_title: Optional[str] = None,
) -> str:
    plan = envelope.daily_plan
    lines: list[str] = []
    _append_mention(lines, mention_user_id)
    if slot_title is not None:
        lines.append(f"**[{slot_title}]**")
        lines.append("")
    current_local = now_local()
    lines.append(f"_지금 {current_local.strftime('%Y-%m-%d %H:%M')} 기준_")
    if snapshot is not None:
        if snapshot.is_stale:
            lines.append(
                f"마지막 동기화 기준 브리핑입니다. 생성 시각: {snapshot.generated_at.strftime('%Y-%m-%d %H:%M')}"
            )
        else:
            lines.append(f"오늘의 브리핑입니다. 생성 시각: {snapshot.generated_at.strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append("**오늘 브리핑**")
    lines.extend(_non_empty_lines(plan.discord_briefing))
    lines.append("")
    lines.append("**아침 브리핑**")
    lines.extend(_morning_summary_lines(_strip_hallucinated_now(plan.morning_briefing)))

    if plan.prioritized_tasks:
        lines.append("")
        lines.append("**추천 작업**")
        for index, task in enumerate(plan.prioritized_tasks[:3], start=1):
            lines.append(f"{index}. {task.title}")
            detail_parts = [f"우선순위: {_priority_label(task.priority_level)}"]
            if task.due_date:
                detail_parts.append(f"기한: {_due_label(task.due_date)}")
            lines.append(f"   - {' | '.join(detail_parts)}")

    if plan.time_block_briefings:
        lines.append("")
        lines.append("**시간대 메모**")
        work_end = _resolve_work_end_boundary(plan.fixed_schedule)
        if work_end is None:
            for briefing in plan.time_block_briefings:
                lines.append(f"- {_time_range(briefing.start, briefing.end)} {briefing.title}")
                lines.append(f"  {briefing.briefing}")
        else:
            work_group = [
                briefing for briefing in plan.time_block_briefings
                if datetime.fromisoformat(briefing.start) < work_end
            ]
            post_work_group = [
                briefing for briefing in plan.time_block_briefings
                if datetime.fromisoformat(briefing.start) >= work_end
            ]
            if work_group:
                lines.append(f"_업무 시간 (~ {work_end.strftime('%H:%M')})_")
                for briefing in work_group:
                    lines.append(f"- {_time_range(briefing.start, briefing.end)} {briefing.title}")
                    lines.append(f"  {briefing.briefing}")
            if post_work_group:
                if work_group:
                    lines.append("")
                lines.append(f"_퇴근 후 ({work_end.strftime('%H:%M')} 이후)_")
                for briefing in post_work_group:
                    lines.append(f"- {_time_range(briefing.start, briefing.end)} {briefing.title}")
                    lines.append(f"  {briefing.briefing}")

    if plan.checkpoints:
        lines.append("")
        lines.append("**체크포인트**")
        for checkpoint in plan.checkpoints[:3]:
            lines.append(f"- {checkpoint.prompt}")

    return "\n".join(lines).strip()


def format_missing_plan_snapshot_message(
    *,
    mention_user_id: Optional[int] = None,
) -> str:
    lines: list[str] = []
    _append_mention(lines, mention_user_id)
    lines.append("아직 오늘 daily-plan snapshot이 없습니다.")
    lines.append("아래 순서로 로컬 동기화를 먼저 실행한 뒤 다시 확인해 주세요.")
    lines.append("")
    lines.append("```bash")
    lines.append("yule calendar sync --json")
    lines.append("yule github issues --limit 30")
    lines.append("yule planning snapshot --json")
    lines.append("```")
    return "\n".join(lines)


def format_snapshot_regenerating_message(
    *,
    mention_user_id: Optional[int] = None,
    slot_title: Optional[str] = None,
) -> str:
    lines: list[str] = []
    _append_mention(lines, mention_user_id)
    if slot_title is not None:
        lines.append(f"**[{slot_title}]**")
        lines.append("")
    lines.append("브리핑 데이터를 준비하고 있습니다.")
    lines.append("캘린더와 GitHub 이슈를 모아 snapshot을 만든 뒤 곧 이어서 보내드릴게요.")
    return "\n".join(lines)


def format_snapshot_regeneration_failed_message(
    *,
    mention_user_id: Optional[int] = None,
    error: Optional[str] = None,
) -> str:
    lines: list[str] = []
    _append_mention(lines, mention_user_id)
    lines.append("snapshot을 다시 만들지 못했습니다.")
    if error:
        lines.append(f"원인: {error}")
    lines.append("아래 순서로 직접 동기화를 시도한 뒤 다시 요청해 주세요.")
    lines.append("")
    lines.append("```bash")
    lines.append("yule calendar sync --json")
    lines.append("yule github issues --limit 30")
    lines.append("yule planning snapshot --json")
    lines.append("```")
    return "\n".join(lines)


def format_checkpoints_message(
    checkpoints: Sequence[PlanningCheckpoint],
    *,
    reference_time: datetime,
    mention_user_id: Optional[int] = None,
    include_response_prompt: bool = False,
) -> str:
    if not checkpoints:
        lines: list[str] = []
        _append_mention(lines, mention_user_id)
        lines.append(f"{reference_time.strftime('%H:%M')} 기준으로 예정된 체크포인트가 없습니다.")
        return "\n".join(lines)

    lines: list[str] = []
    _append_mention(lines, mention_user_id)
    lines.append(f"{reference_time.strftime('%H:%M')} 기준 체크포인트")
    for checkpoint in checkpoints:
        lines.append(f"- {checkpoint.prompt}")
    if include_response_prompt:
        lines.append("")
        lines.append("완료했으면 `완료` 또는 `yes`, 건너뛰려면 `건너뛰기` 또는 `skip`으로 답해 주세요.")
    return "\n".join(lines)


def format_scheduled_briefing_message(
    briefing: PlanningScheduledBriefing,
    *,
    snapshot: Optional[DailyPlanSnapshot] = None,
    mention_user_id: Optional[int] = None,
) -> str:
    if snapshot is not None:
        return format_plan_today_message(
            snapshot.envelope,
            mention_user_id=mention_user_id,
            snapshot=snapshot,
            slot_title=briefing.title,
        )

    lines: list[str] = []
    _append_mention(lines, mention_user_id)
    lines.append(f"**[{briefing.title}]**")
    lines.append("")
    lines.extend(_paragraph_lines(normalize_paragraph_spacing(briefing.content)))
    return "\n".join(lines).strip()


def format_references_block(
    references: Sequence[Mapping[str, Any]],
    *,
    title: str = "참고 레퍼런스",
    limit: int = 5,
) -> str:
    """Render a Discord-friendly block from a sequence of reference items.

    Each item may include: ``title``, ``source``, ``url``, ``takeaway``.
    Returned string is ready to append to any message body. Empty input
    returns an empty string so callers can splice unconditionally.

    Reserved for the design/marketing flow — engineering-agent currently
    does not auto-collect references, but this slot keeps the message
    format stable for the future fetcher (env-strategy.md §7).
    """

    if not references:
        return ""

    lines: list[str] = [f"**{title}**"]
    for item in list(references)[:limit]:
        lines.append(_format_reference_line(item))
    return "\n".join(line for line in lines if line)


def _format_reference_line(item: Mapping[str, Any]) -> str:
    title = _coerce_text(item.get("title")) or "(제목 없음)"
    source = _coerce_text(item.get("source"))
    url = _coerce_text(item.get("url"))
    takeaway = _coerce_text(item.get("takeaway"))

    head = f"- **{title}**"
    if source:
        head += f" · {source}"
    if url:
        head += f" — {url}"
    if takeaway:
        head += f"\n  ↪ {takeaway}"
    return head


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text


def split_discord_message(message: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    """Break *message* into pieces that each fit Discord's content limit.

    Discord rejects ``content`` > 2000 chars (regular messages) and
    > 4000 chars (forum starters) with ``50035 — Must be N or fewer in
    length``. We default to ``DISCORD_MESSAGE_LIMIT`` (1900) so the
    same chunker is safe for every channel.send / thread.send / forum
    create_thread call. Any caller wanting a different cap (e.g. forum
    starters) should pass an explicit ``limit``.

    Long-line guarantee: if a *single* line is itself longer than the
    limit, we hard-slice it into ``limit``-sized pieces so the splitter
    never returns a chunk above the cap. Without that guarantee a
    single huge line would get re-emitted whole and Discord would still
    reject it.
    """

    if limit <= 0:
        return [message]
    if len(message) <= limit:
        return [message]

    chunks: list[str] = []
    current_lines: list[str] = []
    current_length = 0

    def _flush() -> None:
        nonlocal current_lines, current_length
        if current_lines:
            chunks.append("\n".join(current_lines))
            current_lines = []
            current_length = 0

    for line in message.splitlines():
        if len(line) > limit:
            # Single-line overflow: emit anything we've buffered, then
            # hard-slice the long line into ``limit`` sized blocks so no
            # individual chunk exceeds the cap. This catches log-style
            # output, very long URLs glued into a paragraph, etc.
            _flush()
            for offset in range(0, len(line), limit):
                chunks.append(line[offset : offset + limit])
            continue

        added_length = len(line) + (1 if current_lines else 0)
        if current_lines and current_length + added_length > limit:
            _flush()
            current_lines = [line]
            current_length = len(line)
            continue

        current_lines.append(line)
        current_length += added_length

    _flush()
    return chunks


_HALLUCINATED_NOW_LINE_PATTERNS = (
    re.compile(r"^\s*현재\s*\d{4}년.*?\d{1,2}\s*시\s*\d{1,2}\s*분.*?입니다\.?\s*$"),
    re.compile(r"^\s*현재\s*(?:오전|오후|새벽|아침|저녁|밤)?\s*\d{1,2}\s*시\s*\d{1,2}\s*분.*?입니다\.?\s*$"),
    re.compile(r"^\s*지금\s*(?:오전|오후|새벽|아침|저녁|밤)?\s*\d{1,2}\s*시\s*\d{1,2}\s*분.*?입니다\.?\s*$"),
)


def _strip_hallucinated_now(text: str) -> str:
    if not text:
        return text
    surviving_lines: list[str] = []
    for raw_line in text.splitlines():
        if any(pattern.match(raw_line) for pattern in _HALLUCINATED_NOW_LINE_PATTERNS):
            continue
        surviving_lines.append(raw_line)
    cleaned = "\n".join(surviving_lines)
    return cleaned.strip("\n")


def _time_range(start_value: str, end_value: str) -> str:
    return f"{datetime.fromisoformat(start_value).strftime('%H:%M')}~{datetime.fromisoformat(end_value).strftime('%H:%M')}"


def _resolve_work_end_boundary(fixed_schedule: Sequence[object]) -> Optional[datetime]:
    work_event_ends: list[datetime] = []
    for block in fixed_schedule:
        title = getattr(block, "title", "")
        if "업무 수행" not in title:
            continue
        try:
            work_event_ends.append(datetime.fromisoformat(getattr(block, "end", "")))
        except (TypeError, ValueError):
            continue
    if not work_event_ends:
        return None
    return max(work_event_ends)


def _non_empty_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()] or [text]


def _paragraph_lines(text: str) -> list[str]:
    if not text:
        return []
    raw_lines = text.replace("\r\n", "\n").splitlines()
    result: list[str] = []
    previous_blank = False
    started = False
    for raw in raw_lines:
        line = raw.rstrip()
        if not line.strip():
            if not started or previous_blank:
                continue
            result.append("")
            previous_blank = True
            continue
        result.append(line)
        previous_blank = False
        started = True
    while result and not result[-1].strip():
        result.pop()
    return result


def _morning_summary_lines(text: str) -> list[str]:
    normalized = normalize_paragraph_spacing(text)
    lines: list[str] = []
    for line in _paragraph_lines(normalized):
        if line.strip() in {"추천 작업", "초반 흐름"}:
            break
        lines.append(line)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines or _paragraph_lines(normalized) or _non_empty_lines(text)


def _priority_label(value: str) -> str:
    return {
        "high": "높음",
        "medium": "중간",
        "low": "낮음",
    }.get(value, value)


def _due_label(value: str) -> str:
    if "T" in value:
        return datetime.fromisoformat(value).strftime("%m-%d %H:%M")
    return value


def _append_mention(lines: list[str], mention_user_id: Optional[int]) -> None:
    if mention_user_id is None:
        return
    lines.append(f"<@{mention_user_id}>")
    lines.append("")
