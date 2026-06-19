"""Calendar persistence contract — the structural shapes ``yule_storage`` persists.

Interface extraction (RWT3): ``calendar_state`` used to import the concrete calendar
models from ``yule_integrations.calendar.models`` *just for type hints*
(``if TYPE_CHECKING``), which created a static ``storage ↔ integrations`` cycle. Storage
is the persistence layer and must not depend on the external-adapter layer — so it now
declares the minimal structural ``Protocol`` it reads. Integrations' concrete dataclasses
(``CalendarEvent`` / ``CalendarTodo`` / ``CalendarQueryResult``) satisfy these by shape
(duck typing), no inheritance required, and ``storage`` no longer references
``integrations``. See ``docs/package-topology.md`` §7.
"""

from __future__ import annotations

from typing import Any, Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class CalendarEventLike(Protocol):
    """The event attributes ``calendar_state`` serialises."""

    item_uid: str
    title: str
    description: str
    start: Any
    end: Any
    all_day: bool
    calendar_name: str
    category_color: str
    source: str
    last_modified: Any

    def to_dict(self) -> dict: ...


@runtime_checkable
class CalendarTodoLike(Protocol):
    """The todo attributes ``calendar_state`` serialises."""

    item_uid: str
    title: str
    description: str
    start: Any
    start_all_day: bool
    due: Any
    due_all_day: bool
    status: str
    completed: bool
    completed_at: Any
    percent_complete: Any
    priority: Any
    calendar_name: str
    category_color: str
    source: str
    last_modified: Any

    def to_dict(self) -> dict: ...


@runtime_checkable
class CalendarQueryResultLike(Protocol):
    """A query result is anything exposing ``events`` and ``todos`` sequences."""

    events: Sequence[CalendarEventLike]
    todos: Sequence[CalendarTodoLike]


__all__ = ("CalendarEventLike", "CalendarTodoLike", "CalendarQueryResultLike")
