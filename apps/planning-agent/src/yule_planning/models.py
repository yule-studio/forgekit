from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from yule_integrations.calendar.models import CalendarEvent, CalendarTodo
from yule_integrations.github.issues import GitHubIssue


@dataclass(frozen=True)
class PlanningSourceStatus:
    source_id: str
    source_type: str
    ok: bool
    item_count: int
    warning: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "ok": self.ok,
            "item_count": self.item_count,
            "warning": self.warning,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "PlanningSourceStatus":
        return cls(
            source_id=str(payload["source_id"]),
            source_type=str(payload["source_type"]),
            ok=bool(payload["ok"]),
            item_count=int(payload["item_count"]),
            warning=_optional_string(payload.get("warning")),
        )


@dataclass(frozen=True)
class ReminderItem:
    item_id: str
    title: str
    description: str = ""
    due_date: Optional[str] = None
    priority_hint: Optional[str] = None
    estimated_minutes: int = 30
    tags: Sequence[str] = ()

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "description": self.description,
            "due_date": self.due_date,
            "priority_hint": self.priority_hint,
            "estimated_minutes": self.estimated_minutes,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "ReminderItem":
        tags = payload.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        due_date = _optional_string(payload.get("due_date"))
        priority_hint = _optional_string(payload.get("priority_hint"))
        return cls(
            item_id=str(payload.get("item_id") or payload.get("id") or payload.get("title") or "reminder"),
            title=str(payload.get("title") or "(untitled reminder)"),
            description=str(payload.get("description") or ""),
            due_date=due_date,
            priority_hint=priority_hint,
            estimated_minutes=_estimated_minutes_from_value(payload.get("estimated_minutes")),
            tags=[str(tag) for tag in tags],
        )


