from __future__ import annotations

from datetime import date
import os
import tempfile
import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

# ``icalendar`` is in pyproject dependencies, but on a dev box that hasn't
# run ``pip install -e .`` the import will fail and crash the entire
# ``unittest`` discovery. Skip the suite in that case so other tests can
# still run; CI that installs the project will exercise this normally.
try:
    from icalendar import Calendar
except ModuleNotFoundError:
    Calendar = None  # type: ignore[assignment]

from yule_orchestrator.integrations.calendar.models import CalendarQueryResult
from yule_orchestrator.integrations.calendar.parsing import build_todo
from yule_orchestrator.storage.calendar_state import list_calendar_state_records, sync_calendar_query_result


@unittest.skipIf(
    Calendar is None,
    "icalendar not installed — run `pip install -e .` to enable this suite",
)
class CalendarCategoryColorTestCase(unittest.TestCase):
    def test_build_todo_extracts_naver_category_color(self) -> None:
        component = _first_component(
            """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:todo-1
SUMMARY:오늘 해야 할 업무
DUE;VALUE=DATE:20260423
STATUS:NEEDS-ACTION
X-NAVER-CATEGORY-COLOR:27
END:VTODO
END:VCALENDAR
"""
        )

        todo = build_todo(component, "내 할 일")

        self.assertEqual(todo.category_color, "27")
        self.assertEqual(todo.to_dict()["category_color"], "27")

    def test_query_result_round_trip_preserves_category_color(self) -> None:
        component = _first_component(
            """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:todo-2
SUMMARY:mail-mail 동작 원리 정리
DUE;VALUE=DATE:20260423
X-NAVER-CATEGORY-COLOR:22
END:VTODO
END:VCALENDAR
"""
        )
        todo = build_todo(component, "내 할 일")
        result = CalendarQueryResult(
            source="naver-caldav",
            start_date=date(2026, 4, 23),
            end_date=date(2026, 4, 23),
            events=[],
            todos=[todo],
        )

        restored = CalendarQueryResult.from_dict(result.to_dict())

        self.assertEqual(restored.todos[0].category_color, "22")

    def test_calendar_state_stores_category_color(self) -> None:
        temp_dir = _temporary_directory_or_skip(self)
        self.addCleanup(temp_dir.cleanup)
        previous_db_path = os.environ.get("YULE_CACHE_DB_PATH")
        self.addCleanup(_restore_env, "YULE_CACHE_DB_PATH", previous_db_path)
        os.environ["YULE_CACHE_DB_PATH"] = os.path.join(temp_dir.name, "cache.sqlite3")

        component = _first_component(
            """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:todo-3
SUMMARY:포트폴리오 정리
DUE;VALUE=DATE:20260423
X-NAVER-CATEGORY-COLOR:7
END:VTODO
END:VCALENDAR
"""
        )
        todo = build_todo(component, "내 할 일")
        result = CalendarQueryResult(
            source="naver-caldav",
            start_date=date(2026, 4, 23),
            end_date=date(2026, 4, 23),
            events=[],
            todos=[todo],
        )

        sync_calendar_query_result(result, scope_hash="scope-1")
        records = list_calendar_state_records(
            start_date=date(2026, 4, 23),
            end_date=date(2026, 4, 23),
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].category_color, "7")
        self.assertEqual(records[0].payload["category_color"], "7")

    def test_calendar_state_range_filter_excludes_other_days(self) -> None:
        temp_dir = _temporary_directory_or_skip(self)
        self.addCleanup(temp_dir.cleanup)
        previous_db_path = os.environ.get("YULE_CACHE_DB_PATH")
        self.addCleanup(_restore_env, "YULE_CACHE_DB_PATH", previous_db_path)
        os.environ["YULE_CACHE_DB_PATH"] = os.path.join(temp_dir.name, "cache.sqlite3")

        today_todo = build_todo(
            _first_component(
                """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:todo-today
SUMMARY:오늘 할 일
DUE;VALUE=DATE:20260423
END:VTODO
END:VCALENDAR
"""
            ),
            "내 할 일",
        )
        tomorrow_todo = build_todo(
            _first_component(
                """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:todo-tomorrow
SUMMARY:내일 할 일
DUE;VALUE=DATE:20260424
END:VTODO
END:VCALENDAR
"""
            ),
            "내 할 일",
        )

        sync_calendar_query_result(
            CalendarQueryResult(
                source="naver-caldav",
                start_date=date(2026, 4, 23),
                end_date=date(2026, 4, 24),
                events=[],
                todos=[today_todo, tomorrow_todo],
            ),
            scope_hash="scope-range",
        )

        same_day = list_calendar_state_records(
            start_date=date(2026, 4, 23),
            end_date=date(2026, 4, 23),
        )

        self.assertEqual([record.external_uid for record in same_day], ["todo-today"])


def _first_component(raw_ical: str):
    calendar = Calendar.from_ical(raw_ical)
    return next(component for component in calendar.walk("VTODO"))


def _temporary_directory_or_skip(test_case: unittest.TestCase) -> tempfile.TemporaryDirectory:
    try:
        return tempfile.TemporaryDirectory()
    except (FileNotFoundError, PermissionError) as exc:
        test_case.skipTest(f"temporary directory is not writable in this environment: {exc}")


def _restore_env(name: str, previous_value: str | None) -> None:
    if previous_value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = previous_value
