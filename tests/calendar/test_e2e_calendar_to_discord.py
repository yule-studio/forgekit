"""End-to-end regression test: calendar inputs -> planning snapshot -> Discord formatter.

이 테스트는 단위 테스트로는 잡기 어려운, 모듈 사이의 통합 흐름을 한 번에 회귀 검증한다.
외부 네트워크나 디스크에 의존하지 않고 임시 SQLite 캐시만 사용한다.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.discord.formatter import format_plan_today_message
from yule_orchestrator.integrations.calendar.models import CalendarEvent, CalendarTodo
from yule_orchestrator.planning.inputs import build_planning_inputs
from yule_orchestrator.planning.planner import build_daily_plan
from yule_orchestrator.planning.snapshots import (
    load_daily_plan_snapshot,
    save_daily_plan_snapshot,
)


PLAN_DATE = date(2026, 4, 23)


def _calendar_event() -> CalendarEvent:
    return CalendarEvent(
        item_uid="event-1",
        title="팀 회의",
        start="2026-04-23T10:00:00+09:00",
        end="2026-04-23T11:00:00+09:00",
        all_day=False,
        calendar_name="회사",
        source="naver-caldav",
        description="- 10시 ~ 10시 30분 : 어제 회고\n- 10시 30분 ~ 11시 : 다음 스프린트",
        last_modified=None,
        category_color="27",
    )


def _calendar_todo() -> CalendarTodo:
    return CalendarTodo(
        item_uid="todo-1",
        title="이슈 리뷰",
        start=None,
        due="2026-04-23",
        start_all_day=False,
        due_all_day=True,
        status="NEEDS-ACTION",
        completed=False,
        completed_at=None,
        priority=None,
        percent_complete=None,
        calendar_name="회사 업무",
        source="naver-caldav",
        description="",
        last_modified=None,
        category_color="27",
    )


class CalendarToDiscordE2ETestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self._previous_db_path = os.environ.get("YULE_CACHE_DB_PATH")
        self.addCleanup(self._restore_env, "YULE_CACHE_DB_PATH", self._previous_db_path)
        os.environ["YULE_CACHE_DB_PATH"] = os.path.join(self.temp_dir.name, "cache.sqlite3")

        self._previous_ollama_planning = os.environ.get("OLLAMA_PLANNING_ENABLED")
        self.addCleanup(self._restore_env, "OLLAMA_PLANNING_ENABLED", self._previous_ollama_planning)
        os.environ["OLLAMA_PLANNING_ENABLED"] = "false"

    @staticmethod
    def _restore_env(name: str, previous_value: str | None) -> None:
        if previous_value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous_value

    def test_full_pipeline_round_trip(self) -> None:
        inputs = build_planning_inputs(
            plan_date=PLAN_DATE,
            calendar_events=[_calendar_event()],
            calendar_todos=[_calendar_todo()],
            github_issues=[],
            reminders=[],
        )

        envelope = build_daily_plan(inputs, use_ollama=False)
        plan = envelope.daily_plan

        self.assertEqual(plan.plan_date, PLAN_DATE)
        self.assertEqual(plan.summary.fixed_event_count, 1)
        self.assertEqual(plan.summary.todo_count, 1)
        self.assertGreaterEqual(len(plan.briefings), 4)
        slot_types = {briefing.briefing_type for briefing in plan.briefings}
        self.assertEqual(
            slot_types,
            {"morning", "work_start", "lunch", "evening"},
        )

        saved = save_daily_plan_snapshot(envelope, ttl_seconds=300)
        loaded = load_daily_plan_snapshot(PLAN_DATE, ttl_seconds=300)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertFalse(loaded.is_stale)
        self.assertEqual(loaded.envelope.daily_plan.plan_date, PLAN_DATE)
        self.assertEqual(
            loaded.envelope.daily_plan.summary.todo_count,
            plan.summary.todo_count,
        )
        self.assertEqual(saved.cache_key, loaded.cache_key)

        message = format_plan_today_message(loaded.envelope, snapshot=loaded)
        self.assertIn("**오늘 브리핑**", message)
        self.assertIn("**아침 브리핑**", message)
        self.assertIn("이슈 리뷰", message)
        self.assertIn("팀 회의", message)
        self.assertNotIn("마지막 동기화 기준 브리핑입니다", message)

    def test_stale_snapshot_carries_stale_label_to_discord(self) -> None:
        inputs = build_planning_inputs(
            plan_date=PLAN_DATE,
            calendar_events=[],
            calendar_todos=[_calendar_todo()],
            github_issues=[],
            reminders=[],
        )
        envelope = build_daily_plan(inputs, use_ollama=False)

        save_daily_plan_snapshot(envelope, ttl_seconds=1)

        # ttl_seconds=0 forces the cached entry to be considered stale on read.
        stale = load_daily_plan_snapshot(PLAN_DATE, allow_stale=True, ttl_seconds=0)
        self.assertIsNotNone(stale)
        assert stale is not None
        self.assertTrue(stale.is_stale)

        message = format_plan_today_message(stale.envelope, snapshot=stale)
        self.assertIn("마지막 동기화 기준 브리핑입니다", message)


if __name__ == "__main__":
    unittest.main()
