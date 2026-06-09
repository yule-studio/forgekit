from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
from typing import Optional, Sequence


@dataclass(frozen=True)
class CalendarEvent:
    item_uid: str
    title: str
    start: str
    end: str
    all_day: bool
    calendar_name: str
    source: str
    description: str
    last_modified: Optional[str]
    category_color: Optional[str] = None

    def sort_key(self) -> tuple[int, str, str]:
        return (0 if self.all_day else 1, self.start, self.title.lower())

    def to_dict(self) -> dict:
        return {
            "item_uid": self.item_uid,
            "title": self.title,
            "start": self.start,
            "end": self.end,
            "all_day": self.all_day,
            "calendar_name": self.calendar_name,
            "source": self.source,
            "description": self.description,
            "last_modified": self.last_modified,
            "category_color": self.category_color,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "CalendarEvent":
        item_uid = payload.get("item_uid") or build_fallback_item_uid(
            "event",
            payload.get("calendar_name", ""),
            payload.get("title", ""),
            payload.get("start", ""),
            payload.get("end", ""),
        )
        return cls(
            item_uid=item_uid,
            title=payload["title"],
            start=payload["start"],
            end=payload["end"],
            all_day=payload["all_day"],
            calendar_name=payload["calendar_name"],
            source=payload["source"],
            description=payload.get("description", ""),
            last_modified=payload.get("last_modified"),
            category_color=payload.get("category_color"),
        )


@dataclass(frozen=True)
class CalendarTodo:
    item_uid: str
    title: str
    start: Optional[str]
    due: Optional[str]
    start_all_day: bool
    due_all_day: bool
    status: str
    completed: bool
    completed_at: Optional[str]
    priority: Optional[int]
    percent_complete: Optional[int]
    calendar_name: str
    source: str
    description: str
    last_modified: Optional[str]
    category_color: Optional[str] = None

    def sort_key(self) -> tuple[int, str, str]:
        return (
            1 if self.completed else 0,
            self.due or self.start or "9999-12-31T23:59:59+09:00",
            self.title.lower(),
        )

    def to_dict(self) -> dict:
        return {
            "item_uid": self.item_uid,
            "title": self.title,
            "start": self.start,
            "due": self.due,
            "start_all_day": self.start_all_day,
            "due_all_day": self.due_all_day,
            "status": self.status,
            "completed": self.completed,
            "completed_at": self.completed_at,
            "priority": self.priority,
            "percent_complete": self.percent_complete,
            "calendar_name": self.calendar_name,
            "source": self.source,
            "description": self.description,
            "last_modified": self.last_modified,
            "category_color": self.category_color,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "CalendarTodo":
        item_uid = payload.get("item_uid") or build_fallback_item_uid(
            "todo",
            payload.get("calendar_name", ""),
            payload.get("title", ""),
            payload.get("due") or "",
            payload.get("start") or "",
        )
        return cls(
            item_uid=item_uid,
            title=payload["title"],
            start=payload.get("start"),
            due=payload.get("due"),
            start_all_day=payload["start_all_day"],
            due_all_day=payload["due_all_day"],
            status=payload["status"],
            completed=payload["completed"],
            completed_at=payload.get("completed_at"),
            priority=payload.get("priority"),
            percent_complete=payload.get("percent_complete"),
            calendar_name=payload["calendar_name"],
            source=payload["source"],
            description=payload.get("description", ""),
            last_modified=payload.get("last_modified"),
            category_color=payload.get("category_color"),
        )


@dataclass(frozen=True)
class CalendarQueryResult:
    source: str
    start_date: date
    end_date: date
    events: Sequence[CalendarEvent]
    todos: Sequence[CalendarTodo]
    metrics: Optional[dict[str, float]] = None

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "event_count": len(self.events),
            "todo_count": len(self.todos),
            "events": [event.to_dict() for event in self.events],
            "todos": [todo.to_dict() for todo in self.todos],
            "metrics": dict(self.metrics or {}),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "CalendarQueryResult":
        return cls(
            source=payload["source"],
            start_date=date.fromisoformat(payload["start_date"]),
            end_date=date.fromisoformat(payload["end_date"]),
            events=[CalendarEvent.from_dict(event) for event in payload.get("events", [])],
            todos=[CalendarTodo.from_dict(todo) for todo in payload.get("todos", [])],
            metrics=_parse_metrics(payload.get("metrics")),
        )


def build_fallback_item_uid(item_type: str, *parts: str) -> str:
    normalized = json.dumps([item_type, *parts], ensure_ascii=False, separators=(",", ":"))
    return sha256(normalized.encode("utf-8")).hexdigest()


def _parse_metrics(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}

    metrics: dict[str, float] = {}
    for key, raw_value in value.items():
        try:
            metrics[str(key)] = float(raw_value)
        except (TypeError, ValueError):
            continue
    return metrics