@dataclass(frozen=True)
class PlanningInputs:
    plan_date: date
    timezone: str
    source_statuses: Sequence[PlanningSourceStatus]
    warnings: Sequence[str]
    calendar_events: Sequence[CalendarEvent]
    calendar_todos: Sequence[CalendarTodo]
    github_issues: Sequence[GitHubIssue]
    reminders: Sequence[ReminderItem]
    github_pull_requests: Sequence["GitHubPullRequest"] = ()

    def to_dict(self) -> dict:
        return {
            "plan_date": self.plan_date.isoformat(),
            "timezone": self.timezone,
            "source_statuses": [status.to_dict() for status in self.source_statuses],
            "warnings": list(self.warnings),
            "calendar_events": [event.to_dict() for event in self.calendar_events],
            "calendar_todos": [todo.to_dict() for todo in self.calendar_todos],
            "github_issues": [issue.to_dict() for issue in self.github_issues],
            "reminders": [reminder.to_dict() for reminder in self.reminders],
            "github_pull_requests": [pr.to_dict() for pr in self.github_pull_requests],
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "PlanningInputs":
        from yule_integrations.github.pulls import GitHubPullRequest as _GitHubPullRequest

        return cls(
            plan_date=date.fromisoformat(str(payload["plan_date"])),
            timezone=str(payload["timezone"]),
            source_statuses=[
                PlanningSourceStatus.from_dict(item)
                for item in payload.get("source_statuses", [])
                if isinstance(item, dict)
            ],
            warnings=[str(warning) for warning in payload.get("warnings", [])],
            calendar_events=[
                CalendarEvent.from_dict(item)
                for item in payload.get("calendar_events", [])
                if isinstance(item, dict)
            ],
            calendar_todos=[
                CalendarTodo.from_dict(item)
                for item in payload.get("calendar_todos", [])
                if isinstance(item, dict)
            ],
            github_issues=[
                GitHubIssue.from_dict(item)
                for item in payload.get("github_issues", [])
                if isinstance(item, dict)
            ],
            reminders=[
                ReminderItem.from_dict(item)
                for item in payload.get("reminders", [])
                if isinstance(item, dict)
            ],
            github_pull_requests=[
                _GitHubPullRequest.from_dict(item)
                for item in payload.get("github_pull_requests", [])
                if isinstance(item, dict)
            ],
        )


@dataclass(frozen=True)
class DailyPlanSummary:
    fixed_event_count: int
    all_day_event_count: int
    todo_count: int
    github_issue_count: int
    reminder_count: int
    recommended_task_count: int
    available_focus_minutes: int

    def to_dict(self) -> dict:
        return {
            "fixed_event_count": self.fixed_event_count,
            "all_day_event_count": self.all_day_event_count,
            "todo_count": self.todo_count,
            "github_issue_count": self.github_issue_count,
            "reminder_count": self.reminder_count,
            "recommended_task_count": self.recommended_task_count,
            "available_focus_minutes": self.available_focus_minutes,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "DailyPlanSummary":
        return cls(
            fixed_event_count=int(payload["fixed_event_count"]),
            all_day_event_count=int(payload["all_day_event_count"]),
            todo_count=int(payload["todo_count"]),
            github_issue_count=int(payload["github_issue_count"]),
            reminder_count=int(payload["reminder_count"]),
            recommended_task_count=int(payload["recommended_task_count"]),
            available_focus_minutes=int(payload["available_focus_minutes"]),
        )


@dataclass(frozen=True)
class PlanningTaskCandidate:
    task_id: str
    source_type: str
    title: str
    description: str
    due_date: Optional[str]
    priority_score: int
    priority_level: str
    estimated_minutes: int
    reasons: Sequence[str]
    coding_candidate: bool
    category_color: Optional[str] = None
    category_label: Optional[str] = None
    flexible: bool = False

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "source_type": self.source_type,
            "title": self.title,
            "description": self.description,
            "due_date": self.due_date,
            "priority_score": self.priority_score,
            "priority_level": self.priority_level,
            "estimated_minutes": self.estimated_minutes,
            "reasons": list(self.reasons),
            "coding_candidate": self.coding_candidate,
            "category_color": self.category_color,
            "category_label": self.category_label,
            "flexible": self.flexible,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "PlanningTaskCandidate":
        return cls(
            task_id=str(payload["task_id"]),
            source_type=str(payload["source_type"]),
            title=str(payload["title"]),
            description=str(payload.get("description") or ""),
            due_date=_optional_string(payload.get("due_date")),
            priority_score=int(payload["priority_score"]),
            priority_level=str(payload["priority_level"]),
            estimated_minutes=int(payload["estimated_minutes"]),
            reasons=[str(reason) for reason in payload.get("reasons", [])],
            coding_candidate=bool(payload["coding_candidate"]),
            category_color=_optional_string(payload.get("category_color")),
            category_label=_optional_string(payload.get("category_label")),
            flexible=bool(payload.get("flexible") or False),
        )


@dataclass(frozen=True)
class PlanningTimeBlock:
    start: str
    end: str
    block_type: str
    title: str
    task_id: Optional[str]
    locked: bool

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "block_type": self.block_type,
            "title": self.title,
            "task_id": self.task_id,
            "locked": self.locked,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "PlanningTimeBlock":
        return cls(
            start=str(payload["start"]),
            end=str(payload["end"]),
            block_type=str(payload["block_type"]),
            title=str(payload["title"]),
            task_id=_optional_string(payload.get("task_id")),
            locked=bool(payload["locked"]),
        )


@dataclass(frozen=True)
class PlanningExecutionBlock:
    block_id: str
    source_event_uid: str
    source_event_title: str
    start: str
    end: str
    title: str
    description: str

    def to_dict(self) -> dict:
        return {
            "block_id": self.block_id,
            "source_event_uid": self.source_event_uid,
            "source_event_title": self.source_event_title,
            "start": self.start,
            "end": self.end,
            "title": self.title,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "PlanningExecutionBlock":
        return cls(
            block_id=str(payload["block_id"]),
            source_event_uid=str(payload["source_event_uid"]),
            source_event_title=str(payload["source_event_title"]),
            start=str(payload["start"]),
            end=str(payload["end"]),
            title=str(payload["title"]),
            description=str(payload.get("description") or ""),
        )


@dataclass(frozen=True)
class PlanningBlockBriefing:
    briefing_id: str
    start: str
    end: str
    title: str
    block_type: str
    source_ref: Optional[str]
    briefing: str

    def to_dict(self) -> dict:
        return {
            "briefing_id": self.briefing_id,
            "start": self.start,
            "end": self.end,
            "title": self.title,
            "block_type": self.block_type,
            "source_ref": self.source_ref,
            "briefing": self.briefing,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "PlanningBlockBriefing":
        return cls(
            briefing_id=str(payload["briefing_id"]),
            start=str(payload["start"]),
            end=str(payload["end"]),
            title=str(payload["title"]),
            block_type=str(payload["block_type"]),
            source_ref=_optional_string(payload.get("source_ref")),
            briefing=str(payload["briefing"]),
        )


@dataclass(frozen=True)
class PlanningCheckpoint:
    checkpoint_id: str
    remind_at: str
    source_event_uid: str
    source_event_title: str
    block_id: str
    block_title: str
    block_start: str
    block_end: str
    prompt: str
    kind: str = "wrap_up"

    def to_dict(self) -> dict:
        return {
            "checkpoint_id": self.checkpoint_id,
            "remind_at": self.remind_at,
            "source_event_uid": self.source_event_uid,
            "source_event_title": self.source_event_title,
            "block_id": self.block_id,
            "block_title": self.block_title,
            "block_start": self.block_start,
            "block_end": self.block_end,
            "prompt": self.prompt,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "PlanningCheckpoint":
        return cls(
            checkpoint_id=str(payload["checkpoint_id"]),
            remind_at=str(payload["remind_at"]),
            source_event_uid=str(payload["source_event_uid"]),
            source_event_title=str(payload["source_event_title"]),
            block_id=str(payload["block_id"]),
            block_title=str(payload["block_title"]),
            block_start=str(payload["block_start"]),
            block_end=str(payload["block_end"]),
            prompt=str(payload["prompt"]),
            kind=str(payload.get("kind") or "wrap_up"),
        )


@dataclass(frozen=True)
class PlanningScheduledBriefing:
    briefing_id: str
    briefing_type: str
    title: str
    send_at: str
    content: str
    source: str = "rules"

    def to_dict(self) -> dict:
        return {
            "briefing_id": self.briefing_id,
            "briefing_type": self.briefing_type,
            "title": self.title,
            "send_at": self.send_at,
            "content": self.content,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "PlanningScheduledBriefing":
        return cls(
            briefing_id=str(payload["briefing_id"]),
            briefing_type=str(payload["briefing_type"]),
            title=str(payload["title"]),
            send_at=str(payload["send_at"]),
            content=str(payload["content"]),
            source=str(payload.get("source") or "rules"),
        )


@dataclass(frozen=True)
class DailyPlan:
    plan_date: date
    timezone: str
    source_statuses: Sequence[PlanningSourceStatus]
    warnings: Sequence[str]
    summary: DailyPlanSummary
    fixed_schedule: Sequence[PlanningTimeBlock]
    execution_blocks: Sequence[PlanningExecutionBlock]
    prioritized_tasks: Sequence[PlanningTaskCandidate]
    suggested_time_blocks: Sequence[PlanningTimeBlock]
    morning_briefing: str
    time_block_briefings: Sequence[PlanningBlockBriefing]
    checkpoints: Sequence[PlanningCheckpoint]
    briefings: Sequence[PlanningScheduledBriefing]
    coding_agent_handoff: Sequence[PlanningTaskCandidate]
    discord_briefing: str
    morning_briefing_source: str
    discord_briefing_source: str

    def to_dict(self) -> dict:
        return {
            "plan_date": self.plan_date.isoformat(),
            "timezone": self.timezone,
            "source_statuses": [status.to_dict() for status in self.source_statuses],
            "warnings": list(self.warnings),
            "summary": self.summary.to_dict(),
            "fixed_schedule": [block.to_dict() for block in self.fixed_schedule],
            "execution_blocks": [block.to_dict() for block in self.execution_blocks],
            "prioritized_tasks": [task.to_dict() for task in self.prioritized_tasks],
            "suggested_time_blocks": [block.to_dict() for block in self.suggested_time_blocks],
            "morning_briefing": self.morning_briefing,
            "time_block_briefings": [briefing.to_dict() for briefing in self.time_block_briefings],
            "checkpoints": [checkpoint.to_dict() for checkpoint in self.checkpoints],
            "briefings": [briefing.to_dict() for briefing in self.briefings],
            "coding_agent_handoff": [task.to_dict() for task in self.coding_agent_handoff],
            "discord_briefing": self.discord_briefing,
            "morning_briefing_source": self.morning_briefing_source,
            "discord_briefing_source": self.discord_briefing_source,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "DailyPlan":
        return cls(
            plan_date=date.fromisoformat(str(payload["plan_date"])),
            timezone=str(payload["timezone"]),
            source_statuses=[
                PlanningSourceStatus.from_dict(item)
                for item in payload.get("source_statuses", [])
                if isinstance(item, dict)
            ],
            warnings=[str(warning) for warning in payload.get("warnings", [])],
            summary=DailyPlanSummary.from_dict(payload["summary"]),
            fixed_schedule=[
                PlanningTimeBlock.from_dict(item)
                for item in payload.get("fixed_schedule", [])
                if isinstance(item, dict)
            ],
            execution_blocks=[
                PlanningExecutionBlock.from_dict(item)
                for item in payload.get("execution_blocks", [])
                if isinstance(item, dict)
            ],
            prioritized_tasks=[
                PlanningTaskCandidate.from_dict(item)
                for item in payload.get("prioritized_tasks", [])
                if isinstance(item, dict)
            ],
            suggested_time_blocks=[
                PlanningTimeBlock.from_dict(item)
                for item in payload.get("suggested_time_blocks", [])
                if isinstance(item, dict)
            ],
            morning_briefing=str(payload["morning_briefing"]),
            time_block_briefings=[
                PlanningBlockBriefing.from_dict(item)
                for item in payload.get("time_block_briefings", [])
                if isinstance(item, dict)
            ],
            checkpoints=[
                PlanningCheckpoint.from_dict(item)
                for item in payload.get("checkpoints", [])
                if isinstance(item, dict)
            ],
            briefings=[
                PlanningScheduledBriefing.from_dict(item)
                for item in payload.get("briefings", [])
                if isinstance(item, dict)
            ],
            coding_agent_handoff=[
                PlanningTaskCandidate.from_dict(item)
                for item in payload.get("coding_agent_handoff", [])
                if isinstance(item, dict)
            ],
            discord_briefing=str(payload["discord_briefing"]),
            morning_briefing_source=str(payload.get("morning_briefing_source") or "rules"),
            discord_briefing_source=str(payload.get("discord_briefing_source") or "rules"),
        )


def _optional_string(value: object) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _estimated_minutes_from_value(value: object) -> int:
    if value is None or value == "":
        return 30

    try:
        estimated_minutes = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"estimated_minutes must be an integer-compatible value, got: {value!r}") from exc

    return max(15, estimated_minutes)


@dataclass(frozen=True)
class DailyPlanEnvelope:
    inputs: PlanningInputs
    daily_plan: DailyPlan

    def to_dict(self) -> dict:
        return {
            "inputs": self.inputs.to_dict(),
            "daily_plan": self.daily_plan.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "DailyPlanEnvelope":
        return cls(
            inputs=PlanningInputs.from_dict(payload["inputs"]),
            daily_plan=DailyPlan.from_dict(payload["daily_plan"]),
        )
