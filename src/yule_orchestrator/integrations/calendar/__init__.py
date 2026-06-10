"""Compatibility shim — calendar integration now lives in
``yule_integrations.calendar``.

Re-exports the same public names so existing
``from yule_orchestrator.integrations.calendar import ...`` imports keep
resolving to the identical objects. Submodule shims (``cache``,
``errors``, ``models``, ``naver_caldav``, ``parsing``, ``rendering``)
alias the new modules via ``sys.modules``.
"""

from yule_integrations.calendar import (
    CalendarErrorDetails,
    CalendarEvent,
    CalendarIntegrationError,
    CalendarQueryResult,
    CalendarTodo,
    list_naver_calendar_events,
    list_naver_calendar_items,
    render_calendar_events,
    render_calendar_items,
)


__all__ = [
    "CalendarErrorDetails",
    "CalendarEvent",
    "CalendarIntegrationError",
    "CalendarQueryResult",
    "CalendarTodo",
    "list_naver_calendar_items",
    "list_naver_calendar_events",
    "render_calendar_items",
    "render_calendar_events",
]
