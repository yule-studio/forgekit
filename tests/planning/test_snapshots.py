from __future__ import annotations

import os
from datetime import date, datetime
import tempfile
import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.planning.models import (
    DailyPlan,
    DailyPlanEnvelope,
    DailyPlanSummary,
    PlanningInputs,
    PlanningScheduledBriefing,
    PlanningTaskCandidate,
)
from yule_orchestrator.planning.snapshots import load_daily_plan_snapshot, save_daily_plan_snapshot


class PlanningSnapshotsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = _temporary_directory_or_skip(self)
        self.addCleanup(self.temp_dir.cleanup)
        self.previous_db_path = os.environ.get("YULE_CACHE_DB_PATH")
        self.addCleanup(_restore_env, "YULE_CACHE_DB_PATH", self.previous_db_path)
        os.environ["YULE_CACHE_DB_PATH"] = os.path.join(self.temp_dir.name, "cache.sqlite3")

    def test_save_and_load_daily_plan_snapshot(self) -> None:
        envelope = _envelope()
        generated_at = datetime.fromisoformat("2026-04-23T05:58:00+09:00")

        saved = save_daily_plan_snapshot(
            envelope,
            generated_at=generated_at,
            ttl_seconds=300,
        )
        loaded = load_daily_plan_snapshot(date(2026, 4, 23), ttl_seconds=300)

        self.assertEqual(saved.generated_at, generated_at)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertFalse(loaded.is_stale)
        self.assertEqual(loaded.generated_at, generated_at)
        self.assertEqual(loaded.envelope.daily_plan.plan_date, date(2026, 4, 23))
        self.assertEqual(loaded.envelope.daily_plan.prioritized_tasks[0].title, "오늘 해야 할 업무")


def _envelope() -> DailyPlanEnvelope:
    task = PlanningTaskCandidate(
        task_id="todo:1",
        source_type="calendar_todo",
        title="오늘 해야 할 업무",
        description="",
        due_date="2026-04-23",
        priority_score=95,
        priority_level="high",
        estimated_minutes=60,
        reasons=["due today"],
        coding_candidate=False,
    )
    return DailyPlanEnvelope(
        inputs=PlanningInputs(
            plan_date=date(2026, 4, 23),
            timezone="KST",
            source_statuses=[],
            warnings=[],
            calendar_events=[],
            calendar_todos=[],
            github_issues=[],
            reminders=[],
        ),
        daily_plan=DailyPlan(
            plan_date=date(2026, 4, 23),
            timezone="KST",
            source_statuses=[],
            warnings=[],
            summary=DailyPlanSummary(
                fixed_event_count=0,
                all_day_event_count=0,
                todo_count=1,
                github_issue_count=0,
                reminder_count=0,
                recommended_task_count=1,
                available_focus_minutes=420,
            ),
            fixed_schedule=[],
            execution_blocks=[],
            prioritized_tasks=[task],
            suggested_time_blocks=[],
            morning_briefing="아침 브리핑",
            time_block_briefings=[],
            checkpoints=[],
            briefings=[
                PlanningScheduledBriefing(
                    briefing_id="morning-1",
                    briefing_type="morning",
                    title="아침 브리핑",
                    send_at="2026-04-23T06:00:00+09:00",
                    content="오늘 브리핑\n아침 브리핑",
                )
            ],
            coding_agent_handoff=[],
            discord_briefing="오늘 브리핑",
            morning_briefing_source="rules",
            discord_briefing_source="rules",
        ),
    )


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
