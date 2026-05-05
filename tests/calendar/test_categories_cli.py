from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.cli.calendar import _build_category_summary
from yule_orchestrator.storage import CalendarStateRecord


class CalendarCategoriesCliTestCase(unittest.TestCase):
    def test_build_category_summary_groups_by_color_and_item_type(self) -> None:
        records = [
            self._record("todo", "27", "오늘 해야 할 업무", due_at="2026-04-23"),
            self._record("todo", "22", "mail-mail 동작 원리 정리", due_at="2026-04-23"),
            self._record("event", "27", "업무 수행", start_at="2026-04-23T09:00:00+09:00"),
        ]

        summary = _build_category_summary(records)

        self.assertEqual(len(summary), 3)
        self.assertEqual(summary[0]["category_color"], "22")
        self.assertEqual(summary[0]["item_type"], "todo")
        self.assertEqual(summary[0]["items"][0]["title"], "mail-mail 동작 원리 정리")
        self.assertEqual(summary[1]["category_color"], "27")
        self.assertEqual(summary[1]["item_type"], "event")
        self.assertEqual(summary[2]["category_color"], "27")
        self.assertEqual(summary[2]["item_type"], "todo")

    @staticmethod
    def _record(
        item_type: str,
        category_color: str | None,
        title: str,
        *,
        start_at: str | None = None,
        due_at: str | None = None,
    ) -> CalendarStateRecord:
        return CalendarStateRecord(
            source="naver-caldav",
            scope_hash="scope",
            item_type=item_type,
            item_key=f"{item_type}:{title}",
            external_uid=title,
            calendar_name="내 캘린더",
            title=title,
            start_at=start_at,
            end_at=None,
            due_at=due_at,
            all_day=True,
            status="NEEDS-ACTION" if item_type == "todo" else "CONFIRMED",
            completed=False,
            completed_at=None,
            priority=0 if item_type == "todo" else None,
            percent_complete=None,
            description="",
            last_modified=None,
            category_color=category_color,
            payload={},
            first_seen_at=0.0,
            last_seen_at=0.0,
            last_changed_at=0.0,
        )
