from __future__ import annotations

from datetime import datetime
from typing import List

from .models import CalendarQueryResult


def render_calendar_items(result: CalendarQueryResult) -> str:
    if not result.events and not result.todos:
        return (
            f"Naver Calendar Items ({result.start_date.isoformat()} to {result.end_date.isoformat()})\n\n"
            "No calendar items found for the requested range.\n"
        )

    lines: List[str] = [
        f"Naver Calendar Items ({result.start_date.isoformat()} to {result.end_date.isoformat()})",
        "",
    ]

    if result.events:
        lines.append("Events")
        lines.append("")
        for event in result.events:
            if event.all_day:
                lines.append(f"- [all-day] {event.title} ({event.calendar_name})")
            else:
                lines.append(f"- [{format_time_range(event.start, event.end)}] {event.title} ({event.calendar_name})")
            if event.description:
                lines.append(f"  description: {event.description}")
            if event.category_color:
                lines.append(f"  category_color: {event.category_color}")

    if result.todos:
        if len(lines) > 2:
            lines.append("")
        lines.append("Todos")
        lines.append("")
        for todo in result.todos:
            status_label = todo.status.lower()
            lines.append(f"- [{status_label}] {todo.title} ({todo.calendar_name})")
            if todo.start:
                lines.append(f"  start: {format_temporal_value(todo.start, todo.start_all_day)}")
            if todo.due:
                lines.append(f"  due: {format_temporal_value(todo.due, todo.due_all_day)}")
            if todo.priority is not None:
                lines.append(f"  priority: {todo.priority}")
            if todo.percent_complete is not None:
                lines.append(f"  progress: {todo.percent_complete}%")
            if todo.completed_at:
                lines.append(f"  completed_at: {format_temporal_value(todo.completed_at, all_day=False)}")
            if todo.description:
                lines.append(f"  description: {todo.description}")
            if todo.category_color:
                lines.append(f"  category_color: {todo.category_color}")

    return "\n".join(lines) + "\n"


def render_calendar_events(result: CalendarQueryResult) -> str:
    return render_calendar_items(result)


def format_time_range(start: str, end: str) -> str:
    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)
    return f"{start_dt.strftime('%H:%M')}-{end_dt.strftime('%H:%M')}"


def format_temporal_value(value: str, all_day: bool) -> str:
    if all_day:
        return value
    return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
