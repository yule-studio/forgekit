from __future__ import annotations

import json
import os
from datetime import date
import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.integrations.calendar.models import CalendarTodo
from yule_orchestrator.planning.category_policy import (
    NAVER_CATEGORY_POLICY_JSON_ENV,
    reset_naver_category_policy_cache,
)
from yule_orchestrator.planning.models import PlanningInputs
from yule_orchestrator.planning.planner import build_daily_plan


class PlanningCategoryPolicyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_policy_json = os.environ.get(NAVER_CATEGORY_POLICY_JSON_ENV)
        reset_naver_category_policy_cache()

    def tearDown(self) -> None:
        if self.previous_policy_json is None:
            os.environ.pop(NAVER_CATEGORY_POLICY_JSON_ENV, None)
        else:
            os.environ[NAVER_CATEGORY_POLICY_JSON_ENV] = self.previous_policy_json
        reset_naver_category_policy_cache()

    def test_category_policy_boosts_matching_todo(self) -> None:
        os.environ[NAVER_CATEGORY_POLICY_JSON_ENV] = json.dumps(
            {
                "colors": {
                    "27": {
                        "label": "회사 업무",
                        "priority_boost": 60,
                        "reason": "회사 업무 범주",
                        "coding_candidate": True,
                    }
                }
            }
        )
        reset_naver_category_policy_cache()

        inputs = PlanningInputs(
            plan_date=date(2026, 4, 22),
            timezone="KST",
            source_statuses=[],
            warnings=[],
            calendar_events=[],
            calendar_todos=[
                self._todo("todo-normal", "개인 정리", category_color=None),
                self._todo("todo-company", "오늘 해야 할 업무", category_color="27"),
            ],
            github_issues=[],
            reminders=[],
        )

        plan = build_daily_plan(inputs).daily_plan

        self.assertEqual(plan.prioritized_tasks[0].task_id, "todo:todo-company")
        self.assertEqual(plan.prioritized_tasks[0].category_color, "27")
        self.assertEqual(plan.prioritized_tasks[0].category_label, "회사 업무")
        self.assertTrue(plan.prioritized_tasks[0].coding_candidate)
        self.assertIn("회사 업무 범주", plan.morning_briefing)

    @staticmethod
    def _todo(item_uid: str, title: str, *, category_color: str | None) -> CalendarTodo:
        return CalendarTodo(
            item_uid=item_uid,
            title=title,
            start=None,
            due="2026-04-22",
            start_all_day=False,
            due_all_day=True,
            status="NEEDS-ACTION",
            completed=False,
            completed_at=None,
            priority=0,
            percent_complete=None,
            calendar_name="내 할 일",
            source="naver-caldav",
            description="",
            last_modified=None,
            category_color=category_color,
        )
